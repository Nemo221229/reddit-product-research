"""
One command: keyword → Reddit corpus → LLM analysis (+ Amazon supply) → HTML report.

  python run.py "yoga mat"
  python run.py "yoga mat" --plan-only          # only show which subreddits/queries it would use
  python run.py "yoga mat" --subs yoga,pilates  # manual subreddits (skips auto-selection)
  python run.py "yoga mat" --reuse-labels       # re-score/re-render without re-classifying (cheap)
  python run.py "yoga mat" --recollect          # force a fresh Reddit crawl

Outputs go to $RPR_WORKDIR (default ./rpr-output): data/, cache/, reports/<slug>.html
"""
from __future__ import annotations
import argparse, json, os, subprocess, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from paths import WORK, DATA, REPORTS, slugify  # noqa: E402

FRESH_DAYS = 7


LOG = None  # set in main(): everything is mirrored to <workdir>/logs/<slug>-<time>.log
TIMES = []  # (step, seconds) for the closing summary


def _fmt(sec: float) -> str:
    return f"{sec / 60:.1f} 分钟" if sec >= 90 else f"{sec:.0f} 秒"


def say(msg: str):
    print(msg, flush=True)
    if LOG:
        LOG.write(msg + "\n"); LOG.flush()


def step(title: str):
    say(f"\n{'=' * 64}\n▶ {title}\n{'=' * 64}")


def run(script: str, *args: str, label: str = ""):
    t0 = time.time()
    env = {**os.environ, "RPR_WORKDIR": str(WORK), "PYTHONUNBUFFERED": "1"}
    p = subprocess.Popen([sys.executable, str(HERE / script), *args], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    for line in p.stdout:
        sys.stdout.write(line); sys.stdout.flush()
        if LOG:
            LOG.write(line); LOG.flush()
    r = p
    r.wait()
    if r.returncode != 0:
        raise SystemExit(f"\n✗ {script} 失败（退出码 {r.returncode}），见上方日志")
    TIMES.append((label or script, time.time() - t0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("keyword")
    ap.add_argument("--subs", default="", help="逗号分隔社区名；不填则自动选择")
    ap.add_argument("--queries", default="", help="逗号分隔痛点查询词；不填则自动生成")
    ap.add_argument("--days", type=int, default=180)
    ap.add_argument("--plan-only", action="store_true")
    ap.add_argument("--quick", action="store_true", help=argparse.SUPPRESS)  # kept so old commands still run
    ap.add_argument("--recollect", action="store_true", help="忽略已有语料，重新抓取")
    ap.add_argument("--replan", action="store_true", help="忽略已保存的选社区结果，重新选择")
    ap.add_argument("--reuse-labels", action="store_true", help="复用上次的方向与逐条标注")
    ap.add_argument("--keep-dirs", action="store_true", help="新数据沿用上次的细分方向")
    ap.add_argument("--supply-weight", type=float, default=None, help="Amazon 权重，默认 0.7")
    a = ap.parse_args()

    global LOG
    T0, M0 = time.time(), time.monotonic()  # on macOS monotonic stops while asleep: the gap = sleep
    if sys.platform == "darwin":  # keep idle sleep off for this run (closing the lid still pauses it)
        try:
            subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())])
        except OSError:
            pass
    slug = slugify(a.keyword)
    (WORK / "logs").mkdir(parents=True, exist_ok=True)
    log_path = WORK / "logs" / f"{slug}-{time.strftime('%Y%m%d-%H%M%S')}.log"
    LOG = open(log_path, "w", encoding="utf-8")

    from llm_client import check
    import supply_client
    say(f"工作目录: {WORK}\n日志: {log_path}")
    say(f"LLM: {check()}")
    say("Amazon 供给侧: " + ("已启用" if supply_client.available()
                            else "未设置 SCRAPEDO_TOKEN → 跳过，饱和度仅用 Reddit 判读"))

    corpus = DATA / f"{slug}.json"
    fresh = corpus.exists() and time.time() - corpus.stat().st_mtime < FRESH_DAYS * 86400

    if fresh and not a.quick and json.loads(corpus.read_text()).get("quick"):
        fresh = False  # a full run must not silently reuse a smaller quick-mode corpus
    if a.plan_only or a.recollect or a.replan or not fresh or a.subs:
        step("1/3 抓取 Reddit 讨论（自动选社区 + 痛点查询）")
        m1 = [a.keyword, "--days", str(a.days)]
        if a.subs:
            m1 += ["--subs", a.subs]
        if a.queries:
            m1 += ["--queries", a.queries]
        if a.plan_only:
            m1 += ["--plan-only"]
        if a.replan:
            m1 += ["--replan"]
        if a.quick:
            m1 += ["--quick"]
        run("m1_collect.py", *m1, label="抓取")
        if a.plan_only:
            return
        if not corpus.exists():
            raise SystemExit("✗ 没有抓到语料（可能没有够格的社区）。可用 --subs 手动指定社区。")
    else:
        step(f"1/3 复用 {FRESH_DAYS} 天内已抓取的语料（--recollect 可强制重抓）")
        d = json.loads(corpus.read_text())
        say(f"  {len(d['posts'])} 帖 · {len(d['comments'])} 评论 · 社区: {', '.join(d['subreddits'])}")

    step("2/3 LLM 分析 + Amazon 供给侧")
    m2 = [a.keyword]
    if a.reuse_labels:
        m2.append("--reuse-labels")
    if a.keep_dirs:
        m2.append("--keep-dirs")
    if a.supply_weight is not None:
        m2.append(f"--supply-weight={a.supply_weight}")
    run("m2_llm.py", *m2, label="分析")

    step("3/3 生成报告")
    run("build_report.py", a.keyword, label="出报告")
    out = REPORTS / f"{slug}.html"
    say(f"\n⏱ 总用时 {_fmt(time.time() - T0)}：" + " · ".join(f"{k} {_fmt(v)}" for k, v in TIMES))
    slept = (time.time() - T0) - (time.monotonic() - M0)
    if slept > 60:
        say(f"   其中电脑休眠约 {_fmt(slept)}，实际运行约 {_fmt(time.monotonic() - M0)}（合盖会暂停抓取）")
    say(f"\n✅ 报告: {out}\n   数据: {REPORTS / f'report_data_{slug}.json'}\n   日志: {log_path}")


if __name__ == "__main__":
    main()
