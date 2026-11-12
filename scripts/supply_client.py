"""
Supply-side (marketplace) signal: how crowded is the shelf for a product direction?

Default provider: Scrape.do Amazon Search API (structured JSON, 1 credit/request,
1000 free credits/month). Reads the token from env — never hard-code:
  export SCRAPEDO_TOKEN="..."

amazon_search() is cached on disk for 7 days so re-running a report costs nothing.
organic() → page-1 organic results; the caller marks which are DEDICATED to the
sub-direction (LLM on titles); supply_metrics()/supply_score() → 0-100 (higher = the
sub-direction is already well served by dedicated, established products).
"""
from __future__ import annotations
import hashlib, json, math, os, re, statistics, time
from pathlib import Path
from urllib.parse import quote_plus

import requests

from paths import CACHE as _CACHE
CACHE = _CACHE / "amazon"
TTL = 7 * 86400
ENDPOINT = "https://api.scrape.do/plugin/amazon/search"


def available() -> bool:
    return bool(os.environ.get("SCRAPEDO_TOKEN"))


def amazon_search(keyword: str, geocode: str = "us", retries: int = 3) -> dict | None:
    """One Amazon search results page (page 1). None if no token or the call failed."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / (hashlib.sha1(f"{geocode}|{keyword.lower()}".encode()).hexdigest() + ".json")
    if path.exists() and time.time() - path.stat().st_mtime <= TTL:
        return json.loads(path.read_text())
    token = os.environ.get("SCRAPEDO_TOKEN")
    if not token:
        return None
    for attempt in range(retries):
        try:
            r = requests.get(ENDPOINT, params={"token": token, "keyword": keyword,
                                               "geocode": geocode, "page": 1}, timeout=90)
        except requests.RequestException as e:
            print(f"  [amazon] 网络错误 {e}")
            time.sleep(2 ** attempt)
            continue
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success" or data.get("products"):
                path.write_text(json.dumps(data))
                return data
            print(f"  [amazon] '{keyword}' 返回异常: {data.get('errorMessage')}")
        elif r.status_code in (401, 403):
            print(f"  [amazon] token 无效或额度用完 ({r.status_code}): {r.text[:120]}")
            return None
        else:
            print(f"  [amazon] '{keyword}' HTTP {r.status_code}: {r.text[:120]}")
        time.sleep(3 * (attempt + 1))
    return None


def _count(p: dict) -> int:
    rc = (p.get("rating") or {}).get("count")
    if isinstance(rc, (int, float)):
        return int(rc)
    m = re.search(r"([\d.]+)\s*([KM]?)", str(p.get("reviewCount") or ""), re.I)  # "(9.1K)"
    if not m:
        return 0
    return int(float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6}[m.group(2).upper()])


def organic(data: dict, top_n: int = 10) -> list[dict]:
    """Page-1 organic (non-sponsored) results — what a buyer actually sees and clicks."""
    out, seen = [], set()
    for p in (data.get("products") or []):
        if p.get("isSponsored"):
            continue
        keys = {p.get("asin"), re.sub(r"\W+", " ", (p.get("title") or "").lower()).strip()[:60]} - {None, ""}
        if keys & seen:  # Scrape.do sometimes repeats a listing / its variants (seen 5× on one page)
            continue
        seen |= keys
        out.append({"title": (p.get("title") or "").strip(), "reviews": _count(p),
                    "rating": (p.get("rating") or {}).get("value"),
                    "price": (p.get("price") or {}).get("amount"), "asin": p.get("asin")})
        if len(out) >= top_n:
            break
    return out


def supply_metrics(org: list[dict], dedicated: set[int]) -> dict | None:
    """dedicated = indices of products built specifically for this sub-direction (judged by
    LLM from titles). The rest are generic best-sellers merely ranking for the query.
    Amazon's total result count is deliberately NOT used: it counts loosely related listings
    ('easy clean water bottle' → 90,000+ with 0 dedicated products on page 1) and is capped."""
    if not org:
        return None
    ded = [o for i, o in enumerate(org) if i in dedicated]
    counts = [o["reviews"] for o in ded]
    prices = [o["price"] for o in ded if isinstance(o["price"], (int, float))]
    ratings = [o["rating"] for o in ded if isinstance(o["rating"], (int, float))]
    return {
        "n": len(org), "dedicated": len(ded), "generic": len(org) - len(ded),
        "ded_median_reviews": int(statistics.median(counts)) if counts else 0,
        "ded_strong": sum(1 for c in counts if c >= 1000),
        "ded_avg_rating": round(statistics.mean(ratings), 2) if ratings else None,
        "ded_median_price": round(statistics.median(prices), 2) if prices else None,
        "examples": [{"title": o["title"][:80], "reviews": o["reviews"]} for o in ded[:3]],
    }


def supply_score(m: dict) -> int:
    """0-100, higher = this sub-direction is already well served by DEDICATED products.
       dedicated products among page-1 top 10      40%
       median reviews of those dedicated products  35%  — 100→25, 1k→50, 10k→75, 100k→100
       dedicated products with ≥1k reviews         25%
    Page 1 full of generic best-sellers but no dedicated product → low score (need exists,
    nobody builds specifically for it)."""
    n = max(1, m["n"])
    share = 100 * m["dedicated"] / n
    rev = max(0.0, min(100.0, 25 * math.log10(m["ded_median_reviews"] + 1) - 25)) if m["dedicated"] else 0.0
    strong = 100 * m["ded_strong"] / n
    return round(0.40 * share + 0.35 * rev + 0.25 * strong)


def search_url(keyword: str) -> str:
    return "https://www.amazon.com/s?k=" + quote_plus(keyword)
