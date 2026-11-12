"""
M2 (productionized): LLM-driven analysis of the collected corpus.

  A. cluster the corpus into 细分方向 (1 LLM call)              -> works for ANY keyword
  B. classify every item: direction + sentiment + pain (batched) -> real per-item sentiment
  C. aggregate metrics in code (mentions/heat/freshness/quotes)
  D. estimate saturation + write summary/takeaways (1 LLM call)
  E. write report_data.json  (build_report.py renders it)

Default provider DeepSeek. Run:
  export DEEPSEEK_API_KEY=...
  python m2_llm.py earrings
  python m2_llm.py earrings --reuse-labels   # skip A+B, reuse labels_<slug>.json
  python m2_llm.py earrings --keep-dirs      # new data, same directions as last run (comparable)
"""
from __future__ import annotations
import json, math, os, re, sys, time
from datetime import datetime, timezone
from pathlib import Path

from llm_client import chat_json, fast_model, smart_model
import supply_client

sys.stdout.reconfigure(line_buffering=True)  # show progress live even when piped to a log

from paths import DATA, REPORTS, slugify
ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]
REUSE = "--reuse-labels" in sys.argv
KEEP_DIRS = "--keep-dirs" in sys.argv  # re-classify new data but keep last run's directions
KW = ARGS[0] if ARGS else "earrings"
slug = slugify(KW)
_corpus = DATA / f"{slug}.json"
if not _corpus.exists():
    raise SystemExit(f"找不到 {_corpus}，先运行 m1_collect.py \"{KW}\"")
D = json.load(open(_corpus, encoding="utf-8"))

# off-topic items are filtered by the LLM classifier (dir="none"), not by a keyword blacklist

# build item list (posts + comments)
def comment_item(c):
    return {"text": c["body"][:260], "full": c["body"], "score": c["score"], "t": c["created_utc"],
            "sub": c["subreddit"], "link": c["permalink"], "kind": "comment", "pid": c.get("post_id"),
            "raw": re.sub(r"\s+", " ", c["body"]).strip(), "ctx": c.get("post_title", "")}


items = []
for p in D["posts"]:
    t = (p["title"] + " " + p.get("selftext", "")).strip()
    if t:
        items.append({"text": t[:260], "full": t, "score": p["score"], "t": p["created_utc"],
                      "sub": p["subreddit"], "link": p["permalink"], "kind": "post", "raw": p["title"],
                      "pid": p["id"], "title": p["title"], "nc": p.get("num_comments", 0)})
for c in D["comments"]:
    if c.get("body"):
        items.append(comment_item(c))
print(f"语料 {len(items)} 条")


# ---------- A. cluster ----------------------------------------------------
def cluster():
    top_posts = sorted([x for x in items if x["kind"] == "post"], key=lambda x: x["score"], reverse=True)[:55]
    top_comm = sorted([x for x in items if x["kind"] == "comment"], key=lambda x: x["score"], reverse=True)[:85]
    sample = "\n".join("- " + re.sub(r"\s+", " ", x["text"])[:150] for x in top_posts + top_comm)
    sys_p = (f"You are a product-selection (选品) analyst mining Reddit for e-commerce opportunities "
             f"for the keyword '{KW}'. Cluster the discussion into 5-7 DISTINCT sub-directions that "
             f"each map to a PRODUCT A SELLER COULD ACTUALLY LIST AND SELL — a material, feature, "
             f"use-case, style, or buyer-segment product need. Prioritize directions with clear "
             f"buyer PAIN POINTS or unmet needs.\n"
             f"STRICT EXCLUSIONS — do NOT create directions for these (they are not sellable products):\n"
             f"- piercing aftercare / healing procedures (saline, healing time, infection care, "
             f"'is this healed') — that is service/advice, not a product;\n"
             f"- pure show-and-tell / appreciation (people showing off collections, antiques, "
             f"'look what I found', vintage admiration) — that is content, not a product need.\n"
             f"Collapse any style/design appreciation into AT MOST ONE direction, and only if it "
             f"reflects a real buying preference.\n"
             f"CRITICAL — the directions must be MUTUALLY DISTINCT and non-overlapping. MERGE "
             f"synonyms into ONE direction (e.g. 'hypoallergenic', 'metal allergy', 'sensitive "
             f"ears', 'nickel-free' are all THE SAME direction — never split them). Cover DIFFERENT "
             f"axes — e.g. (a) hypoallergenic/sensitive material, (b) durability: tarnish / plating "
             f"wearing off / solid vs plated, (c) all-day comfort & wearability (flat-back, weight, "
             f"secure backs), (d) non-pierced options (clip-on), (e) a style/quality tier — not "
             f"several flavors of the same axis. Reply ONLY JSON.")
    usr = (f"Sample discussion about '{KW}':\n{sample}\n\n"
           f"Return JSON: {{\"directions\":[{{\"id\":\"short_snake\",\"cn\":\"中文简称\","
           f"\"en\":\"english tag\",\"desc\":\"what product need it covers + which words/themes signal it\"}}], "
           f"\"exclude\":\"one line: what off-topic content to ignore\"}}")
    out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                       model=fast_model(), max_tokens=1800, temperature=0)
    dirs = (out if isinstance(out, list) else out.get("directions") or [])[:8]
    print(f"A. 聚出 {len(dirs)} 个方向: " + ", ".join(d["cn"] for d in dirs))
    return dirs


# ---------- B. classify every item ---------------------------------------
CONCURRENCY = max(1, int(os.environ.get("RPR_CONCURRENCY", "8")))  # parallel LLM calls
TIMES = []  # (stage, seconds) — printed at the end so slow stages are visible in the log


def timed(label, fn, *args):
    t = time.time()
    try:
        return fn(*args)
    finally:
        TIMES.append((label, time.time() - t))


def pmap(fn, xs, workers=None):
    """Run fn over xs in parallel threads, keep input order. LLM / HTTP calls are I/O-bound."""
    from concurrent.futures import ThreadPoolExecutor
    xs = list(xs)
    if not xs:
        return []
    with ThreadPoolExecutor(max_workers=min(workers or CONCURRENCY, len(xs))) as ex:
        return list(ex.map(fn, xs))


LABEL_FULL, LABEL_MAX, LABEL_HEAD = 1000, 1500, 300
# the keyword's last word is the product noun: "harness" in "dog harness". Matching "dog" as well
# would land on every sentence in a dog subreddit.
CORE = re.compile(rf"\b{re.escape(re.sub(r'(?<!s)s$', '', re.split(r'[^a-z0-9]+', KW.lower().strip())[-1]))}", re.I)


def clip(x):
    """What the labeller reads. Short items whole; long ones as the opening plus every sentence
    naming the product with one sentence either side, since a verdict can sit anywhere in a post."""
    t = re.sub(r"\s+", " ", x.get("full") or x["text"]).strip()
    if len(t) <= LABEL_FULL:
        return t
    sents = re.split(r"(?<=[.!?])\s+", t)
    keep = sorted({k for n, st in enumerate(sents) if CORE.search(st)
                   for k in range(max(0, n - 1), min(len(sents), n + 2))})
    out, last, room = [t[:LABEL_HEAD]], -1, LABEL_MAX - LABEL_HEAD
    pos = 0
    for n, st in enumerate(sents):
        if n in keep and pos + len(st) > LABEL_HEAD and room > 0:
            piece = st[:room]
            out.append(("" if n == last + 1 else "… ") + piece)
            room -= len(piece) + 2
            last = n
        pos += len(st) + 1
    return " ".join(out)


def proven(x, quote):
    """The quote must really be in the item: an opinion the model can't point to doesn't count.
    Whether the quote is about the product is left to the model. Requiring the product word near
    the quote demoted ~20% of earring opinions written as "these", "studs" or a brand name."""
    norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
    q = norm(str(quote or ""))
    return bool(q) and q in norm(x.get("full") or x["text"])


def classify(dirs, checkpoint=None, start=0):
    """Label every item in batches, CONCURRENCY batches at a time. Progress is saved to
    `checkpoint` after each batch, so a crash (network drop, quota) resumes instead of starting
    over: a rerun with the same corpus picks up the saved directions and finished batches."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import threading
    dir_desc = "\n".join(f'{d["id"]}: {d["cn"]} — {d["desc"]}' for d in dirs)
    BATCH = 25
    starts = list(range(start, len(items), BATCH))
    total = len(starts)
    state = {"n_items": len(items), "dirs": dirs, "labels": {}, "done": []}
    if checkpoint and checkpoint.exists():
        state = json.loads(checkpoint.read_text(encoding="utf-8"))
    labels = {int(k): v for k, v in state["labels"].items()}
    done = set(state.get("done", []))
    todo = [b for b in starts if b not in done]
    if done:
        print(f"  B. 从断点继续：已完成 {len(done)}/{total} 批")
    lock = threading.Lock()
    sys_p = (f"You label Reddit items for a product-selection tool about '{KW}'. Work in two steps.\n"
             f"STEP 1 — about: true only if the item actually discusses {KW} as a product: recommends or "
             f"asks which one to buy, reports how one fits, works, holds up or fails, or compares them. "
             f"false if it is mainly about the person's situation or a problem with behaviour, training, "
             f"health or care, or if the product is only named in passing while the point lies elsewhere.\n"
             f"STEP 2 — only if about is true: pick the best direction id (or \"none\" if no direction "
             f"fits); sent is the attitude TOWARD THE PRODUCT, never the writer's mood. pos = praises or "
             f"recommends it. neg = says a product failed, disappoints or is badly made. neu = asks a "
             f"question, states facts, or is mixed. pain = true if it voices a complaint or unmet need "
             f"about the product. For pos or neg, quote: copy up to 15 consecutive words, exactly as "
             f"written, that show the attitude toward the product.\n"
             f"Rules: only other products and no {KW}: about=false (a comparison with {KW} counts). Good "
             f"and bad in one item, even about different brands: neu. A {KW} that broke, tore or failed: "
             f"neg, even if the writer doesn't blame it. Saying it is better to do without {KW} or to "
             f"switch to another kind of product: neg.\n"
             f"A stressful situation is not a negative opinion of a product. Someone struggling and asking "
             f"what to buy is about=true, sent=neu, pain=true. Someone venting with no opinion on any "
             f"product is about=false.\n"
             f"If about is false: dir=\"none\", sent=\"neu\", pain=false. Reply ONLY JSON.")

    def one(bi):
        batch = items[bi:bi + BATCH]
        lst = "\n".join(f'{j}: {"[thread: " + x["ctx"] + "] " if x.get("ctx") else ""}{clip(x)}'
                        for j, x in enumerate(batch))
        usr = (f"Directions:\n{dir_desc}\n\nItems:\n{lst}\n\n"
               f'Return JSON {{"items":[{{"i":<index>,"about":true|false,"dir":"<id|none>","sent":"pos|neu|neg","pain":true|false,"quote":"<words or empty>"}}]}}')
        out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                           model=fast_model(), max_tokens=4000)
        rows = out if isinstance(out, list) else out.get("items", [])
        got = {bi + r["i"]: r for r in rows if isinstance(r.get("i"), int) and 0 <= r["i"] < len(batch)}
        for k, r in got.items():
            r["_i"] = k
        for r in got.values():
            if r.get("about") is False:  # not a product opinion: must not feed any direction's metrics
                r.update(dir="none", sent="neu", pain=False)
            elif r.get("sent") in ("pos", "neg") and not proven(items[r["_i"]], r.get("quote")):
                r.update(sent="neu", demoted=True)  # no checkable evidence: don't count the opinion
        return bi, got

    failed = 0
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as ex:
        futs = {ex.submit(one, bi): bi for bi in todo}
        for f in as_completed(futs):
            bi = futs[f]
            try:
                _, got = f.result()
            except Exception as e:  # one bad batch shouldn't throw away a long crawl
                failed += 1
                print(f"\n  ⚠️ 第 {bi // BATCH + 1} 批标注失败，先跳过: {type(e).__name__}: {e}")
                if failed > max(2, total // 5):
                    raise SystemExit(f"标注失败批次过多（{failed}/{total}），可能是 API 不可用。"
                                     f"已完成的批次已保存，稍后重跑同一命令会从断点继续。")
                continue
            with lock:
                labels.update(got)
                done.add(bi)
                if checkpoint:
                    checkpoint.write_text(json.dumps(
                        {"n_items": len(items), "dirs": dirs, "labels": labels, "done": sorted(done)},
                        ensure_ascii=False), encoding="utf-8")
                print(f"  B. 分类批次 {len(done)}/{total}", end="\r", flush=True)
    n_dem = sum(1 for r in labels.values() if r.get("demoted"))
    print(f"\nB. 已分类 {len(labels)}/{len(items)}" + (f"（{failed} 批失败已跳过）" if failed else "")
          + (f"，{n_dem} 条因引文核对不过改为中性" if n_dem else ""))
    return labels


# ---------- B2. top up thin directions -------------------------------------
TOPUP_POSTS = 15  # per thin direction: more threads, not more comments per thread
TOPUP_SECONDS = 180  # the archive slows repeated comment requests (9-19 s each): cap the wait


def top_up(dirs, labels):
    """A thread's comments stay on that thread's topic, so a small direction grows only when we
    read MORE threads about it (yoga mat: 40 → 150 threads lifted 3 of 4 thin directions past 30;
    100 instead of 40 comments per thread lifted none). Fetch comments of up to TOPUP_POSTS
    unread threads already labelled with each thin direction, then label just those comments."""
    from collections import Counter
    from arctic_client import ArcticClient
    from m1_collect import _keep_comment, _clean_comment
    n = Counter(r.get("dir") for r in labels.values())
    thin = [d for d in dirs if 0 < n[d["id"]] < THIN]
    if not thin:
        return labels
    read = {c.get("post_id") for c in D["comments"]}
    posts, picked = {}, set()
    for d in thin:
        cand = sorted((items[i] for i, r in labels.items() if r.get("dir") == d["id"]
                       and items[i]["kind"] == "post" and items[i]["nc"] >= 3
                       and items[i]["pid"] not in read | picked),
                      key=lambda x: x["nc"], reverse=True)[:TOPUP_POSTS]
        posts[d["id"]] = cand
        picked |= {x["pid"] for x in cand}
    # round-robin over directions, busiest threads first, so a time cap still helps each one
    todo = [c[k] for k in range(TOPUP_POSTS) for c in posts.values() if k < len(c)]
    print(f"B2. {len(thin)} 个方向不足 {THIN} 条（" + "、".join(f'{d["cn"]} {n[d["id"]]}' for d in thin)
          + f"）→ 补抓 {len(todo)} 个帖子的评论")
    if not todo:
        return labels
    client = ArcticClient(verbose=False)
    new, t0, done = [], time.time(), 0
    for x in todo:
        if time.time() - t0 > TOPUP_SECONDS:
            print(f"B2. 补抓已用 {TOPUP_SECONDS // 60} 分钟上限，停在 {done}/{len(todo)} 个帖子")
            break
        done += 1
        for c in client.get("comments/search", link_id=x["pid"], limit=40, sort="desc"):
            if _keep_comment(c):
                cc = _clean_comment(c, x["sub"])
                cc["post_id"], cc["post_title"] = x["pid"], x["title"]
                new.append(cc)
    if not new:
        print("B2. 没有补到评论（可能连不上存档），按原样继续")
        return labels
    start = len(items)
    items.extend(comment_item(c) for c in new)
    D["comments"].extend(new)  # saved, so --reuse-labels still lines up with the corpus
    _corpus.write_text(json.dumps(D, ensure_ascii=False), encoding="utf-8")
    labels.update(classify(dirs, start=start))
    m = Counter(r.get("dir") for r in labels.values())
    print("B2. 补抓后: " + "、".join(f'{d["cn"]} {n[d["id"]]}→{m[d["id"]]}' for d in thin))
    return labels


# ---------- C. aggregate --------------------------------------------------
CUTOFF = time.time() - 90 * 86400
ASK = re.compile(r"where (can|do|to)|anyone (know|tried|use|have|recommend)|looking for|"
                 r"any (recommend|suggestion|advice)|recommendation|suggestions|"
                 r"which .{0,30}should i|help me (find|choose|pick)|where'?d you", re.I)

def aggregate(dirs, labels):
    for i, r in labels.items():
        items[i]["dir"] = r.get("dir", "none")
        items[i]["sent"] = r.get("sent", "neu")
        items[i]["pain"] = bool(r.get("pain"))
    assigned = [x for x in items if x.get("dir") and x["dir"] != "none"]
    base_recent = (sum(1 for x in assigned if x["t"] and x["t"] >= CUTOFF) / len(assigned)) if assigned else 0

    rows = []
    for d in dirs:
        m = [x for x in assigned if x["dir"] == d["id"]]
        if not m:
            continue
        upv = sum(max(0, x["score"]) for x in m)
        heat_raw = len(m) + 6 * math.sqrt(upv)
        rec = sum(1 for x in m if x["t"] and x["t"] >= CUTOFF) / len(m)
        fresh = max(0, min(100, round(50 * (rec / base_recent if base_recent else 1))))
        pos = sum(1 for x in m if x["sent"] == "pos")
        neg = sum(1 for x in m if x["sent"] == "neg")
        neu = len(m) - pos - neg
        split = [round(100 * pos / len(m)), round(100 * neu / len(m)), round(100 * neg / len(m))]
        score = split[0] - split[2]
        sent = "pos" if score > 12 else "neg" if score < -12 else "mix"
        asks = sum(1 for x in m if ASK.search(x["text"]))  # hard signal: "求推荐" count
        # quote candidates: pain comments first, then by score; pick_quotes() lets the LLM
        # choose the on-topic one (score alone surfaced off-topic quotes, e.g. HOKA shoes)
        cand = sorted([x for x in m if x["kind"] == "comment" and 25 <= len(x["raw"]) <= 240],
                      key=lambda x: (x["pain"], x["score"]), reverse=True)[:6]
        rows.append({**d, "mentions": len(m), "upv": upv, "heat_raw": round(heat_raw),
                     "fresh": fresh, "split": split, "score": score, "sent": sent,
                     "asks": asks, "neg_pct": split[2], "cands": cand,
                     "samples": [re.sub(r"\s+", " ", x["raw"])[:160] for x in m[:6]]})
    mx = max(r["heat_raw"] for r in rows)
    for r in rows:
        r["heat"] = round(95 * r["heat_raw"] / mx)
    rows.sort(key=lambda r: r["heat"], reverse=True)
    return rows, assigned


# ---------- D. saturation (median of 3, calibrated) + narrative --------------
SAT_RUNS = 3
CALIBRATION = (
    "SATURATION RUBRIC (higher = better served):\n"
    "  0-30  many unanswered 'where can I find / which should I buy' asks, frequent complaints, "
    "no brand people trust;\n"
    "  30-50 a few brands named but asks keep coming or complaints are common;\n"
    "  50-70 a few consensus brands answer most asks, complaints are minor;\n"
    "  70-100 clear consensus picks, high satisfaction, little unmet need.\n"
    "CALIBRATION ANCHORS from reviewed reports:\n"
    "  earrings / hypoallergenic material: ~34 asks, only 1 brand named, many allergy complaints "
    "-> sat 35;\n"
    "  yoga mat / durability: Manduka & Liforme repeatedly recommended as 'buy once', few asks, "
    "low negative -> sat 60;\n"
    "  trinket dish / handmade custom: mostly show-and-tell praise, no unmet asks -> sat 85.\n"
    "COMMERCIAL: clear material/feature/style a seller can list = 70-95; accessory or "
    "behaviour-adjacent (apps, reminders, routines) = 40-60; aftercare/service/advice = <30.")

def _score_once(rows, temperature):
    brief = "\n".join(
        f'{r["id"]} ({r["cn"]}): 提及{r["mentions"]} 热度{r["heat"]} 负面{r["neg_pct"]}% '
        f'求推荐帖约{r["asks"]}条; 样例: {" | ".join(r["samples"][:4])}' for r in rows)
    sys_p = ("For each product sub-direction, estimate SATURATION and COMMERCIAL (0-100) using the "
             "rubric and anchors below, and justify with evidence a human can verify.\n"
             + CALIBRATION +
             "\nFor EACH direction also return 'brands' (brand/shop names actually present in its "
             "samples, [] if none — never invent) and 'evidence' (ONE short Chinese sentence citing "
             "the concrete signals: brands named, whether 求推荐 got answered, negative ratio). "
             "Reply ONLY JSON.")
    usr = (f"Keyword: {KW}\nDirections (with hard signals):\n{brief}\n\n"
           f'Return JSON: {{"directions":[{{"id":..,"sat":0-100,"commercial":0-100,'
           f'"brands":["..."],"evidence":"中文一句证据"}}]}}')
    out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                       model=fast_model(), max_tokens=1500, temperature=temperature)
    res = {}
    for d in out.get("directions") or []:
        if "id" not in d:
            continue
        try:
            res[d["id"]] = {"sat": max(0, min(100, int(d.get("sat", 50)))),
                            "commercial": max(0, min(100, int(d.get("commercial", 60)))),
                            "brands": [b for b in (d.get("brands") or []) if isinstance(b, str)][:6],
                            "evidence": d.get("evidence", "")}
        except (TypeError, ValueError):
            continue
    return res

def saturate(rows):
    """Judgment metrics are the volatile part of the report, so: calibrated rubric + anchors,
    SAT_RUNS independent samples, take the MEDIAN, and keep the spread so the report can
    flag directions whose zone hangs on a coin-flip."""
    import statistics
    runs = pmap(lambda _: _score_once(rows, 0.7), range(SAT_RUNS))  # independent samples → parallel
    out = {}
    for r in rows:
        got = [x[r["id"]] for x in runs if r["id"] in x]
        if not got:
            out[r["id"]] = {"sat": 50, "commercial": 60, "brands": [], "evidence": "", "spread": None}
            continue
        sats = [g["sat"] for g in got]
        med = int(statistics.median(sats))
        rep = min(got, key=lambda g: abs(g["sat"] - med))  # evidence from the run closest to median
        out[r["id"]] = {"sat": med, "commercial": int(statistics.median(g["commercial"] for g in got)),
                        "brands": rep["brands"], "evidence": rep["evidence"],
                        "sat_runs": sats, "spread": max(sats) - min(sats)}
    print("D. 饱和度(3次取中位): " + " · ".join(
        f'{r["cn"]} {out[r["id"]].get("sat_runs")}→{out[r["id"]]["sat"]}' for r in rows))
    return out

# final sat = W × Amazon measured shelf + (1-W) × Reddit judgment. Amazon is hard data, Reddit
# judgment is stable but unvalidated → Amazon leads. Override: --supply-weight 0.6
SUPPLY_WEIGHT = next((float(a.split("=", 1)[1]) for a in sys.argv if a.startswith("--supply-weight=")), 0.7)
# Reddit judgment runs systematically low in mass categories (measured: Amazon 32-44 pts higher
# for yoga mat / water bottle), so a fixed gap flags everything. Report the category-wide
# offset once, and flag only directions that deviate from that offset by ≥ CONFLICT_GAP.
CONFLICT_GAP = 25
THIN = 30  # below this many mentions a sentiment percentage is noise: show "样本少" instead
# (at 30 a share is still ±18 pts; 30 is a rule of thumb, not fitted). A thin direction also gets
# no zone: heat is relative to the category, so in a cold category 1 mention can score 80+.
LOW_SAMPLE = 100  # fewer analyzed items than this in the whole report: no product verdict at all
OFFSET_BANNER = 20

def amazon_supply(rows):
    """D3. For each direction: one shopper-style Amazon query (LLM) → one search page
    (Scrape.do, cached 7 days) → measured shelf saturation 0-100. {} if no token."""
    if not supply_client.available():
        print("D3. 未设置 SCRAPEDO_TOKEN，跳过 Amazon 供给侧（饱和度仅用 Reddit 判读）")
        return {}
    # the query decides most of saturation ("gold plated earrings" 1 dedicated vs "tarnish resistant
    # earrings" 8), so reuse the saved one while a direction is unchanged: reruns stay comparable
    qfile = REPORTS / f"amazon_queries_{slug}.json"
    saved = json.loads(qfile.read_text(encoding="utf-8")) if qfile.exists() else {}
    key = lambda r: f'{r["id"]}|{r["en"]}'
    queries = {r["id"]: saved[key(r)] for r in rows if key(r) in saved}
    new = [r for r in rows if r["id"] not in queries]
    brief = "\n".join(f'{r["id"]}: {r["cn"]} / {r["en"]} — {r["desc"]}' for r in new)
    sys_p = (f"For each product sub-direction of '{KW}', write ONE Amazon US search query a "
             f"shopper would type to find '{KW}' products with that attribute: 2-4 English words, "
             f"must contain the product noun itself (search for the {KW}, not accessories, sprays "
             f"or cases), no brand names, no long descriptive phrases. Reply ONLY JSON.")
    usr = f'Directions:\n{brief}\n\nReturn JSON {{"queries":{{"<id>":"<query>"}}}}'
    if new:
        out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                           model=fast_model(), max_tokens=600, temperature=0)
        queries.update({r["id"]: out["queries"][r["id"]] for r in new
                        if (out.get("queries") or {}).get(r["id"])})
        saved.update({key(r): queries[r["id"]] for r in new if r["id"] in queries})
        qfile.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    def fetch(r):
        q = (queries.get(r["id"]) or f'{r["en"]} {KW}').strip()
        data = supply_client.amazon_search(q)
        return r["id"], q, (supply_client.organic(data) if data else [])
    # 4 at a time: Scrape.do free plans cap concurrent requests
    pages = {rid: (q, org) for rid, q, org in pmap(fetch, rows, workers=4) if org}
    if not pages:
        return {}
    # per direction: LLM must QUOTE the title phrase that makes a product dedicated; code then
    # checks the quote really is in that title. Unverifiable claims don't count (an unchecked
    # LLM judged 10/10 'easy clean' bottles whose titles never mention cleaning).
    def judge(r):
        org = pages[r["id"]][1]
        lst = "\n".join(f'{i}: {o["title"][:160]}' for i, o in enumerate(org))
        sys_p = (f"Amazon results for the '{KW}' sub-direction 「{r['cn']}」 ({r['en']}: {r['desc']}). "
                 f"The shopper searched '{pages[r['id']][0]}'. "
                 f"Check EVERY product. It is DEDICATED if its TITLE explicitly advertises ANY core "
                 f"attribute of this sub-direction (one is enough — e.g. for 'non-slip & sweat-absorbing' "
                 f"a title saying 'Non Slip' qualifies; synonyms and concrete forms count: 'Dry-Grip' = "
                 f"non-slip, 'dishwasher safe' = easy clean, 'Glass' / 'Stainless Steel' = plastic-free "
                 f"material, any attribute word from the search query counts). Quote the exact words "
                 f"from the title as proof. If the title does not mention the attribute, it is NOT "
                 f"dedicated, however popular. Reply ONLY JSON.")
        usr = (f'{lst}\n\nReturn JSON {{"items":[{{"i":<index>,"dedicated":true|false,'
               f'"quote":"<exact words copied from that title, or empty>"}}]}} — one entry per product.')
        out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                           model=fast_model(), max_tokens=1000, temperature=0)
        norm = lambda t: re.sub(r"[^a-z0-9]+", " ", t.lower()).strip()
        ok = {}
        for x in out.get("items") or out.get("dedicated") or []:
            if x.get("dedicated") is False:
                continue
            i, q = x.get("i"), str(x.get("quote") or "")
            if isinstance(i, int) and 0 <= i < len(org) and norm(q) and norm(q) in norm(org[i]["title"]):
                ok[i] = q
        # backstop: the query was written to name this direction's attribute ("glass water bottle"),
        # so a title carrying that attribute phrase is dedicated even if the LLM read it too literally
        attr = norm(re.sub(re.escape(KW), " ", pages[r["id"]][0], flags=re.I))
        if attr:
            for i, o in enumerate(org):
                if i not in ok and f" {attr} " in f" {norm(o['title'])} ":
                    ok[i] = attr
        return r["id"], ok
    ded = dict(pmap(judge, [r for r in rows if r["id"] in pages]))
    res = {}
    for r in rows:
        if r["id"] not in pages:
            continue
        q, org = pages[r["id"]]
        proofs = ded.get(r["id"], {})
        m = supply_client.supply_metrics(org, set(proofs))
        if m:
            m["proofs"] = [{"title": org[i]["title"][:90], "quote": q, "reviews": org[i]["reviews"]}
                           for i, q in sorted(proofs.items())][:4]
        if m:
            res[r["id"]] = {"query": q, "url": supply_client.search_url(q),
                            "score": supply_client.supply_score(m), **m}
    print("D3. Amazon 供给分: " + " · ".join(
        f'{r["cn"]}「{res[r["id"]]["query"]}」专门{res[r["id"]]["dedicated"]}/{res[r["id"]]["n"]}→{res[r["id"]]["score"]}'
        if r["id"] in res
        else f'{r["cn"]} 无数据' for r in rows))
    return res

def pick_highlights(directions):
    """Takeaway SUBJECTS are chosen in code from the final zones, so the headline can never
    contradict the quadrant. The LLM only writes the wording (narrate)."""
    by_heat = sorted(directions, key=lambda d: d["heat"], reverse=True)
    op = [d for d in by_heat if d["zone"] == "op"]
    blue = [d for d in by_heat if d["zone"] == "blue"]
    # no 机会区 → best 蓝海 is the headline (never promote a 红海/长尾 direction to "opportunity");
    # too little data overall → no verdict, whatever the zones say
    low = sum(d["mentions"] for d in directions) < LOW_SAMPLE
    solid = [d for d in by_heat if d["zone"] != "thin"] or by_heat
    top = solid[0] if low else op[0] if op else (blue[0] if blue else solid[0])
    top["_headline"] = "low" if low else "op" if op else ("blue" if blue else "none")
    # strongest pain = most negative MENTIONS (count), not the highest % on a tiny sample;
    # skip the headline so the three cards name three different directions
    others = ([d for d in directions if d["id"] != top["id"] and d["sent"] != "thin"]
              or [d for d in directions if d["id"] != top["id"]] or directions)
    pain = max(others, key=lambda d: (d["mentions"] * d["split"][2], -d["score"]))
    rise = [d for d in by_heat if d["zone"] == "blue" and d["id"] not in (top["id"], pain["id"])
            and top.get("_headline") != "low"]
    rise = max(rise, key=lambda d: (100 - d["sat"]) + d["fresh"]) if rise else None
    return top, pain, rise

def narrate(directions, top, pain, rise, banner=None):
    zl = {"op": "机会区", "red": "红海", "blue": "蓝海苗头", "tail": "长尾", "thin": "样本少"}
    brief = "\n".join((f'{d["cn"]}: 小众需求（提及{d["mentions"]}，讨论少，不分区）'
                       if d["mentions"] < THIN else
                       f'{d["cn"]}: 区域={zl[d["zone"]]} 热度{d["heat"]} 饱和度{d["sat"]} '
                       f'负面{d["split"][2]}% 提及{d["mentions"]}; 依据: {d["evidence"]}'
                       + (f'; {d["conflict_text"]}' if d.get("conflict") else ""))
                      for d in directions)
    lead = {"op": "头号机会", "blue": "最值得切入的方向",
            "none": "无明显机会，相对最值得关注",
            "low": "样本不足，不作选品判断；讨论最多"}[top.get("_headline", "op")]
    pain_s = "" if pain["sent"] == "thin" else f'（负面 {pain["split"][2]}%）'
    top_s = "" if top["zone"] == "thin" else f'（{zl[top["zone"]]}）'
    picks = (f'{lead} = {top["cn"]}{top_s}; 最强痛点 = {pain["cn"]}{pain_s}; '
             f'蓝海苗头 = {rise["cn"] if rise else "无"}')
    sys_p = ("You write the summary of a product-selection report. The zones and the three "
             "highlighted directions are FINAL — do not contradict or re-rank them; name each "
             "highlighted direction exactly as given with exactly its role label (if it says "
             "无明显机会 or 样本不足, never call anything 头号机会 or 机会; if 样本不足, say first that "
             "the data is too thin for a product decision). Never give a percentage for a direction whose "
             "is a 小众需求 (call it 小众需求 if you mention it at all), and don't copy labels or notes from the input in brackets. Chinese, concise, seller-oriented. Reply ONLY JSON.")
    usr = (f"Keyword: {KW}\nDirections (final):\n{brief}\n\nHighlights (final): {picks}\n"
           + (f"Category-wide supply note (mention it in the summary): {banner}\n" if banner else "") +
           f"\n"
           f'Return JSON: {{"summary_html":"<中文3句，可用<span class=\\"hi\\">重点</span>和'
           f'<span class=\\"neg-hi\\">痛点</span>和<b>加粗</b>>", '
           f'"top_p":"一句：为什么{top["cn"]}是「{lead}」、怎么切入", '
           f'"pain_p":"一句：{pain["cn"]}的痛点是什么、能做什么产品", '
           f'"rise_p":"一句：蓝海苗头的机会（无则写空字符串）"}}')
    out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                       model=fast_model(), max_tokens=900, temperature=0)
    tag = {"op": "▲ 头号机会", "blue": "▲ 最值得切入 · 本品类无机会区",
           "none": "▲ 暂无明显机会", "low": "⚠ 样本不足，不作选品判断"}[top.pop("_headline")]
    takeaways = [{"tag": tag, "cls": "t-op", "big": top["cn"], "p": out.get("top_p", "")},
                 {"tag": "◆ 最强痛点", "cls": "t-pain", "big": pain["cn"],
                  "p": (f'仅 {pain["mentions"]} 条提及，比例不可靠。' if pain["sent"] == "thin"
                        else f'负面 {pain["split"][2]}%。') + out.get("pain_p", "")}]
    if rise:
        takeaways.append({"tag": "↗ 蓝海苗头", "cls": "t-rise", "big": rise["cn"],
                          "p": out.get("rise_p", "")})
    return out.get("summary_html", ""), takeaways


# ---------- cloud + painpoints (code) ------------------------------------
STOP = set(("a an the and or but of to in on for with my your our their you i it is are was were be been being this that these those at as by from just so very too much "
            "more most some any all not no can cant dont im ive get got have has had do does did will would should could them they he she we me us if then than about into "
            "out up down over under again once here there when where why how what which who whom also like one two thing things something someone anyone people time going go "
            "getting really know think want need make made way ways good bad new old lot lots pretty though because could would still find only since ago few off take put "
            "see use used using well before after right left both same each other others any every actual actually said say says even ever never always sometimes maybe "
            "much many little bit around back day days month months year years now right thing im you're they're it's don't didn't doesn't your you our we're etc yeah yes "
            "no ok okay lol nap thanks thank hi hey oh well quite rather bit really super pretty kinda gonna wanna").split())

# generic filler that adds noise, not signal (works across keywords)
CLOUD_DROP = set(("love great good best better nice looking look feel doing keep bought using "
                  "want need thing really pretty little long last mine home work works classes "
                  "practice everyone people first thanks help question years months recommend "
                  "actually probably definitely honestly literally i've i'm they're it's you're "
                  "while through times times year month week day days weeks something someone "
                  "anything everything much many able around going come came went able "
                  "sure post idea ideas cool share cute nice beautiful gorgeous lovely amazing "
                  "made make making done well yeah wow congrats congratulations").split())

WORD_KINDS = ("brand", "pain", "feat")

def wordcloud(assigned, known_brands):
    """Frequency-sized cloud over the ANALYZED items, colored by WORD TYPE (not sentiment):
    brand / pain word / product feature. A cheap rule pass removes obvious junk, then one
    LLM call labels the top candidates and drops remaining filler (without, might, ...).
    Brands named in the saturation step are forced to 'brand' as a hard signal."""
    from collections import Counter
    kw_toks = set(re.findall(r"[a-z]{3,}", KW.lower()))
    kw_toks |= {t + "s" for t in list(kw_toks)}

    freq = Counter()
    for x in assigned:
        for w in set(re.findall(r"[a-z][a-z'-]{3,}", x["text"].lower())):
            w = w.strip("'-")
            if ("'" in w or w.startswith(("http", "www")) or len(w) < 4 or w in STOP
                    or w in kw_toks or w in CLOUD_DROP):
                continue
            freq[w] += 1
    for w in [w for w in freq if w.endswith("s") and w[:-1] in freq]:  # rings -> ring
        freq[w[:-1]] += freq.pop(w)
    cands = [w for w, n in freq.most_common(70) if n >= 2]
    if not cands:
        return []

    sys_p = (f"You label words for a product-research word cloud about '{KW}'. For each word pick: "
             f"'brand' = a PROPER NAME of a specific brand, shop or product line (generic words like "
             f"brand, shop, store, jeweler, company are NOT brands → drop); 'pain' = a word that names a problem or "
             f"complaint (e.g. slippery, tarnish, smell, itchy, broke, peeling); 'feat' = a concrete "
             f"product attribute, material, part or use-case (e.g. cork, titanium, thick, travel, "
             f"sweat, towel); 'drop' = generic filler that says nothing about the product (e.g. "
             f"without, might, different, sure, trying, body, kind, place, please, looks). When unsure "
             f"between feat and drop, choose drop. Reply ONLY JSON.")
    usr = f'Words: {", ".join(cands)}\n\nReturn JSON {{"labels":{{"<word>":"brand|pain|feat|drop"}}}}'
    try:
        out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                           model=fast_model(), max_tokens=1500, temperature=0)
        lab = {k.lower(): v for k, v in (out.get("labels") or {}).items()}
    except Exception as e:
        print(f"  词云标注失败，回退为全部 feat: {e}")
        lab = {}
    # only single-word brands are forced (multi-word ones like "Silver X" would tag 'silver')
    brand_toks = {b.lower() for b in known_brands if re.fullmatch(r"[A-Za-z]{4,}", b.strip())}
    kept = []
    for w in cands:
        kind = "brand" if w in brand_toks else lab.get(w, "feat")
        if kind == "brand" and w in {"brand", "brands", "shop", "store", "jeweler", "company"}:
            kind = "drop"
        if kind in WORD_KINDS:
            kept.append((w, freq[w], kind))
    kept = kept[:28]
    dropped = [w for w in cands[:40] if lab.get(w) == "drop" and w not in brand_toks]
    print(f"E. 词云: {len(cands)} 候选 → 保留 {len(kept)}（"
          + " ".join(f"{k}{sum(1 for *_, c in kept if c == k)}" for k in WORD_KINDS)
          + f"）· 去掉的高频虚词: {', '.join(dropped[:12])}")
    if not kept:
        return []
    mx = kept[0][1]
    return [{"t": w, "s": round(14 + 28 * n / mx), "c": k} for w, n, k in kept]

def pain_candidates(assigned, n=15):
    pains = [x for x in assigned if x.get("pain") and 25 <= len(x["raw"]) <= 200]
    pains.sort(key=lambda x: x["score"], reverse=True)
    seen, out = set(), []
    for x in pains:
        key = x["raw"][:40]
        if key in seen:
            continue
        seen.add(key)
        out.append(x)
        if len(out) >= n:
            break
    return out

def _quote(x):
    return {"quote": "“" + re.sub(r"\s+", " ", x["raw"]).strip() + "”",
            "sub": "r/" + x["sub"], "url": "https://www.reddit.com" + x["link"]}

def _pain(x):
    return {"t": re.sub(r"\s+", " ", x["raw"]).strip(), "sub": "r/" + x["sub"],
            "up": str(x["score"]), "url": "https://www.reddit.com" + x["link"]}

def _cand_line(j, x):
    ctx = f' [thread: {x["ctx"][:70]}]' if x.get("ctx") else ""
    text = re.sub(r"\s+", " ", x["raw"])[:220]
    return f"  {j}: {text}{ctx}"

def pick_quotes(rows, pains):
    """One LLM call: per direction choose the candidate that is ON-TOPIC for the keyword and
    illustrates that direction (or -1 = none fits); rank pain candidates the same way.
    Falls back to the old score-based pick if the call fails."""
    blocks = []
    for r in rows:
        if r["cands"]:
            blocks.append(f'[{r["id"]}] {r["cn"]} — {r["desc"]}\n' +
                          "\n".join(_cand_line(j, x) for j, x in enumerate(r["cands"])))
    plist = "\n".join(_cand_line(j, x) for j, x in enumerate(pains))
    sys_p = (f"You pick evidence quotes for a product-selection report about '{KW}'. A quote is "
             f"VALID only if it is about '{KW}' itself (or the exact product type) AND shows a buyer "
             f"need, complaint, preference or recommendation. REJECT quotes about a different product "
             f"(e.g. shoes, jewelry, furniture when the keyword is something else), jokes, compliments "
             f"on someone's photo/creation ('so cute', 'gorgeous', 'love it' are INVALID even if they name "
             f"the product), chit-chat, or anything a seller cannot act on. The [thread] title is "
             f"context only. Prefer specific, concrete quotes. Returning -1 is better than a weak "
             f"quote. Think briefly per direction in 'why' before choosing. Reply ONLY JSON.")
    usr = (f"Direction candidates:\n" + "\n\n".join(blocks) +
           f"\n\nPain-point candidates:\n{plist}\n\n"
           f'Return JSON {{"why":{{"<direction id>":"<10 words>"}},"quotes":{{"<direction id>":<index or -1>}},'
           f'"pains":[<up to 5 VALID pain indices, best first>]}}')
    try:
        out, _ = chat_json([{"role": "system", "content": sys_p}, {"role": "user", "content": usr}],
                           model=fast_model(), max_tokens=1200, temperature=0)
        qsel, psel = out.get("quotes") or {}, out.get("pains") or []
    except Exception as e:  # keep the report building even if this step fails
        print(f"  pick_quotes 失败，回退到按分数选: {e}")
        qsel, psel = {r["id"]: 0 for r in rows}, list(range(min(5, len(pains))))
    quotes, rejected = {}, 0
    for r in rows:
        j = qsel.get(r["id"], -1)
        if isinstance(j, int) and 0 <= j < len(r["cands"]):
            quotes[r["id"]] = _quote(r["cands"][j])
        elif r["cands"]:
            rejected += 1
    pp = [_pain(pains[j]) for j in psel if isinstance(j, int) and 0 <= j < len(pains)][:5]
    print(f"D2. 引文: {len(quotes)}/{len(rows)} 方向有合格引文（{rejected} 个方向全部候选被判离题）"
          f" · 痛点流 {len(pp)}/{len(pains)} 条候选入选")
    return quotes, pp


# ---------- run -----------------------------------------------------------
def main():
    t0 = time.time()
    REPORTS.mkdir(parents=True, exist_ok=True)
    cache = REPORTS / f"labels_{slug}.json"
    if REUSE:
        c = json.loads(cache.read_text(encoding="utf-8"))
        if c.get("n_items") != len(items):
            raise SystemExit(f"{cache.name} 与当前语料不匹配，去掉 --reuse-labels 重跑")
        dirs, labels = c["dirs"], {int(k): v for k, v in c["labels"].items()}
        print(f"A+B. 复用缓存标注: {len(dirs)} 方向, {len(labels)} 条")
    else:
        ckpt = REPORTS / f"labels_{slug}.partial.json"
        resume = None
        if ckpt.exists():
            c = json.loads(ckpt.read_text(encoding="utf-8"))
            if c.get("n_items") == len(items):
                resume = c["dirs"]  # the saved labels refer to these directions
            else:
                ckpt.unlink()
        if resume:
            dirs = resume
            print("A. 发现上次未完成的标注，沿用其方向并从断点继续: " + ", ".join(d["cn"] for d in dirs))
        elif KEEP_DIRS and cache.exists():
            dirs = json.loads(cache.read_text(encoding="utf-8"))["dirs"]
            print(f"A. 沿用上次的 {len(dirs)} 个方向: " + ", ".join(d["cn"] for d in dirs))
        else:
            dirs = timed("聚类", cluster)
        labels = timed("标注", classify, dirs, ckpt)
        labels = timed("补抓", top_up, dirs, labels)
        cache.write_text(json.dumps({"n_items": len(items), "dirs": dirs, "labels": labels},
                                    ensure_ascii=False), encoding="utf-8")
        ckpt.unlink(missing_ok=True)
    rows, assigned = aggregate(dirs, labels)
    # saturation, Amazon supply and quote picking don't depend on each other → run together
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_sat = ex.submit(timed, "饱和度", saturate, rows)
        f_sup = ex.submit(timed, "Amazon", amazon_supply, rows)
        f_quo = ex.submit(timed, "引文", pick_quotes, rows, pain_candidates(assigned))
        scored, supply, (quotes, pains) = f_sat.result(), f_sup.result(), f_quo.result()

    gaps = [supply[r["id"]]["score"] - scored[r["id"]]["sat"] for r in rows if r["id"] in supply]
    offset = round(sum(gaps) / len(gaps)) if len(gaps) >= 3 else 0

    directions = []
    for r in rows:
        sc = scored[r["id"]]
        s, c = sc["sat"], sc["commercial"]
        amz = supply.get(r["id"])
        conflict = None
        if amz:  # measured shelf + Reddit judgment
            s = round(SUPPLY_WEIGHT * amz["score"] + (1 - SUPPLY_WEIGHT) * sc["sat"])
            amz["w"] = SUPPLY_WEIGHT
            gap = amz["score"] - sc["sat"] - offset
            if gap >= CONFLICT_GAP:
                conflict = ("crowded_unmet", f"供需信号矛盾：Amazon 货架拥挤（供给分 {amz['score']}），Reddit 判读却低得多"
                            f"（{sc['sat']}），差距明显超出本品类平均——货多但用户仍有抱怨/求推荐，"
                            f"现有产品可能没解决问题，或是差异化机会；需人工核实")
            elif -gap >= CONFLICT_GAP:
                conflict = ("empty_served", f"供需信号矛盾：Amazon 货架偏空（供给分 {amz['score']}），Reddit 判读却高得多"
                            f"（{sc['sat']}），差距明显超出本品类平均——需求可能本身小或有替代品，"
                            f"也可能是搜索词太窄；需人工核实")
        opp = 100 - s
        zone = ("op" if r["heat"] >= 50 and opp >= 50 else "red" if r["heat"] >= 50 else
                "blue" if opp >= 50 else "tail")
        note = None
        if c < 50:  # flag low commercial value, but only DEMOTE if it's clearly not a product
            note = f"商业价值偏低({c})——讨论多≠好卖，判读需人工复核"
            if c < 30:
                zone = "tail"
                note = "护理/服务或欣赏向，选品价值低（讨论多≠好卖）"
        # zone hangs on the 50 line or the 3 judgments disagreed → say so instead of hiding it
        runs = sc.get("sat_runs") or []
        if zone != "tail" or c >= 30:
            if abs(opp - 50) <= 5 or (sc.get("spread") or 0) >= 15:
                edge = (f"饱和度接近分界线（Reddit 三次判读 {'/'.join(map(str, runs))}"
                        + (f"，Amazon 实测 {amz['score']}" if amz else "") + f"，综合 {s}），区域判定需谨慎")
                note = f"{note}；{edge}" if note else edge
        if amz and amz.get("dedicated", 9) <= 1:
            zero = (f"Amazon 第 1 页几乎没有专门针对这个方向的产品（{amz['dedicated']} 个，搜「{amz['query']}」）：可能是真空白，"
                    f"也可能是搜索词不贴切或卖家标题不这么写——点开搜索核实后再下结论")
            note = f"{note}；{zero}" if note else zero
        if r["mentions"] < THIN and zone != "tail":
            thin = f"提及仅 {r['mentions']} 条（不足 {THIN}），热度是品类内相对分，区域不作判断"
            note = f"{thin}；{note}" if note else thin
            zone = "thin"
        evi = sc
        d = {"id": r["id"], "cn": r["cn"], "en": r["en"], "heat": r["heat"], "sat": s,
             "fresh": r["fresh"], "mentions": r["mentions"],
             "sent": "thin" if r["mentions"] < THIN else r["sent"],
             "score": r["score"], "split": r["split"], "zone": zone, "commercial": c,
             "asks": r["asks"], "brands": evi.get("brands", []),
             "evidence": evi.get("evidence", ""), "sat_runs": runs,
             "sat_reddit": sc["sat"], "amazon": amz}
        if conflict:
            d["conflict"], d["conflict_text"] = conflict
        if note:
            d["note"] = note
        if r["id"] in quotes:
            d.update(quotes[r["id"]])
        else:
            d.update({"quote": "（暂无合适引文）", "sub": "", "url": "#"})
        directions.append(d)

    banner = None
    if supply and offset >= OFFSET_BANNER:
        banner = (f"Amazon 实测普遍比 Reddit 判读高 {offset} 分：这个品类在 Amazon 上整体已经很拥挤。"
                  f"Reddit 上的抱怨和求推荐，更多说明「现有产品还不够好」（差异化机会），而不是「货架上没货」。")
    elif supply and offset <= -OFFSET_BANNER:
        banner = (f"Amazon 实测普遍比 Reddit 判读低 {-offset} 分：Amazon 上这个品类供给偏少，"
                  f"需求可能在别的渠道被满足（如 Etsy/线下），或搜索词与 Reddit 讨论的产品不完全对应。")
    known = {b for e in scored.values() for b in e["brands"]}
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_cloud = ex.submit(timed, "词云", wordcloud, assigned, known)
        summary, takeaways = timed("摘要", narrate, directions, *pick_highlights(directions), banner)
        cloud = f_cloud.result()

    win = "?"
    ts = [x["t"] for x in items if x["t"]]
    if ts:
        win = (datetime.fromtimestamp(min(ts), timezone.utc).strftime("%Y-%m") + " → " +
               datetime.fromtimestamp(max(ts), timezone.utc).strftime("%Y-%m"))
    report = {
        "keyword": KW, "source": "Reddit · LLM 分析",
        "window": win,
        "meta": {"subreddits": len(D["subreddits"]), "posts": len(D["posts"]),
                 "comments": len(D["comments"]), "analyzed": len(assigned)},
        "summary_html": summary, "takeaways": takeaways,
        "directions": directions, "cloud": cloud,
        "painpoints": pains,
    }
    report["quick"] = bool(D.get("quick"))
    report["low_sample"] = sum(d["mentions"] for d in directions) < LOW_SAMPLE
    report["niche_below"] = THIN  # build_report lists directions under this as 小众需求
    report["supply"] = bool(supply)
    report["supply_weight"] = SUPPLY_WEIGHT if supply else 0
    report["supply_offset"], report["supply_banner"] = (offset if supply else None), banner
    n_conf = sum(1 for d in directions if d.get("conflict"))
    if supply:
        print(f"   供需矛盾方向 {n_conf}/{len(directions)}（品类平均差 {offset:+d}，偏离 ≥ {CONFLICT_GAP} 才算）")
    out_path = REPORTS / f"report_data_{slug}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n✅ {out_path.name} 已更新 · {len(directions)} 方向 · 用时 {time.time()-t0:.0f}s")
    print("⏱ 分析各步用时（饱和度/Amazon/引文并行，词云/摘要并行）: "
          + " · ".join(f"{k} {v:.0f}s" for k, v in TIMES))
    print(f"   下一步: python build_report.py \"{KW}\"")

if __name__ == "__main__":
    main()
