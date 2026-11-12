# Methodology / 方法论

Every number in the report, how it is computed, and why. Code references are to `scripts/`.

## Contents
1. Collection (subreddit selection, queries)
2. Directions and labelling
3. Data-layer metrics: mentions, heat, freshness, sentiment, asks
4. Saturation: Reddit judgment + Amazon supply
5. Zones, flags and highlight cards
6. Quotes, pain stream, word cloud
7. Known limits

---

## 1. Collection — `m1_collect.py`

**Subreddit auto-selection.** One LLM call proposes ~15 candidate subreddits, each with a buyer-fit score
1-5 (5 = people ask which one to buy / complain about it; 1 = makers, sellers, loosely related), 8 pain
queries, and 0-2 aliases (other names buyers use: trinket dish → ring dish, jewelry dish).

Each candidate with fit ≥ 3 is verified on its 5 newest listing pages (500 posts): keyword, alias and
pain-term matches are counted locally. If those pages don't reach back 180 days, the count is scaled to
the full window (shown with `≈`).

Rank = `fit × log(1 + posts)` — a fit-5 community with 20 posts beats a fit-2 community with 100.
Keep the top 7 with ≥ 3 posts and fit ≥ 3. If verification fails, or a busy sub's sample has no match, fit-≥4
candidates fill the remaining slots marked `?` (unverified). Fewer than 3 verified communities triggers
the "thin Reddit signal" warning.

**Crawl.** Full-text search is heavily rate-limited by the archive and returns at most 100 posts per query,
so each chosen sub's post listing is paged by date instead (about 0.6 s per 100 posts) and filtered locally:
a post matches if it contains every word of any term, plurals allowed. Subs named after the product keep
every post. Up to 200 pages per sub; a busier sub is read in runs of 5 pages spread evenly across the
window, so 新鲜度 isn't skewed to recent posts. The 250 most-discussed matches per sub are kept, then
comments of 40 posts: slots are shared by each sub's share of the posts (at least 3, at most 35%), most
discussed first within a sub, so one talkative community can't dominate the corpus. Listings are stored 7 days.

(`--quick` still exists for old commands but is no longer documented: 4 subs, 60 pages, 150 posts, 20
comment threads. Too few items for most directions to reach 30 mentions.)

## 2. Directions and labelling — `m2_llm.py`

- **Cluster:** top 55 posts + 85 comments by score → 5-7 mutually distinct, *sellable* directions
  (material, feature, use-case, style tier, buyer segment). Aftercare/service advice and pure
  show-and-tell are excluded; synonyms are merged. temperature 0.
- **Label:** in batches of 25, two steps per item. First `about`: does it actually discuss the product?
  Items mainly about the person's situation, behaviour, training, health or care are `about=false`, get
  `none`, and are excluded from all metrics. Then, if about: direction, sentiment toward the product
  (never the writer's mood), pain. The labeller reads items up to 1,000 characters whole; longer ones as
  the first 300 characters plus every sentence naming the product noun with one sentence either side,
  1,500 characters at most. Comments also show the thread title.
  A positive or negative label must quote the item, and code checks the quote really exists in it;
  otherwise the label falls back to neutral. (An earlier version also required the product noun next to
  the quote; it demoted ~20% of earring opinions written as "these", "studs" or a brand, and dropping it
  made no measurable difference on the 150 hand-labelled items.)
  Top-up: a thread's comments stay on its topic, so a small direction grows only by reading more
  threads about it (yoga mat: 150 instead of 40 threads lifted 3 of 4 thin directions; 100 instead of
  40 comments per thread lifted none). After labelling, each direction under 30 mentions gets the
  comments of up to 15 unread posts already labelled with it (most comments first, round-robin across
  directions, 3-minute cap); only the new comments are labelled and they are saved into the corpus.
  Fixed rules: only other products = not about; good and bad in one item = neutral; a product that broke =
  negative; better without it or switching to another kind of product = negative.
  Checked against 150 hand-labelled items on one keyword (`dog harness`, one rater, adjudicated, used
  while tuning, so optimistic, no held-out check of the final prompt): three-way agreement 86-87%, overall
  negative share 12-13% against 13% by hand, negative precision about 66%, negative recall 63-68%, and
  about 45% of relevant items missed.
  Read sentiment as a share across many items, not item by item.
  "分析讨论" in the header = items assigned to some direction.

## 3. Data-layer metrics (measured)

| Metric | Formula |
|---|---|
| 提及量 mentions | items labelled to the direction |
| 需求热度 heat | `mentions + 6·√(sum of positive upvotes)`, rescaled so the top direction = 95 |
| 新鲜度 freshness | share of the direction's items from the last 90 days ÷ that share across all assigned items, × 50, clamped 0-100 (50 = average) |
| 情感 sentiment | pos / neu / neg %; net = pos% − neg%; >12 正面, <−12 负面, else 混合 |
| 求推荐帖 asks | items matching an ask pattern ("where can I…", "any recommendations", "looking for", "which … should I") |

## 4. Saturation 饱和度 (higher = better served = less opportunity)

**Final = 0.7 × Amazon supply score + 0.3 × Reddit judgment** (override with `--supply-weight`). Without a
Scrape.do token: Reddit judgment only.

### 4a. Reddit judgment (judged)
The LLM sees each direction's hard signals (mentions, heat, negative %, asks) and sample texts and scores
0-100 against a rubric:
- 0-30 many unanswered asks, frequent complaints, no trusted brand
- 30-50 a few brands named but asks keep coming / complaints common
- 50-70 a few consensus brands answer most asks, complaints minor
- 70-100 clear consensus picks, high satisfaction, little unmet need

Calibration anchors from reviewed reports: earrings / hypoallergenic → 35; yoga mat / durability (Manduka,
Liforme as "buy once" consensus) → 60; trinket dish / handmade custom (mostly show-and-tell) → 85.
It must list only brands present in the samples and give a one-sentence evidence line. Sampled 3× at
temperature 0.7, the **median** is used; the 3 values are kept for the borderline check. Measured effect:
run-to-run spread fell from ~8-10 points to ~2.

### 4b. Amazon supply score (measured) — `supply_client.py`
For each direction the LLM writes one 2-4 word shopper query containing the product noun (no brands, no
accessories). Scrape.do returns Amazon US search page 1; sponsored results are dropped and duplicates
removed (same ASIN or same title), leaving the top 10 organic listings.

A separate LLM call per direction marks which of those 10 are **dedicated** to the direction — the title
must explicitly advertise any core attribute of it (synonyms OK: "Dry-Grip" = non-slip, "dishwasher safe"
= easy clean). It must quote the title words; the code accepts the product only if the quote really
appears in that title. (Without this check the model once marked 10/10 bottles "easy clean" when no title
mentioned cleaning.)

```
supply = 0.40 × (dedicated / 10 × 100)
       + 0.35 × reviews_score(median reviews of dedicated products)   # 100→25, 1k→50, 10k→75, 100k→100
       + 0.25 × (dedicated products with ≥ 1,000 reviews / 10 × 100)
```

Page 1 full of generic best-sellers but no dedicated product → low score: the need exists but nobody builds
specifically for it. Amazon's total result count is deliberately **not** used: it counts loosely related
listings ("easy clean water bottle" → 90,000+ results with 0 dedicated products on page 1) and is capped at
"over 10,000". An earlier version that used it, and counted generic best-sellers as supply, scored almost
every niche 80-95 — that is the failure mode this design avoids.

### 4c. Disagreement
`offset` = mean(Amazon − Reddit) across the category's directions.
- |offset| ≥ 20 → a category-level banner under the summary (e.g. shelf is crowded across the board).
- A direction whose (Amazon − Reddit − offset) is ≥ +25 or ≤ −25 gets a **供需信号矛盾** flag:
  - crowded shelf, Reddit still unhappy → products exist but don't solve it → differentiation lead
  - empty shelf, Reddit satisfied → small need, substitutes elsewhere, or the query was too narrow

## 5. Zones, flags, highlight cards

`机会度 opportunity = 100 − saturation`

| Zone | Rule |
|---|---|
| 机会区 opportunity | heat ≥ 50 and opportunity ≥ 50 |
| 红海 red ocean | heat ≥ 50 and opportunity < 50 |
| 蓝海苗头 emerging | heat < 50 and opportunity ≥ 50 |
| 长尾 long tail | heat < 50 and opportunity < 50 |

- Commercial value < 50 → note "讨论多≠好卖"; < 30 → forced to 长尾 (service/appreciation, not a product).
- **Borderline** note when |opportunity − 50| ≤ 5 or the 3 Reddit judgments spread ≥ 15.
- **Highlight cards are chosen in code, not by the LLM**, so they can never contradict the quadrant:
  - headline = highest-heat 机会区 direction; if none, highest-heat 蓝海 labelled
    "最佳机会（无机会区，取蓝海热度最高）"; if neither, "暂无明显机会"
  - strongest pain = most negative *mentions* (count × neg %), excluding the headline
  - emerging = 蓝海 direction maximising opportunity + freshness, excluding the other two
  The LLM only writes the wording, and is told the exact role labels.

## 6. Quotes, pain stream, word cloud

- **Card quote:** 6 candidate comments (pain first, then score), shown to the LLM with their thread title;
  it picks the one that is about the product itself and shows a buyer need, or none ("暂无合适引文").
- **Pain stream:** 15 highest-scored pain items, deduped; the LLM keeps up to 5 on-topic ones.
- **Word cloud:** frequency over analysed items only; stopwords, contractions, links, the keyword itself
  and plural duplicates removed; the LLM types the top 70 as brand / pain / feature / filler and filler is
  dropped. Single-word brands found in the saturation step are forced to "brand". Colours = word type
  (blue brand, red pain, grey feature), not sentiment — the same word appears in praise and complaints, so
  sentiment colouring was misleading; sentiment lives in the per-direction bars.

## 7. Known limits

- Reddit skews English-speaking, US, enthusiast. Gift/decor/craft categories are mostly show-and-tell.
- The archive sample is recent-heavy, so freshness differences are compressed.
- Clustering can produce different directions on full re-runs (`--keep-dirs` pins them).
- Amazon = one marketplace, one query, page 1. Check the linked search for niches that matter.
- The Reddit judgment and commercial score are model opinions with evidence attached, not measurements.
