"""Where runs write their files. Never inside the skill folder — the skill may be read-only
and shared. Default: ./rpr-output under the current directory; override with RPR_WORKDIR."""
import os
from pathlib import Path

WORK = Path(os.environ.get("RPR_WORKDIR") or (Path.cwd() / "rpr-output")).expanduser().resolve()
DATA = WORK / "data"        # raw Reddit corpus per keyword
CACHE = WORK / "cache"      # HTTP caches (Reddit archive 7d, Amazon 7d)
REPORTS = WORK / "reports"  # report_data_<slug>.json + <slug>.html


def slugify(keyword: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", keyword.lower()).strip("-")
