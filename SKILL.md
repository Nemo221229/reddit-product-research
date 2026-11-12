---
name: reddit-product-research
description: Turn one e-commerce product keyword into a visual product-opportunity report by mining real Reddit buyer discussions (complaints, "which one should I buy" asks, brand mentions) and checking Amazon page-1 supply, then splitting the category into 5-7 sellable niches scored on demand heat, sentiment, saturation and opportunity. Use this skill whenever someone wants to find product niches, validate a product idea, do 选品 / 选品方向 / 细分市场 / 市场机会 / 蓝海红海 analysis, asks "what should I sell", "is X saturated", "what do buyers complain about with X", or wants Reddit-based market or trend research for Amazon, Etsy, Shopify, TikTok Shop or any online store — even if they don't mention Reddit or ask for a "report" explicitly.
---

# Reddit Product Research · Reddit 选品情报

Give it a product keyword (e.g. `yoga mat`, `water bottle`, `earrings`). It finds where real buyers talk
about that product on Reddit, reads the discussion with an LLM, splits the category into 5-7 sellable
sub-directions (细分方向), scores each one, checks how crowded Amazon page 1 already is, and renders a
self-contained Chinese HTML report: summary → opportunity quadrant → word cloud → per-niche cards with
verifiable evidence → pain-point quotes with links back to Reddit → methodology.

Everything runs through `scripts/run.py`. Your job is to set it up, run it, present the report, and
explain what it means — including where it should not be trusted.

## 1. First-time setup / 首次配置

The user brings their own keys. Keys live in the user's shell, never in files, never in chat — a key
pasted into a conversation is stored in transcripts and should be considered leaked. If a user starts
pasting one, stop them and tell them to revoke it and set it in their shell instead.

```bash
python3 -m pip install -r <skill-dir>/requirements.txt
```

**LLM (required)** — any OpenAI-compatible chat API. Ask which one they have; DeepSeek is the default
and cheapest (a full report is a few cents).

| Provider | What the user sets in `~/.zshrc` / `~/.bashrc` |
|---|---|
| DeepSeek (default) | `export DEEPSEEK_API_KEY="..."` |
| OpenAI | `export LLM_PROVIDER=openai` + `export OPENAI_API_KEY="..."` |
| Anything OpenAI-compatible (Qwen, Kimi, Zhipu, OpenRouter, local) | `export LLM_PROVIDER=custom` + `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` |

**Amazon supply side (optional, recommended)** — free Scrape.do account (https://scrape.do, 1000 free
requests/month, no card). One report uses ~7 requests; results are cached 7 days.
`export SCRAPEDO_TOKEN="..."`. Without it the report still works, but saturation then relies on
Reddit judgment alone — say so when presenting.

Suggest the user runs the `export` lines themselves (in Claude Code: `! echo 'export X="..."' >> ~/.zshrc`)
and then restarts the shell or sources the profile. To check a key is present without printing any of
it: `[ -n "$DEEPSEEK_API_KEY" ] && echo set || echo missing`. Commands you run may need
`source ~/.zshrc` first to see keys added after the session started.

Reddit data comes from the Arctic Shift public Reddit archive — no account needed.

## 2. Pick the keyword / 确定关键词

Reddit is mostly English, so the keyword should be the English product noun buyers actually type:
`ring dish` not `首饰托盘`. If the user gives Chinese or a vague theme ("圣诞礼物", "宠物用品"), propose
one or two concrete English product nouns and confirm. If the user already names a concrete product
(even inside a Chinese sentence, e.g. "dog harness 在 Reddit 上有哪些机会"), just use it. A single product
type works far better than a broad theme — "dog harness" yields niches, "pet supplies" yields mush.

## 3. Run / 运行

All outputs go to `$RPR_WORKDIR` (default `./rpr-output` in the current directory): `data/`, `cache/`,
`reports/`. Set `RPR_WORKDIR` if the user wants them elsewhere.

Optional dry run — picks and saves the subreddits and queries without crawling posts. It still samples
each candidate's recent posts, so it takes a minute or two; the full run then reuses the saved plan
(`--replan` to choose again):

```bash
python3 <skill-dir>/scripts/run.py "yoga mat" --plan-only
```

Full run:

```bash
python3 <skill-dir>/scripts/run.py "yoga mat"
```

Run it in the background with a long timeout. Every run writes a full log to
`$RPR_WORKDIR/logs/<slug>-<time>.log` (the path is printed first) — tail it to report progress.

Tell the user the expected time up front so a slow run doesn't look broken:
- **About 15-25 minutes** (measured 11-21 before top-up on water bottle / yoga mat / earrings, plus up
  to 3 minutes of top-up). Busier communities take longer. On macOS the run blocks idle sleep itself, but closing a laptop
  lid still pauses it; the final `⏱` line then says how long the machine slept.
- To check which communities a keyword would use before committing 15+ minutes, run `--plan-only`
  (1-2 minutes). There is no fast mode: a smaller sample mostly turns directions into 小众需求.
  If the archive can't be reached at all (DNS filter, firewall), the run stops within seconds and says so.
- **Analysis** (incl. Amazon): ~1-2 minutes for ~2,000 posts+comments — labelling runs 8 batches at a
  time (`RPR_CONCURRENCY` to change; lower it if your LLM provider rate-limits), and saturation /
  Amazon / quotes run in parallel. Longer if the LLM API is slow.
- **Top-up** (up to 3 more minutes): directions under 30 mentions get comments from up to 15 more of
  their own threads, then only those comments are labelled. The archive slows repeated comment
  requests, so this step stops at 3 minutes.
- The log ends with per-stage timings (`⏱` lines), so you can tell the user what was slow.

**If a run dies midway, just run the same command again.** Nothing finished is lost: the corpus is
reused for 7 days, the subreddit plan is reused, and labelling resumes from its last saved batch
(`labels_<slug>.partial.json`). A single failed batch is skipped with a warning rather than aborting.

What happens inside (so you can narrate progress):
1. **Collect** — the LLM proposes ~15 candidate subreddits with a buyer-fit score, each is verified
   by sampling its recent posts and counting keyword matches, and the top 7 are kept. Each subreddit's
   180-day post listing is then paged through and filtered locally with auto-generated pain-point terms
   (very busy ones are sampled evenly across the window), then comments of the most-discussed matches
   are pulled.
   If fewer than 3 communities qualify, it warns that the category has thin Reddit signal.
2. **Analyse** — cluster into 5-7 sellable directions → label every item (direction, sentiment, pain) →
   compute heat / freshness / sentiment from data → saturation judged 3× against a rubric and take the
   median → Amazon page-1 check per direction → pick on-topic quotes → typed word cloud → summary.
3. **Render** — `reports/<slug>.html`.

Useful flags: `--reuse-labels` (re-score and re-render without re-labelling — seconds, nearly free;
use it after tweaking weights), `--keep-dirs` (fresh data but the same directions as last time, so two
reports are comparable), `--subs a,b,c` (manual communities), `--recollect` (ignore the 7-day corpus
cache), `--supply-weight 0.6` (Amazon share of saturation, default 0.7).

## 4. Present the report / 交付报告

The HTML is self-contained. If you have a tool for publishing HTML pages (e.g. an Artifact tool),
publish `reports/<slug>.html` and give the link; otherwise give the file path and offer to open it
(`open` on macOS). Keep the report itself free of data-vendor names — the methodology footer describes
the pipeline generically by design.

Then give a short read-out in the user's language:
- the headline opportunity, the strongest pain point and the emerging niche (they come from the
  report's three highlight cards — don't re-rank them),
- 1-2 concrete product ideas grounded in the quoted pains,
- the caveats below that apply to this run.

Before calling something an opportunity, look at what's behind it in
`reports/report_data_<slug>.json` (`directions[]`: `zone`, `sat`, `sat_reddit`, `amazon.query`,
`amazon.dedicated`, `amazon.proofs`, `conflict`, `note`; top level: `takeaways`, `supply_banner`). A low
saturation that rests on an Amazon query with 0-1 dedicated products may be a query artifact (sellers
describe it differently) rather than an empty shelf — the card carries a note when dedicated is 0 or 1. Say
so, and suggest the user click the Amazon search link on that card to check.

## 5. How far to trust it / 结论可信度

Be candid; the report is designed to separate what was measured from what was judged.

- **Measured (trust):** quotes (all link to Reddit) and Amazon page-1 counts (each "dedicated product"
  is backed by a quoted title phrase the code verified).
- **Counted from model labels (trust the broad picture):** mentions, heat, freshness and the sentiment
  split. On 150 hand-labelled items the labeller agreed 86-87% of the time and matched the overall negative
  share, but about a third of its negative calls were wrong and about 45% of relevant items were missed. Don't quote small differences or sentiment on a thin direction.
- **Judged (verify):** the Reddit half of saturation and the commercial-value score. They're stable
  run-to-run (median of 3, calibrated rubric) but not validated against ground truth.
- **Thin data:** a direction with under 30 mentions is listed under 小众需求 at the end of section 04
  (mention count + one quote) instead of getting a card, a bubble, a zone or a sentiment: heat is
  relative within the category, so in a cold category 1 mention can score 80+. Present these as leads
  to research with their own keyword, not as opportunities. If the
  whole report has under 100 analyzed items (`low_sample: true`) it gives no product verdict: tell
  the user Reddit says too little about this keyword and suggest a more common one.
- **Borderline flags** ("饱和度接近分界线") mean the zone could flip; treat as either side.
- **"供需信号矛盾" flags** mean Amazon and Reddit disagree for that niche more than usual for the category.
  "Crowded shelf but buyers still complain" often means existing products don't solve the problem —
  a differentiation lead, not a dead end. Worth a manual look at the linked Amazon search.
- **Category fit:** gift/decor/craft items (trinket dishes, art prints) are discussed on Reddit as
  show-and-tell, not as buyer pain — expect few assigned items, few brands and weak signal. Say so
  rather than over-reading the chart.
- **Directions can change** on a full re-run (the clustering is an LLM step). Use `--keep-dirs` when the
  user wants to compare over time.
- Amazon reflects one marketplace's page 1 for one query per niche; the query is shown on each card
  and links to the live search.

For questions like "why is this saturated?", "how is heat computed?" or "why did X land in 红海?", read
`references/methodology.md` — it has every formula, threshold and design decision.

## 6. Troubleshooting / 常见问题

- Run crashed (network drop, DNS error, quota): rerun the same command — it resumes (see §3).

- `连不上 Reddit 存档 … TLS 证书校验失败`: something on the user's network (router/OS content filter such
  as CleanBrowsing, parental controls, a company proxy) is blocking `arctic-shift.photon-reddit.com`.
  Only the user can allow it — tell them which domain to whitelist; don't change their DNS settings.
- LLM `429` / rate-limit retries during labelling: `export RPR_CONCURRENCY=4` (or lower) and rerun.

- `缺少 DEEPSEEK_API_KEY` (or other key): the key isn't in this shell — `source ~/.zshrc` or re-export.
- Many `失败` rows / very slow collection: the Reddit archive is overloaded. Wait and rerun (finished
  subreddits are stored for 7 days), or pass `--subs` to skip verification.
- `没有找到够格的社区`: keyword too niche or too non-English for Reddit. Try a more common English
  name, or pass `--subs`.
- `labels_<slug>.json 与当前语料不匹配`: the corpus changed since labelling — drop `--reuse-labels`.
- Provider rejects JSON mode: handled automatically (falls back to prompt-only JSON).
- `[amazon] token 无效或额度用完`: Scrape.do token wrong or monthly quota used; the run continues
  without the Amazon half.
