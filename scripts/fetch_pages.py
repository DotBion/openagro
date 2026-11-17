"""Discover public SHE and FarmDeck URLs and fetch raw HTML locally."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Allow running as a script from any cwd
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bs4 import BeautifulSoup  # noqa: E402

from common import (  # noqa: E402
    BLOG_LISTING_SLUGS,
    FARMDECK_CATEGORY_SLUGS,
    FARMDECK_ORIGIN,
    FUNDING_LISTING_SLUGS,
    LOG_DIR,
    RAW_DIR,
    SHE_ORIGIN,
    SNAPSHOT_DIR,
    canonical_url,
    ensure_dirs,
    http_get,
    slug_from_url,
    write_json,
)

try:
    from lxml import etree
except ImportError:  # pragma: no cover
    etree = None


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def parse_sitemap(xml_text: str) -> list[str]:
    urls: list[str] = []
    if etree is not None:
        try:
            root = etree.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            for loc in root.xpath("//sm:loc", namespaces=ns):
                if loc.text:
                    urls.append(loc.text.strip())
            if urls:
                return urls
        except Exception:
            pass
    # Fallback
    import re

    return [u.strip() for u in re.findall(r"<loc>(.*?)</loc>", xml_text)]


def fetch_sitemap(url: str, dest_name: str) -> list[str]:
    resp = http_get(url, accept="application/xml,text/xml,text/plain,*/*")
    resp.raise_for_status()
    dest = RAW_DIR / "sitemaps" / dest_name
    dest.write_text(resp.text, encoding="utf-8")
    urls = [canonical_url(u) for u in parse_sitemap(resp.text)]
    return urls


def load_snapshot_sitemap(filename: str) -> list[str]:
    path = SNAPSHOT_DIR / filename
    if not path.exists():
        return []
    return [canonical_url(u) for u in parse_sitemap(path.read_text(encoding="utf-8", errors="replace"))]


def classify_she(url: str) -> tuple[str, str] | None:
    """Return (source, slug) or None if not a target document."""
    url = canonical_url(url)
    if not url.startswith(SHE_ORIGIN):
        return None
    path = url[len(SHE_ORIGIN) :]
    parts = [p for p in path.split("/") if p]
    if not parts:
        return None
    if parts[0] == "hub":
        if len(parts) == 2:
            return "she_qa", parts[1]
        return None
    if parts[0] == "blog":
        if len(parts) == 2 and parts[1] not in BLOG_LISTING_SLUGS:
            return "she_journal", parts[1]
        return None
    if parts[0] == "funding":
        if len(parts) == 2 and parts[1] not in FUNDING_LISTING_SLUGS:
            return "she_funding", parts[1]
        return None
    return None


def classify_farmdeck(url: str) -> tuple[str, str] | None:
    url = canonical_url(url)
    if "farmdeck.co" not in url:
        return None
    # normalize origin
    path = urlparse_path(url)
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "products" and parts[1] not in {"",}:
        # /products/{category}/{slug}
        if parts[1] in FARMDECK_CATEGORY_SLUGS or True:
            if len(parts) == 3:
                return "farmdeck_product", f"{parts[1]}/{parts[2]}"
    return None


def urlparse_path(url: str) -> str:
    from urllib.parse import urlparse

    return urlparse(url).path


def is_farmdeck_product(url: str) -> bool:
    path = urlparse_path(canonical_url(url))
    parts = [p for p in path.split("/") if p]
    if len(parts) != 3 or parts[0] != "products":
        return False
    # Exclude category indexes and obvious non-product pages
    if parts[1] in FARMDECK_CATEGORY_SLUGS and parts[2] in FARMDECK_CATEGORY_SLUGS:
        return False
    if parts[2] in {"demo"} and parts[1] == "products":
        return False
    return True


def discover_urls() -> dict:
    ensure_dirs()
    report: dict = {"started_at": _now_iso(), "sources": {}, "errors": []}

    she_live: list[str] = []
    farm_live: list[str] = []
    try:
        she_live = fetch_sitemap(f"{SHE_ORIGIN}/sitemap.xml", "soilhealthexchange.com.sitemap.xml")
    except Exception as exc:
        report["errors"].append({"sitemap": f"{SHE_ORIGIN}/sitemap.xml", "error": str(exc)})
    try:
        farm_live = fetch_sitemap(f"{FARMDECK_ORIGIN}/sitemap.xml", "farmdeck.co.sitemap.xml")
    except Exception as exc:
        report["errors"].append({"sitemap": f"{FARMDECK_ORIGIN}/sitemap.xml", "error": str(exc)})

    she_snap = load_snapshot_sitemap("soilhealthexchange.com.sitemap.xml")
    farm_snap = load_snapshot_sitemap("www.farmdeck.co.sitemap.xml")

    she_all = sorted(set(she_live) | set(she_snap))
    farm_all = sorted(set(farm_live) | set(farm_snap))

    targets: dict[str, list[dict]] = {
        "she_qa": [],
        "she_journal": [],
        "she_funding": [],
        "farmdeck_product": [],
    }
    for url in she_all:
        classified = classify_she(url)
        if classified:
            source, slug = classified
            targets[source].append({"url": url, "slug": slug, "in_live_sitemap": url in she_live, "in_snapshot_sitemap": url in she_snap})

    for url in farm_all:
        if is_farmdeck_product(url):
            # prefer farmdeck.co canonical
            url_c = canonical_url(url).replace("https://www.farmdeck.co", FARMDECK_ORIGIN)
            path_parts = [p for p in urlparse_path(url_c).split("/") if p]
            slug = "/".join(path_parts[1:])
            targets["farmdeck_product"].append(
                {
                    "url": url_c,
                    "slug": slug,
                    "in_live_sitemap": canonical_url(url) in farm_live or url_c in farm_live,
                    "in_snapshot_sitemap": canonical_url(url) in farm_snap or url_c in farm_snap,
                }
            )

    # Dedupe farmdeck
    seen = set()
    deduped = []
    for item in targets["farmdeck_product"]:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduped.append(item)
    targets["farmdeck_product"] = deduped

    report["sources"] = {
        k: {
            "count": len(v),
            "live_sitemap_count": sum(1 for i in v if i["in_live_sitemap"]),
            "snapshot_only": [i["url"] for i in v if i["in_snapshot_sitemap"] and not i["in_live_sitemap"]],
            "live_only": [i["url"] for i in v if i["in_live_sitemap"] and not i["in_snapshot_sitemap"]],
        }
        for k, v in targets.items()
    }
    report["she_sitemap_live_count"] = len(she_live)
    report["she_sitemap_snapshot_count"] = len(she_snap)
    report["farmdeck_sitemap_live_count"] = len(farm_live)
    report["farmdeck_sitemap_snapshot_count"] = len(farm_snap)
    report["targets"] = targets
    write_json(LOG_DIR / "url_discovery.json", report)
    return report


def raw_path_for(source: str, url: str) -> Path:
    return RAW_DIR / source / f"{slug_from_url(url)}.html"


def fetch_all(force: bool = False) -> dict:
    discovery = discover_urls()
    targets = discovery["targets"]
    results = {
        "started_at": _now_iso(),
        "force": force,
        "fetched": [],
        "skipped_existing": [],
        "failures": [],
        "counts": {},
    }
    all_items: list[tuple[str, dict]] = []
    for source, items in targets.items():
        for item in items:
            all_items.append((source, item))

    total = len(all_items)
    print(f"Discovered {total} target pages: " + ", ".join(f"{k}={len(v)}" for k, v in targets.items()), flush=True)

    for i, (source, item) in enumerate(all_items, 1):
        url = item["url"]
        dest = raw_path_for(source, url)
        if dest.exists() and dest.stat().st_size > 500 and not force:
            results["skipped_existing"].append({"source": source, "url": url, "path": str(dest)})
            if i % 50 == 0:
                print(f"  [{i}/{total}] skip existing {url}", flush=True)
            continue
        try:
            t0 = time.monotonic()
            resp = http_get(url)
            elapsed = time.monotonic() - t0
            if resp.status_code != 200:
                results["failures"].append(
                    {
                        "source": source,
                        "url": url,
                        "status": resp.status_code,
                        "error": f"HTTP {resp.status_code}",
                    }
                )
                print(f"  [{i}/{total}] FAIL {resp.status_code} {url}", flush=True)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(resp.content)
            results["fetched"].append(
                {
                    "source": source,
                    "url": url,
                    "path": str(dest),
                    "status": resp.status_code,
                    "bytes": len(resp.content),
                    "final_url": resp.url,
                    "elapsed_sec": round(elapsed, 3),
                }
            )
            print(f"  [{i}/{total}] {resp.status_code} {len(resp.content):,}B {url}", flush=True)
        except Exception as exc:
            results["failures"].append({"source": source, "url": url, "error": str(exc)})
            print(f"  [{i}/{total}] ERROR {url}: {exc}", flush=True)

    # counts
    from collections import Counter

    c = Counter()
    have = Counter()
    for source, item in all_items:
        c[source] += 1
        if raw_path_for(source, item["url"]).exists():
            have[source] += 1
    results["counts"] = {
        source: {
            "discovered": c[source],
            "on_disk": have[source],
            "fetched_this_run": sum(1 for r in results["fetched"] if r["source"] == source),
            "failed": sum(1 for r in results["failures"] if r["source"] == source),
        }
        for source in targets
    }
    results["finished_at"] = _now_iso()
    write_json(LOG_DIR / "fetch_log.json", results)
    print("Fetch complete:", results["counts"], "failures:", len(results["failures"]), flush=True)
    return results


if __name__ == "__main__":
    force = "--force" in sys.argv
    fetch_all(force=force)
