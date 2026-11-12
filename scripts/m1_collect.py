"""
M1: collect raw Reddit data for a keyword via the Arctic Shift archive, save it,
and print a summary to eyeball whether the signal is there.

For each subreddit we page through its post listing by date and filter locally by keyword,
then pull comments of the most-discussed matches. Listing pages answer in under a second;
full-text search is heavily rate-limited by the archive (minutes per query when busy), and
capped at 100 results per query. Everything carries created_utc, so 新鲜度 is a real time-series.

Subreddits and pain-point queries are chosen automatically when not given:
an LLM proposes candidates, then each candidate is VERIFIED against Arctic Shift
(keyword matches in a sample of its recent posts, scaled to the window) and the top ones are kept.

  python m1_collect.py "earrings"                    # auto subs + auto queries
  python m1_collect.py "earrings" --plan-only        # just show what it would pick
  python m1_collect.py "earrings" --subs jewelry,piercing --queries tarnish,nickel   # manual override
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import time
from datetime import datetime, timedelta, timezone

from pathlib import Path

from arctic_client import ArcticClient

sys.stdout.reconfigure(line_buffering=True)  # show progress live even when piped to a log

from paths import DATA as DATA_DIR, CACHE
BOT_HINT = re.compile(r"\b(bot|automoderator)\b", re.I)
JUNK = {"[deleted]", "[removed]", ""}


TIMES, _T = [], [time.time()]


def lap(label: str):
    """Record time since the previous lap — the log then shows which stage was slow."""
    now = time.time()
    TIMES.append((label, now - _T[0]))
    _T[0] = now


def _fmt(sec: float) -> str:
    return f"{sec / 60:.1f}min" if sec >= 90 else f"{sec:.0f}s"


def name_matched_subs(client: ArcticClient, keyword: str, limit: int) -> list[str]:
    """Fallback discovery: subreddits whose NAME matches the keyword."""
    rows = client.get("subreddits/search", subreddit=keyword, limit=limit)
    return [r["display_name"] for r in rows if r.get("display_name")]


def propose(keyword: str) -> tuple[list[dict], list[str]]:
    """One LLM call: candidate subreddits (where BUYERS of this product talk) + pain queries."""
    from llm_client import chat_json, fast_model  # lazy: manual mode needs no LLM key
    sys_p = ("You help find where real BUYERS discuss a product on Reddit, for e-commerce product "
             "research. Suggest subreddits that actually exist and where people ask for "
             "recommendations, complain, or compare this product: the product's own community, the "
             "hobby/activity communities that use it, buying-advice communities (BuyItForLife, "
             "Frugal, etc.) and audience communities. Avoid pure meme/art/news subs. Use exact "
             "subreddit names without 'r/'. Also give short English search queries that surface "
             "buyer pain points and needs for this product (problems, materials, features). "
             "Reply ONLY JSON.")
    usr = (f"Product keyword: {keyword}\n"
           f'Return JSON {{"subs":[{{"name":"...","fit":1-5,"why":"中文一句"}}] (15 items, best first; '
           f'fit = how much BUYER talk about this product happens there: 5 = people ask which '
           f'one to buy / complain about it; 1 = makers, sellers, or only loosely related), '
           f'"queries":["..."] (8 items, each 1-3 words, include the keyword itself first), '
           f'"aliases":["..."] (0-2 other names buyers commonly use for the SAME product, '
           f'e.g. trinket dish -> ring dish, jewelry dish; [] if none)}}')
    out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                       model=fast_model(), max_tokens=1500, temperature=0)
    subs = [x for x in (out.get("subs") or []) if isinstance(x, dict) and x.get("name")]
    seen, uniq = set(), []
    for x in subs:  # LLM sometimes lists r/jewelry and r/Jewelry — subreddit names are case-insensitive
        x["name"] = x["name"].strip().removeprefix("r/").strip("/ ")
        try:
            x["fit"] = max(1, min(5, int(x.get("fit", 3))))
        except (TypeError, ValueError):
            x["fit"] = 3
        if x["name"].lower() not in seen:
            seen.add(x["name"].lower())
            uniq.append(x)
    subs = uniq
    queries = [q.strip() for q in (out.get("queries") or []) if isinstance(q, str) and q.strip()]
    aliases = [a.strip() for a in (out.get("aliases") or [])
               if isinstance(a, str) and a.strip() and a.strip().lower() != keyword.lower()][:2]
    return subs, queries, aliases


def auto_select(client: ArcticClient, keyword: str, after: str, n: int, min_hits: int,
                probe_max: int = 0):
    """LLM proposes, Arctic Shift verifies: count real keyword posts per candidate in the
    window, keep the top n with at least min_hits. Name-matched subs are added as candidates."""
    cands, queries, aliases = propose(keyword)
    queries += [a for a in aliases if a not in queries]
    have = {c["name"].lower() for c in cands}
    for name in name_matched_subs(client, keyword, limit=5):
        if name.lower() not in have:
            cands.append({"name": name, "fit": 3, "why": "名字含关键词", "named": True})
            have.add(name.lower())
    cands.sort(key=lambda c: -c["fit"])
    if probe_max:  # quick mode: fewer candidates; keep name-matched subs, they're often the core one
        named = [c for c in cands if c.get("named")][:2]
        cands = named + [c for c in cands if not c.get("named")][:probe_max - len(named)]
    print(f"  （帖量为最近 {SAMPLE_PAGES} 页帖子里的命中数，≈ = 按时间跨度推算到全窗口；相关度 1-5 由 LLM 判断，<3 不选；? = 未验证补位）")
    print(f"  LLM 候选 {len(cands)} 个社区，逐个抽样验证真实帖量（匹配词 {queries}，since {after}）…")
    match = matcher(queries + [keyword])
    full_days = (datetime.now(timezone.utc) - datetime.strptime(after, "%Y-%m-%d")
                 .replace(tzinfo=timezone.utc)).days
    rows, samples = [], {}
    for c in cands:
        c["err"], c["est"] = None, False
        if c["fit"] < 3:  # would never be picked — don't spend requests on it
            c["hits"], c["comments"], c["rank"], c["skip"] = 0, 0, 0.0, True
            rows.append(c)
            continue
        got, complete = sample_sub(client, c["name"], after)
        samples[c["name"]] = (got, complete)
        hits = [p for p in got if match(p)]
        if client.last_error and not got:
            c["hits"], c["comments"], c["err"] = 0, 0, client.last_error
        else:
            scale = 1.0
            if not complete and len(got) > 1:
                span = (got[0]["created_utc"] - got[-1]["created_utc"]) / 86400
                scale = max(1.0, full_days / max(span, 0.5))
            c["hits"] = round(len(hits) * scale)
            c["comments"] = round(sum(p.get("num_comments", 0) for p in hits) * scale)
            c["est"] = scale > 1
            c["thin"] = c["est"] and not hits  # busy sub: a small sample can miss a real topic
        # rank = buyer-fit × volume: a fit-5 sub with 20 posts beats a fit-2 sub with 100
        c["rank"] = c["fit"] * math.log1p(c["hits"])
        rows.append(c)
    rows.sort(key=lambda c: c["rank"], reverse=True)
    picked = [c for c in rows if c["hits"] >= min_hits and c["fit"] >= 3][:n]
    # don't lose a clearly relevant community to an archive error or a small sample —
    # fill remaining slots with unverified fit>=4 candidates
    for c in sorted([c for c in rows if (c["err"] or c.get("thin")) and c["fit"] >= 4], key=lambda c: -c["fit"]):
        if len(picked) >= n:
            break
        c["unverified"] = True
        picked.append(c)
    verified = [c for c in picked if not c.get("unverified")]
    if len(verified) < 3:
        print(f"\n  ⚠️ 只有 {len(verified)} 个社区有足量真实讨论：这个品类在 Reddit 上买家讨论稀少，"
              f"报告信号会偏弱（可考虑换个更通用的关键词，或用 --subs 手动补充）")
    failed = [c["name"] for c in rows if c["err"]]
    if failed:
        print(f"  ⚠️ {len(failed)} 个社区查询失败（Arctic Shift 超时/不存在），未计入: {', '.join(failed)}")
    print(f"\n  {'社区':<24}{'帖量':>6}{'评论':>7} 相关度 选中  理由")
    for c in rows:
        mark = "  ? " if c.get("unverified") else "  ✓ " if c in picked else "    "
        hits = ("跳过" if c.get("skip") else "失败" if c["err"] else ("≈" if c["est"] else "") + str(c["hits"]))
        print(f"  r/{c['name']:<22}{hits:>6}{c['comments']:>7}   {c['fit']}  {mark}  {c.get('why', '')}")
    return [c["name"] for c in picked], queries, samples


QUICK = {"subs": 4, "probe_max": 8, "comment_posts": 20, "pages": 60, "post_limit": 150}  # --quick
COMMENT_FLOOR, COMMENT_CAP = 3, 0.35  # per-subreddit slots for comment fetching
SAMPLE_PAGES = 5  # listing pages per candidate when verifying subreddits
MAX_PAGES = 200   # per subreddit; busier subs are sampled evenly across the window
RUN_PAGES = 5     # consecutive pages per sampling step
PAGE = 100
LIST_FIELDS = ("id", "title", "selftext", "score", "num_comments", "created_utc", "permalink")


def matcher(queries: list[str]):
    """True if a post contains every word of any query (plurals allowed), in title or body —
    the same AND semantics as a search box, run locally."""
    terms = []
    for q in queries:
        words = [w for w in re.split(r"[^a-z0-9]+", q.lower()) if w]
        if words:
            terms.append([re.compile(rf"\b{re.escape(w)}(?:s|es)?\b") for w in words])

    def match(p: dict) -> bool:
        text = f'{p.get("title") or ""} {p.get("selftext") or ""}'.lower()
        return any(all(r.search(text) for r in t) for t in terms)
    return match


def _page(client: ArcticClient, sub: str, after: str, before=None) -> list[dict]:
    rows = client.get("posts/search", cache=False, subreddit=sub, after=after, before=before,
                      limit=PAGE, sort="desc")
    return [{k: p.get(k) for k in LIST_FIELDS} for p in rows if p.get("created_utc")]


def sample_sub(client: ArcticClient, sub: str, after: str, pages: int = SAMPLE_PAGES):
    """The newest few listing pages. Returns (posts, complete), complete = window fully read."""
    rows, before = [], None
    for _ in range(pages):
        got = _page(client, sub, after, before)
        rows += got
        if len(got) < PAGE:
            return rows, not client.last_error
        before = got[-1]["created_utc"]
    return rows, False


def crawl_sub(client: ArcticClient, sub: str, after: str, max_pages: int, seed=None):
    """Page through a subreddit's window, newest first. If the window needs more than max_pages,
    read RUN_PAGES then jump, spreading pages evenly across it so 新鲜度 isn't skewed to recent
    posts. Returns (posts, sampled). Stored for 7 days so a rerun doesn't page again."""
    path = CACHE / "listing" / f"{sub.lower()}-{re.sub(r'[^0-9]', '', after)[:6]}-{max_pages}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 7 * 86400:
        saved = json.loads(path.read_text())
        return saved["posts"], saved["sampled"]
    rows, complete = seed or sample_sub(client, sub, after)
    sampled = False
    if rows and not complete:
        start = datetime.strptime(after, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        oldest = rows[-1]["created_utc"]
        per_page = max(1.0, (rows[0]["created_utc"] - oldest) / max(1, len(rows) / PAGE))
        used = len(rows) // PAGE
        factor = max(1.0, (oldest - start) / per_page / max(1, max_pages - used))
        sampled, done = factor > 1, False
        jump = int(RUN_PAGES * per_page * (factor - 1))
        before = oldest - jump  # jump first: the seed pages already stand for the newest stretch
        while before > start and used < max_pages and not done:
            for _ in range(RUN_PAGES):
                got = _page(client, sub, after, before)
                used += 1
                rows += got
                if len(got) < PAGE:  # end of window, or an archive error: keep what we have
                    done = True
                    break
                before = got[-1]["created_utc"]
            if sampled:
                before -= jump
    rows = list({p["id"]: p for p in rows if p.get("id")}.values())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"posts": rows, "sampled": sampled}, ensure_ascii=False))
    return rows, sampled


def pick_comment_posts(ranked: list[dict], n: int) -> list[dict]:
    """Share comment slots by each subreddit's share of the posts, so one talkative community
    can't set the sentiment of the whole report: floor COMMENT_FLOOR, cap COMMENT_CAP of n.
    `ranked` is most-discussed first; spare slots go to the next most-discussed posts."""
    by_sub: dict[str, list[dict]] = {}
    for p in ranked:
        by_sub.setdefault(p["subreddit"], []).append(p)
    cap = math.ceil(n * COMMENT_CAP)
    quota = {s: min(len(ps), cap, max(COMMENT_FLOOR, round(n * len(ps) / len(ranked))))
             for s, ps in by_sub.items()}
    while sum(quota.values()) > n and max(quota.values()) > COMMENT_FLOOR:  # rounding overshoot
        quota[max(quota, key=quota.get)] -= 1
    picked = [p for s, ps in by_sub.items() for p in ps[:quota[s]]]
    ids = {p["id"] for p in picked}
    spare = n - len(picked)
    for p in ranked:
        if spare <= 0:
            break
        if p["id"] not in ids and quota[p["subreddit"]] < cap:
            picked.append(p)
            ids.add(p["id"])
            quota[p["subreddit"]] += 1
            spare -= 1
    return picked[:n]


def fetch_comments_for_posts(client: ArcticClient, posts: list[dict],
                             n_posts: int, per_post: int) -> list[dict]:
    """comments/search has no full-text `query`; instead fetch comments OF the
    keyword-relevant posts by link_id. Prioritize posts with real discussion."""
    ranked = sorted(posts, key=lambda p: (p["num_comments"], p["score"]), reverse=True)
    out = []
    for p in pick_comment_posts(ranked, n_posts):
        rows = client.get("comments/search", link_id=p["id"],
                          limit=per_post, sort="desc")
        for c in rows:
            if _keep_comment(c):
                cc = _clean_comment(c, p["subreddit"])
                cc["post_id"] = p["id"]
                cc["post_title"] = p["title"]
                out.append(cc)
    return out


def _keep_post(p: dict) -> bool:
    return bool((p.get("title") or "").strip()) and p.get("selftext") not in ("[removed]", "[deleted]")


def _keep_comment(c: dict) -> bool:
    body = (c.get("body") or "").strip()
    if body in JUNK or len(body) < 15:
        return False
    if BOT_HINT.search(c.get("author") or ""):
        return False
    return True


def _clean_post(p: dict, sub: str) -> dict:
    return {
        "id": p.get("id"),
        "subreddit": sub,
        "title": (p.get("title") or "").strip(),
        "selftext": (p.get("selftext") or "").strip(),
        "score": p.get("score", 0),
        "num_comments": p.get("num_comments", 0),
        "created_utc": p.get("created_utc"),
        "permalink": p.get("permalink", ""),
    }


def _clean_comment(c: dict, sub: str) -> dict:
    return {
        "id": c.get("id"),
        "subreddit": sub,
        "body": (c.get("body") or "").strip(),
        "score": c.get("score", 0),
        "author": c.get("author", ""),
        "created_utc": c.get("created_utc"),
        "permalink": c.get("permalink", ""),
    }


def collect(keyword: str, subs: list[str], queries: list[str], days: int,
            post_limit: int, comment_posts: int, comment_limit: int, interval: float,
            n_subs: int = 7, min_hits: int = 3, plan_only: bool = False, replan: bool = False,
            quick: bool = False, max_pages: int = MAX_PAGES):
    client = ArcticClient(min_interval=interval, verbose=False)
    samples = {}
    after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")

    down = client.ping()
    if down:
        raise SystemExit(f"✗ 连不上 Reddit 存档 arctic-shift.photon-reddit.com：{down}。\n"
                         f"  请检查网络/DNS（如路由器或系统的内容过滤），放行该域名后重跑同一命令。")
    print(f"\n=== ① 目标社区（近 {days} 天，since {after}）===")
    plan_file = DATA_DIR.parent / "plans" / f"{re.sub(r'[^a-z0-9]+', '-', keyword.lower()).strip('-')}{'.quick' if quick else ''}.json"
    if not subs and not replan and plan_file.exists() and time.time() - plan_file.stat().st_mtime < 7 * 86400:
        plan = json.loads(plan_file.read_text())  # reuse the --plan-only result instead of re-asking
        subs, auto_queries = plan["subs"], plan["queries"]
        print(f"  复用 7 天内的选社区结果（{plan_file.name}；--replan 可重新选择）")
    elif not subs:
        subs, auto_queries, samples = auto_select(client, keyword, after, n_subs, min_hits,
                                                  QUICK["probe_max"] if quick else 0)
        if subs:
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(json.dumps({"subs": subs, "queries": auto_queries}, ensure_ascii=False))
    else:
        auto_queries = []
    lap("① 选社区")
    queries = queries or auto_queries
    if not subs:
        print("⚠️  没有找到够格的社区。用 --subs jewelry,piercing,... 手动指定。")
        return
    if not queries:
        queries = [keyword]
    print("\n  选定社区: " + ", ".join("r/" + s for s in subs))
    print(f"  查询词: {queries}")
    if plan_only:
        return

    print(f"\n=== ② 翻页抓帖子，本地按关键词筛选（每社区最多 {max_pages} 页、留 {post_limit} 帖）===")
    match = matcher(queries + [keyword])
    all_posts = []
    for s in subs:
        dedicated = keyword.lower().replace(" ", "") in s.lower()  # e.g. r/Earrings: every post counts
        t = time.time()
        raw, sampled = crawl_sub(client, s, after, max_pages, samples.get(s))
        hits = [p for p in raw if _keep_post(p) and (dedicated or match(p))]
        hits.sort(key=lambda p: (p.get("num_comments") or 0, p.get("score") or 0), reverse=True)
        all_posts += [_clean_post(p, s) for p in hits[:post_limit]]
        print(f"  r/{s:<22} 读 {len(raw):>6} 帖 → 命中 {len(hits):>4} → 留 {min(len(hits), post_limit):>4}"
              f"   {time.time() - t:.0f}s" + ("  (按时间均匀抽样)" if sampled else ""))
    all_posts = list({p["id"]: p for p in all_posts if p["id"]}.values())
    lap("② 抓帖子")

    print(f"\n=== ③ 抓评论（给讨论最多的前 {comment_posts} 帖，每帖 {comment_limit} 条）===")
    all_comments = fetch_comments_for_posts(client, all_posts, comment_posts, comment_limit)
    all_comments = list({c["id"]: c for c in all_comments if c["id"]}.values())
    print(f"  共 {len(all_comments)} 条评论")
    lap("③ 抓评论")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
    out = DATA_DIR / f"{slug}.json"
    payload = {
        "keyword": keyword,
        "collected_at": int(time.time()),
        "window_days": days,
        "quick": quick,
        "subreddits": subs,
        "posts": all_posts,
        "comments": all_comments,
        "stats": {"subreddits": len(subs), "posts": len(all_posts),
                  "comments": len(all_comments)},
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    _summary(payload, out)
    print("\n⏱ 抓取各步用时: " + " · ".join(f"{k} {_fmt(v)}" for k, v in TIMES))


def _summary(payload: dict, out: Path):
    posts, comments = payload["posts"], payload["comments"]
    print("\n" + "=" * 60)
    print(f"✅ 完成：{payload['stats']['subreddits']} 社区 · "
          f"{payload['stats']['posts']} 帖 · {payload['stats']['comments']} 评论")
    print(f"   原始数据：{out}")

    # date span — proof we have real timestamps for 新鲜度
    ts = [x["created_utc"] for x in posts + comments if x.get("created_utc")]
    if ts:
        lo = datetime.fromtimestamp(min(ts), timezone.utc).strftime("%Y-%m-%d")
        hi = datetime.fromtimestamp(max(ts), timezone.utc).strftime("%Y-%m-%d")
        print(f"   时间跨度：{lo} → {hi}（可做真时间序列新鲜度）")

    print("\n--- 抽样帖子标题（按分数）---")
    for p in sorted(posts, key=lambda x: x["score"], reverse=True)[:10]:
        print(f"  [{p['score']:>4}] r/{p['subreddit']} · {p['title'][:70]}")

    print("\n--- 抽样评论（痛点线索候选，按分数）---")
    for c in sorted(comments, key=lambda x: x["score"], reverse=True)[:8]:
        body = re.sub(r"\s+", " ", c["body"])[:120]
        print(f"  [{c['score']:>4}] r/{c['subreddit']} · {body}")

    print("\n👉 肉眼判断：标题/评论里能看出「细分方向」和「痛点」吗？")
    print("   能 → 数据可行，进 M2（LLM 抽取聚类+打分）。")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("--subs", default="", help="逗号分隔的社区名，如 jewelry,piercing")
    ap.add_argument("--queries", default="", help="逗号分隔的查询词，命中痛点，如 'tarnish,hypoallergenic'")
    ap.add_argument("--days", type=int, default=180, help="回溯天数")
    ap.add_argument("--post-limit", type=int, default=250, help="每社区保留多少条命中帖（讨论最多的优先）")
    ap.add_argument("--comment-posts", type=int, default=40, help="给前 N 个高讨论帖抓评论")
    ap.add_argument("--comment-limit", type=int, default=40, help="每帖抓多少条评论")
    ap.add_argument("--interval", type=float, default=1.2)
    ap.add_argument("--n-subs", type=int, default=7, help="自动选社区时保留几个")
    ap.add_argument("--min-hits", type=int, default=3, help="自动选社区时，至少多少条关键词帖才入选")
    ap.add_argument("--plan-only", action="store_true", help="只选社区/查询词并保存（会查询帖量，约几分钟），不抓取")
    ap.add_argument("--replan", action="store_true", help="忽略已保存的选社区结果，重新选择")
    ap.add_argument("--quick", action="store_true", help="快速模式：少社区、少查询词，样本更小")
    args = ap.parse_args()
    if args.quick:
        args.n_subs, args.comment_posts, args.post_limit = QUICK["subs"], QUICK["comment_posts"], QUICK["post_limit"]

    subs = [s.strip().lstrip("r/").strip() for s in args.subs.split(",") if s.strip()]
    queries = [q.strip() for q in args.queries.split(",") if q.strip()]
    collect(args.keyword, subs, queries, args.days, args.post_limit,
            args.comment_posts, args.comment_limit, args.interval,
            args.n_subs, args.min_hits, args.plan_only, args.replan, args.quick,
            QUICK["pages"] if args.quick else MAX_PAGES)
