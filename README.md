# reddit-product-research · Reddit 选品情报

A Claude skill that turns one product keyword into a visual product-opportunity report from real Reddit
buyer discussions plus an Amazon page-1 supply check.

一个 Claude skill：输入一个商品关键词，从 Reddit 真实买家讨论中挖掘细分选品方向，结合 Amazon 第一页的供给情况，生成可视化的中文选品情报报告。

---

## What you get / 你会得到什么

- 5-7 sellable sub-directions (细分方向) for the keyword, e.g. `yoga mat` → non-slip, durability, cleaning, portability, eco materials…
- Per direction: demand heat, freshness, sentiment, saturation, opportunity zone (机会区 / 红海 / 蓝海苗头 / 长尾)
- Verifiable evidence: brands named on Reddit, "which one should I buy" counts, Amazon page-1 dedicated products with the title words that prove it, one representative buyer quote linking back to Reddit
- An opportunity quadrant, a typed word cloud (brand / pain / feature), a pain-point stream
- Flags where the analysis is uncertain (borderline zones, Amazon-vs-Reddit disagreement, thin categories)

## Requirements / 需要准备

| | Required? | Cost | How |
|---|---|---|---|
| Python 3.9+ | yes | — | `python3 -m pip install -r requirements.txt` |
| LLM API key (any OpenAI-compatible) | **yes** | a few cents per report on DeepSeek | see below |
| Scrape.do token (Amazon supply side) | optional, recommended | free tier 1000 requests/month, ~7 per report | sign up at https://scrape.do |
| Reddit data | — | free | Arctic Shift public archive, no account |

**You bring your own keys. 使用者需自行提供 API key 并注册 Scrape.do 账号。** Keys are read from environment
variables only. Never paste a key into a chat or commit it to a file — if you did, revoke it and make a new one.
请把 key 设置在自己的终端环境变量里，不要发到聊天里，也不要写进文件。

```bash
# LLM — pick one / 三选一
export DEEPSEEK_API_KEY="..."                                    # default 默认，最便宜
export LLM_PROVIDER=openai;  export OPENAI_API_KEY="..."
export LLM_PROVIDER=custom;  export LLM_API_KEY="..."; export LLM_BASE_URL="https://.../v1"; export LLM_MODEL="..."
                                                                 # Qwen / Kimi / Zhipu / OpenRouter / local …

# Amazon supply side (optional) / Amazon 供给侧（可选）
export SCRAPEDO_TOKEN="..."
```

Put these lines in `~/.zshrc` or `~/.bashrc` so they persist. 写进 `~/.zshrc` 或 `~/.bashrc` 可长期生效。
Check without revealing the key / 检查是否已设置（不显示 key 内容）: `[ -n "$DEEPSEEK_API_KEY" ] && echo set || echo missing`

## Install / 安装

Copy this folder to your Claude skills directory (Claude Code: `~/.claude/skills/reddit-product-research/`),
or install the packaged `.skill` file. Then just ask Claude, e.g.:

把文件夹复制到 Claude 的 skills 目录（Claude Code：`~/.claude/skills/reddit-product-research/`），或安装打包好的 `.skill` 文件。然后直接对 Claude 说：

> 帮我看看 dog harness 在 Reddit 上有哪些细分选品机会
> Is the "standing desk mat" market saturated? What do buyers complain about?

## Run without Claude / 不通过 Claude 直接运行

```bash
python3 scripts/run.py "yoga mat"               # full run → rpr-output/reports/yoga-mat.html
python3 scripts/run.py "yoga mat" --plan-only   # just show chosen subreddits & queries
python3 scripts/run.py "yoga mat" --reuse-labels  # re-score & re-render cheaply
```

Outputs go to `$RPR_WORKDIR` (default `./rpr-output`): `reports/`, `logs/`, `data/`, `cache/`.
Typical run: 15-25 min, longer for very busy communities; per-stage timings are printed at the end. On macOS idle sleep is blocked during the run, but closing a laptop lid still pauses it. If a run fails midway, rerun the same command — the crawl, the subreddit plan and finished labelling batches are all reused.

运行时间：约 15–25 分钟，社区越活跃越久，结束时会打印各步用时。macOS 上运行期间会自动阻止闲置休眠，但合盖仍会暂停任务。中途失败直接重跑同一命令即可，已完成的部分会自动复用、断点续跑。

## How it works / 原理

See [`references/methodology.md`](references/methodology.md) for every formula and threshold. In short:

1. LLM proposes candidate subreddits + pain terms → each verified on a sample of its posts → top 7 paged through by date and filtered locally
2. LLM clusters the discussion into sellable directions and labels every post/comment
3. Heat, freshness, sentiment, asks computed from data; saturation = 70% Amazon page-1 supply (dedicated
   products, verified by quoted title words) + 30% Reddit judgment (rubric, calibrated, median of 3)
4. Highlights picked by code from the zones; LLM only writes the wording

## Honest limits / 局限

- Reddit is English-heavy and enthusiast-heavy. Gift/decor/craft products show weak signal (mostly show-and-tell).
- The Reddit half of saturation and the commercial score are model judgments with evidence, not measurements.
- Amazon = one query per direction, page 1, US marketplace. The query wording can move saturation a lot; the card warns when page 1 has 0-1 dedicated products.
- Directions with fewer than 30 mentions are listed as 小众需求 (leads), not scored. Cold categories (under 100 analyzed items) get no verdict.
- Sentiment labelling was checked against 150 hand-labelled items on one keyword only (86-87% agreement).
- Some networks' DNS filters block the Reddit archive; the run stops within seconds and says so.
- Respect the free services: the Reddit archive is a community resource — don't hammer it.

## Data sources / 数据来源

- Reddit content via the [Arctic Shift](https://github.com/ArthurHeitmann/arctic_shift) public archive API
- Amazon search results via [Scrape.do](https://scrape.do) (your account)
- LLM: your provider

Use of each service is subject to its own terms. 各数据服务的使用须遵守其各自条款。

## License / 许可证

Free to use, including for your own business. Modifying, redistributing or selling it is not allowed.
See [LICENSE](LICENSE).

可免费使用，包括用于你自己的生意（比如给自己的店铺选品）；不允许修改后发布、再分发或售卖。详见 [LICENSE](LICENSE)。
