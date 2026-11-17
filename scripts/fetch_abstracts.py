"""Fetch Crossref/OpenAlex abstracts for DOIs cited in the KB (abstracts only)."""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    API_USER_AGENT,
    KB_DIR,
    LOG_DIR,
    RAW_DIR,
    ensure_dirs,
    estimate_tokens,
    normalize_ws,
    polite_sleep,
    read_jsonl,
    session,
    write_json,
    write_jsonl,
)

DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
JATS_RE = re.compile(r"</?jats:[^>]+>")
TAG_RE = re.compile(r"<[^>]+>")


def normalize_doi(doi: str) -> str:
    doi = doi.strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = doi.strip().rstrip(").,;")
    doi = re.sub(r"/(full|pdf|html)$", "", doi, flags=re.I)
    return doi


def collect_dois(records: list[dict]) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()

    def add(raw: str | None) -> None:
        if not raw:
            return
        for m in DOI_RE.finditer(raw):
            doi = normalize_doi(m.group(0))
            key = doi.lower()
            if key not in seen:
                seen.add(key)
                found.append(doi)

    for rec in records:
        if rec.get("source") == "paper_abstract":
            continue
        for doi in rec.get("cited_dois") or []:
            add(doi)
        add(rec.get("text") or "")
        for ref in rec.get("references") or []:
            add(ref.get("url") or "")
            add(ref.get("title") or "")
    return found


def strip_jats(text: str) -> str:
    text = text.replace("\n", " ")
    # Common Crossref JATS: <jats:title>Abstract</jats:title><jats:p>...</jats:p>
    text = re.sub(r"<jats:title>.*?</jats:title>", " ", text, flags=re.I)
    text = JATS_RE.sub(" ", text)
    text = TAG_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def reconstruct_openalex_abstract(inv: dict | None) -> str | None:
    if not inv or not isinstance(inv, dict):
        return None
    pairs = []
    for word, positions in inv.items():
        for pos in positions:
            pairs.append((pos, word))
    if not pairs:
        return None
    pairs.sort()
    return " ".join(w for _, w in pairs)


def fetch_crossref(doi: str) -> dict | None:
    url = f"https://api.crossref.org/works/{quote(doi, safe='')}"
    polite_sleep()
    resp = session.get(
        url,
        timeout=45,
        headers={"User-Agent": API_USER_AGENT, "Accept": "application/json"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    msg = resp.json().get("message") or {}
    abstract = msg.get("abstract")
    abstract = strip_jats(abstract) if abstract else None
    titles = msg.get("title") or []
    title = titles[0] if titles else None
    authors = []
    for a in msg.get("author") or []:
        name = " ".join(p for p in [a.get("given"), a.get("family")] if p)
        if name:
            authors.append(name)
    date_parts = (
        (msg.get("published-print") or msg.get("published-online") or msg.get("published") or {}).get("date-parts")
        or [[]]
    )
    year = date_parts[0][0] if date_parts and date_parts[0] else None
    licenses = []
    for lic in msg.get("license") or []:
        if lic.get("URL"):
            licenses.append(lic["URL"])
    return {
        "provider": "crossref",
        "doi": msg.get("DOI") or doi,
        "title": title,
        "abstract": abstract,
        "authors": authors,
        "year": year,
        "container": (msg.get("container-title") or [None])[0],
        "licenses": licenses,
        "url": msg.get("URL") or f"https://doi.org/{doi}",
        "raw_path": None,
    }


def fetch_openalex(doi: str) -> dict | None:
    url = f"https://api.openalex.org/works/https://doi.org/{quote(doi)}"
    polite_sleep()
    resp = session.get(
        url,
        timeout=45,
        headers={"User-Agent": API_USER_AGENT, "Accept": "application/json"},
    )
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    msg = resp.json()
    abstract = reconstruct_openalex_abstract(msg.get("abstract_inverted_index"))
    authors = []
    for a in msg.get("authorships") or []:
        name = (a.get("author") or {}).get("display_name")
        if name:
            authors.append(name)
    licenses = []
    loc = msg.get("primary_location") or {}
    if loc.get("license"):
        licenses.append(str(loc["license"]))
    oa = msg.get("open_access") or {}
    if oa.get("oa_status"):
        licenses.append(f"oa_status:{oa['oa_status']}")
    return {
        "provider": "openalex",
        "doi": (msg.get("doi") or f"https://doi.org/{doi}").replace("https://doi.org/", ""),
        "title": msg.get("display_name") or msg.get("title"),
        "abstract": abstract,
        "authors": authors,
        "year": msg.get("publication_year"),
        "container": ((loc.get("source") or {}).get("display_name") if loc else None),
        "licenses": licenses,
        "url": msg.get("doi") or f"https://doi.org/{doi}",
        "raw_path": None,
    }


def to_kb_record(meta: dict) -> dict:
    doi = normalize_doi(meta["doi"])
    authors = meta.get("authors") or []
    abstract = meta.get("abstract") or ""
    lines = [f"# {meta.get('title') or doi}"]
    if authors:
        lines.append(f"**Authors:** {', '.join(authors)}")
    if meta.get("year"):
        lines.append(f"**Year:** {meta['year']}")
    if meta.get("container"):
        lines.append(f"**Venue:** {meta['container']}")
    lines.append(f"**DOI:** {doi}")
    lines.append(f"**URL:** {meta.get('url') or f'https://doi.org/{doi}'}")
    if meta.get("licenses"):
        lines.append("**License / rights (as reported by the API):** " + "; ".join(meta["licenses"]))
    lines.append(
        "Note: this record contains the publisher abstract only, retrieved from "
        f"{meta.get('provider')}. It is not the full text of the paper."
    )
    lines.append("## Abstract")
    lines.append(abstract)
    text = normalize_ws("\n\n".join(lines))
    rec_id = f"paper:{doi.lower()}"
    return {
        "id": rec_id,
        "source": "paper_abstract",
        "url": meta.get("url") or f"https://doi.org/{doi}",
        "title": meta.get("title") or doi,
        "date": str(meta["year"]) if meta.get("year") else None,
        "author": ", ".join(authors) if authors else None,
        "expert": None,
        "states": [],
        "region": None,
        "crop": [],
        "practice": [],
        "tags": [],
        "trust_tier": 2,
        "text": text,
        "doi": doi,
        "abstract": abstract,
        "provider": meta.get("provider"),
        "licenses": meta.get("licenses") or [],
        "container": meta.get("container"),
    }


def fetch_abstracts(records: list[dict] | None = None) -> list[dict]:
    ensure_dirs()
    if records is None:
        records = read_jsonl(KB_DIR / "kb.jsonl")
    dois = collect_dois(records)
    log = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "n_dois": len(dois),
        "results": [],
        "failures": [],
        "no_abstract": [],
    }
    print(f"Fetching abstracts for {len(dois)} unique DOIs", flush=True)
    new_records: list[dict] = []
    RAW_DIR.joinpath("paper_abstract").mkdir(parents=True, exist_ok=True)

    for i, doi in enumerate(dois, 1):
        entry = {"doi": doi, "provider": None, "status": None}
        meta = None
        try:
            meta = fetch_crossref(doi)
            if meta:
                entry["provider"] = "crossref"
                # Save raw-ish summary (not full API blob to keep files small; already logged)
        except Exception as exc:
            entry["crossref_error"] = str(exc)
            meta = None
        if not meta or not meta.get("abstract"):
            try:
                oa = fetch_openalex(doi)
                if oa and (not meta or not meta.get("abstract")):
                    # prefer Crossref metadata but take OpenAlex abstract if needed
                    if meta and oa.get("abstract"):
                        meta["abstract"] = oa["abstract"]
                        meta["provider"] = "crossref+openalex_abstract"
                        if oa.get("licenses"):
                            meta["licenses"] = list(dict.fromkeys((meta.get("licenses") or []) + oa["licenses"]))
                    elif oa:
                        meta = oa
                        entry["provider"] = "openalex"
            except Exception as exc:
                entry["openalex_error"] = str(exc)
        if not meta:
            entry["status"] = "not_found"
            log["failures"].append(entry)
            print(f"  [{i}/{len(dois)}] NOT FOUND {doi}", flush=True)
            continue
        if not meta.get("abstract"):
            entry["status"] = "no_abstract"
            entry["title"] = meta.get("title")
            log["no_abstract"].append(entry)
            print(f"  [{i}/{len(dois)}] NO ABSTRACT {doi} ({meta.get('title')})", flush=True)
            continue
        rec = to_kb_record(meta)
        # persist a compact sidecar
        side = RAW_DIR / "paper_abstract" / (doi.replace("/", "_") + ".json")
        side.write_text(
            __import__("json").dumps(
                {k: meta[k] for k in meta if k != "raw_path"},
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        rec["raw_path"] = str(side)
        new_records.append(rec)
        entry["status"] = "ok"
        entry["title"] = rec["title"]
        entry["provider"] = rec["provider"]
        entry["abstract_chars"] = len(rec["abstract"])
        log["results"].append(entry)
        print(f"  [{i}/{len(dois)}] OK {rec['provider']} {doi}", flush=True)

    # Merge into kb.jsonl (replace any previous paper_abstract rows)
    others = [r for r in records if r.get("source") != "paper_abstract"]
    merged = others + new_records
    write_jsonl(KB_DIR / "kb.jsonl", merged)
    log["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    log["n_abstracts"] = len(new_records)
    write_json(LOG_DIR / "abstracts_log.json", log)
    print(f"Added {len(new_records)} paper_abstract records", flush=True)
    return new_records


if __name__ == "__main__":
    fetch_abstracts()
