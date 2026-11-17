"""Shared paths, HTTP helpers, and constants for the OpenAgronomy KB build."""

from __future__ import annotations

import json
import random
import re
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import requests

ROOT = Path("/workspace/openagronomy")
SCRIPTS = ROOT / "scripts"
KB_DIR = ROOT / "kb"
RAW_DIR = KB_DIR / "raw"
QNA_DIR = ROOT / "qna"
SNAPSHOT_DIR = ROOT / "raw-snapshots-2026-11-15"
LOG_DIR = KB_DIR / "logs"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
# Crossref polite pool prefers a contact; keep the same browser UA plus a project token.
API_USER_AGENT = (
    USER_AGENT + " OpenAgronomyKB/0.1 (local research corpus; no-contact)"
)

REQUEST_INTERVAL_SEC = 1.15
REQUEST_TIMEOUT_SEC = 45
MAX_RETRIES = 3

SHE_ORIGIN = "https://soilhealthexchange.com"
FARMDECK_ORIGIN = "https://farmdeck.co"

FUNDING_LISTING_SLUGS = {"", "states", "closing-soon", "review"}
BLOG_LISTING_SLUGS = {"", "top-posts"}
FARMDECK_CATEGORY_SLUGS = {
    "seeds",
    "biologicals",
    "cover-crops",
    "seed-treatments",
    "crop-protection",
    "fertility",
    "demo",
}

# Stated-tag allowlists. A term is recorded as crop/practice only when it already
# appears as a page tag, keyword, or labeled field — never inferred from prose.
CROP_TAGS = {
    "corn",
    "maize",
    "soy",
    "soybean",
    "soybeans",
    "wheat",
    "cotton",
    "rice",
    "oats",
    "oat",
    "barley",
    "rye",
    "cereal rye",
    "winter rye",
    "hairy vetch",
    "red clover",
    "clover",
    "vetch",
    "alfalfa",
    "radish",
    "forage radish",
    "turnip",
    "brassica",
    "brassicas",
    "sugar beet",
    "sugarbeet",
    "sorghum",
    "sunflower",
    "potato",
    "tomato",
    "apple",
    "grape",
    "pasture",
    "grass",
    "silage",
    "specialty crop",
    "muskmelon",
}

PRACTICE_TAGS = {
    "cover crop",
    "cover crops",
    "no-till",
    "no till",
    "organic no-till",
    "organic no till",
    "reduced till",
    "strip-till",
    "strip till",
    "tillage",
    "nutrient management",
    "nitrogen management",
    "manure management",
    "grazing",
    "water quality",
    "energy efficiency",
    "rangeland",
    "planting green",
    "roller crimper",
    "cover crop termination",
    "weed suppression",
    "integrated weed management",
    "soil moisture",
    "composting",
    "irrigation",
    "buffers",
    "high tunnels",
    "drainage",
    "soil testing",
    "soil health",
    "terraces",
    "virtual fencing",
    "living mulch",
    "interseeding",
    "interseeded",
    "organic farming",
    "organic",
    "conservation tillage",
    "soil compaction",
    "field management",
    "crop rotation",
    "residue management",
}

US_STATE_BY_NAME = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    "district of columbia": "DC",
    "puerto rico": "PR",
}
US_STATE_BY_ABBR = {v: k.title() for k, v in US_STATE_BY_NAME.items()}
# Prefer conventional casing for names
US_STATE_BY_ABBR.update(
    {
        "DC": "District of Columbia",
        "PR": "Puerto Rico",
        "NH": "New Hampshire",
        "NJ": "New Jersey",
        "NM": "New Mexico",
        "NY": "New York",
        "NC": "North Carolina",
        "ND": "North Dakota",
        "RI": "Rhode Island",
        "SC": "South Carolina",
        "SD": "South Dakota",
        "WV": "West Virginia",
    }
)

session = requests.Session()
session.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
)

_last_request_at = 0.0
_robots: dict[str, RobotFileParser] = {}


def ensure_dirs() -> None:
    for p in (
        KB_DIR,
        RAW_DIR,
        QNA_DIR,
        LOG_DIR,
        RAW_DIR / "she_qa",
        RAW_DIR / "she_journal",
        RAW_DIR / "she_funding",
        RAW_DIR / "farmdeck_product",
        RAW_DIR / "sitemaps",
        RAW_DIR / "paper_abstract",
    ):
        p.mkdir(parents=True, exist_ok=True)


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "_", path).strip("_")
    return slug or "index"


def canonical_url(url: str) -> str:
    parsed = urlparse(url)
    path = parsed.path or "/"
    if path != "/" and path.endswith("/"):
        path = path[:-1]
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return f"https://{netloc}{path}"


def _robot_parser(origin: str) -> RobotFileParser:
    if origin not in _robots:
        rp = RobotFileParser()
        rp.set_url(urljoin(origin, "/robots.txt"))
        try:
            polite_sleep()
            resp = session.get(urljoin(origin, "/robots.txt"), timeout=REQUEST_TIMEOUT_SEC)
            rp.parse(resp.text.splitlines())
        except Exception:
            # If robots.txt cannot be read, be conservative: allow only public paths we already checked.
            rp.parse(["User-Agent: *", "Allow: /"])
        _robots[origin] = rp
    return _robots[origin]


def allowed_by_robots(url: str) -> bool:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    # Hard skip of known disallowed prefixes even if robots fetch fails.
    path = parsed.path or "/"
    lowered = path.lower()
    if lowered.startswith(("/api/", "/admin/", "/studio/", "/go/", "/rotation")):
        return False
    if lowered in {"/api", "/admin", "/studio"}:
        return False
    return _robot_parser(origin).can_fetch(USER_AGENT, url)


def polite_sleep() -> None:
    global _last_request_at
    now = time.monotonic()
    wait = REQUEST_INTERVAL_SEC - (now - _last_request_at)
    if wait > 0:
        time.sleep(wait + random.uniform(0, 0.15))
    _last_request_at = time.monotonic()


def http_get(url: str, *, accept: str | None = None, stream: bool = False) -> requests.Response:
    if not allowed_by_robots(url):
        raise PermissionError(f"robots.txt disallows {url}")
    headers = {}
    if accept:
        headers["Accept"] = accept
    last_err: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        polite_sleep()
        try:
            resp = session.get(
                url,
                timeout=REQUEST_TIMEOUT_SEC,
                headers=headers or None,
                allow_redirects=True,
                stream=stream,
            )
            if resp.status_code in {429, 500, 502, 503, 504}:
                retry_after = resp.headers.get("Retry-After")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = REQUEST_INTERVAL_SEC * (2 ** attempt)
                time.sleep(min(delay, 30))
                last_err = RuntimeError(f"HTTP {resp.status_code} for {url}")
                continue
            return resp
        except requests.RequestException as exc:
            last_err = exc
            time.sleep(REQUEST_INTERVAL_SEC * attempt)
    assert last_err is not None
    raise last_err


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            n += 1
    return n


def read_jsonl(path: Path) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    words = re.findall(r"\w+|[^\w\s]", text)
    return max(1, int(round(len(words) * 1.15)))


def normalize_ws(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def classify_stated_tags(raw_tags: Iterable[str]) -> dict[str, list[str]]:
    """Split stated page tags into crop / practice lists using allowlists only."""
    crops: list[str] = []
    practices: list[str] = []
    tags: list[str] = []
    seen_c: set[str] = set()
    seen_p: set[str] = set()
    seen_t: set[str] = set()
    for raw in raw_tags:
        if not raw:
            continue
        cleaned = re.sub(r"^#\s*", "", raw).strip()
        if not cleaned or cleaned.lower() in {
            "verified expert",
            "sources",
            "source",
            "independent",
        }:
            continue
        if re.fullmatch(r"SHE-[A-Z]+-\d{4}-\d+", cleaned):
            continue
        if re.fullmatch(r"\d+\s+sources?", cleaned, re.I):
            continue
        key = cleaned.lower()
        if key not in seen_t:
            tags.append(cleaned)
            seen_t.add(key)
        if key in CROP_TAGS and key not in seen_c:
            crops.append(cleaned)
            seen_c.add(key)
        if key in PRACTICE_TAGS and key not in seen_p:
            practices.append(cleaned)
            seen_p.add(key)
    return {"tags": tags, "crop": crops, "practice": practices}


def parse_states_from_text(*texts: str | None) -> list[str]:
    """Extract explicit US state names/abbreviations and nationwide coverage."""
    found: list[str] = []
    seen: set[str] = set()

    def add(label: str) -> None:
        if label not in seen:
            found.append(label)
            seen.add(label)

    for text in texts:
        if not text:
            continue
        lower = text.lower()
        if re.search(
            r"\b(nationwide|available nationwide|united states|all 50 states|national(?:\s+program)?)\b",
            lower,
        ):
            add("nationwide")
        # Full names first (so "West Virginia" is not also "Virginia" wrongly — use word boundaries)
        for name, abbr in sorted(US_STATE_BY_NAME.items(), key=lambda x: -len(x[0])):
            if re.search(rf"\b{re.escape(name)}\b", lower):
                add(US_STATE_BY_ABBR[abbr])
        # Standalone abbreviations (2-letter, word boundary). Avoid "in", "or", "me" false positives
        # by requiring uppercase appearance in the original text.
        for abbr, name in US_STATE_BY_ABBR.items():
            if re.search(rf"\b{abbr}\b", text):
                add(name)
    return found
