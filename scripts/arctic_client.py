"""
Client for the Arctic Shift public Reddit archive API.

Why Arctic Shift instead of Reddit directly:
  - Reddit's own .json endpoints now 403 non-residential IPs, and the official
    Data API needs an approval we were denied.
  - Arctic Shift (https://arctic-shift.photon-reddit.com) is a maintained public
    archive of Reddit content, built for research. No auth, and it carries
    HISTORY — so 新鲜度 can use real timestamps instead of a new-vs-top proxy.

API notes (verified):
  - /api/subreddits/search?subreddit=<q>   → matches subreddit NAMES only
  - /api/posts/search   requires `subreddit` or `author`; supports `query` (full-text), `after`, `before`, `limit`, `sort`
  - /api/comments/search  same shape
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from urllib.parse import urlencode

import requests

BASE = "https://arctic-shift.photon-reddit.com/api"
USER_AGENT = "reddit-product-research/0.1 (personal research; read-only)"
from paths import CACHE
CACHE_DIR = CACHE / "reddit"


class ArcticClient:
    def __init__(self, min_interval: float = 1.2, cache_ttl_days: float = 7.0,
                 max_retries: int = 4, verbose: bool = True):
        self.min_interval = min_interval
        self.cache_ttl = cache_ttl_days * 86400
        self.max_retries = max_retries
        self.verbose = verbose
        self._last = 0.0
        self.last_error = None  # None = last get() succeeded (even if it returned 0 rows)
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def ping(self) -> str | None:
        """None if the archive answers; otherwise a short reason. Checked once up front so an
        unreachable archive (DNS filter, firewall, outage) fails in seconds, not after a
        full crawl of retries."""
        try:
            r = self.session.get(f"{BASE}/subreddits/search", params={"subreddit": "dogs", "limit": 1},
                                 timeout=20)
        except requests.exceptions.SSLError:
            return "TLS 证书校验失败——通常是 DNS 过滤/家长控制/公司网络拦截了这个域名"
        except requests.RequestException as e:
            return f"网络连接失败（{type(e).__name__}）"
        return None if r.status_code < 500 else f"服务端错误 HTTP {r.status_code}"

    def _cache_path(self, url: str) -> Path:
        return CACHE_DIR / (hashlib.sha1(url.encode()).hexdigest() + ".json")

    def _read_cache(self, url: str):
        p = self._cache_path(url)
        if p.exists() and time.time() - p.stat().st_mtime <= self.cache_ttl:
            try:
                return json.loads(p.read_text())
            except Exception:
                return None
        return None

    def _throttle(self):
        wait = self.min_interval - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def get(self, endpoint: str, cache: bool = True, **params):
        """GET /api/<endpoint>. Returns the parsed `data` list, or [] on failure.
        cache=False for listing pages: the caller stores a compact per-subreddit file instead."""
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{BASE}/{endpoint.lstrip('/')}?{urlencode(clean)}"

        self.last_error = None
        cached = self._read_cache(url) if cache else None
        if cached is not None:
            if self.verbose:
                print(f"  [cache] {endpoint} {clean}")
            return cached

        r, attempt, waits, slow = None, -1, 0, False
        while attempt + 1 < self.max_retries:
            attempt += 1
            self._throttle()
            try:
                r = self.session.get(url, timeout=30)
            except requests.RequestException as e:
                if self.verbose:
                    print(f"  [neterr] {e}")
                time.sleep(2 ** attempt)
                continue

            if r.status_code == 200:
                data = r.json().get("data") or []
                if cache:
                    self._cache_path(url).write_text(json.dumps(data))
                if self.verbose:
                    print(f"  [ok]    {endpoint} {clean} → {len(data)}")
                return data

            if r.status_code == 429:
                delay = float(r.headers.get("retry-after", 2 ** (attempt + 2)))
                if self.verbose:
                    print(f"  [429]   sleeping {delay:.0f}s")
                time.sleep(delay)
                continue

            # 422 "Timeout. Maybe slow down a bit" has two meanings. Refused within a moment =
            # per-minute budget spent: sleep until x-ratelimit-reset (shorter waits just get
            # refused again), which doesn't count as a failed attempt. Slow = the query really
            # timed out: back off, and let the caller try smaller date windows.
            if r.status_code == 422 and "timeout" in r.text.lower():
                reset = float(r.headers.get("x-ratelimit-reset") or 0)
                if r.elapsed.total_seconds() < 2 and reset > 0 and waits < 6:
                    waits += 1
                    attempt -= 1
                    if self.verbose:
                        print(f"  [limit] budget spent, waiting {reset:.0f}s")
                    time.sleep(min(reset, 60) + 1)
                    continue
                slow = r.elapsed.total_seconds() >= 2
                delay = 3 * (attempt + 1)
                if self.verbose:
                    print(f"  [422]   query timeout, retry in {delay}s")
                time.sleep(delay)
                continue

            if r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue

            # 400 etc. — surface the error message, don't retry
            msg = ""
            try:
                msg = r.json().get("error", "")
            except Exception:
                pass
            if self.verbose:
                print(f"  [{r.status_code}] {endpoint} {clean} — {msg}")
            self.last_error = f"{r.status_code} {msg}"
            return []

        # "timeout" lets callers fall back to smaller date windows instead of giving up
        self.last_error = "timeout" if slow else "retries exhausted"
        return []
