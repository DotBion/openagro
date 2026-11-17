"""Parse saved HTML into kb.jsonl records and chunks.jsonl."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Comment, NavigableString, Tag
from markdownify import markdownify as html_to_md

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import (  # noqa: E402
    KB_DIR,
    LOG_DIR,
    RAW_DIR,
    classify_stated_tags,
    ensure_dirs,
    estimate_tokens,
    normalize_ws,
    parse_states_from_text,
    slug_from_url,
    write_json,
    write_jsonl,
)

CTA_HEADING_RE = re.compile(
    r"^(putting this advice into practice|plan your next step|similar questions|"
    r"continue reading|optional expert service|turn program research into|"
    r"explore further|before you buy, ask|next step|find .+ suppliers|"
    r"not sure where to buy|put each result in context)$",
    re.I,
)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)


def load_html(path: Path) -> BeautifulSoup:
    return BeautifulSoup(path.read_text(encoding="utf-8", errors="replace"), "lxml")


def jsonld_blocks(soup: BeautifulSoup) -> list[dict]:
    blocks = []
    for script in soup.find_all("script", type="application/ld+json"):
        raw = script.string or script.get_text() or ""
        raw = raw.strip()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, list):
            blocks.extend([d for d in data if isinstance(d, dict)])
        elif isinstance(data, dict):
            if isinstance(data.get("@graph"), list):
                blocks.extend([d for d in data["@graph"] if isinstance(d, dict)])
            blocks.append(data)
    return blocks


def first_jsonld(blocks: list[dict], *types: str) -> dict | None:
    want = {t.lower() for t in types}
    for b in blocks:
        typ = b.get("@type")
        typ_l = typ.lower() if isinstance(typ, str) else ""
        if typ_l in want:
            return b
        if isinstance(typ, list) and any(str(t).lower() in want for t in typ):
            return b
    return None


def ld_name(node: Any) -> str | None:
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        return node.get("name") or node.get("headline")
    if isinstance(node, list) and node:
        return ld_name(node[0])
    return None


def meta_content(soup: BeautifulSoup, *keys: str) -> str | None:
    for key in keys:
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag and tag.get("content"):
            return tag["content"].strip()
    return None


def extract_dt_map(root: Tag | BeautifulSoup) -> dict[str, str]:
    out = {}
    for dt in root.find_all("dt"):
        label = dt.get_text(" ", strip=True)
        dd = dt.find_next_sibling("dd")
        if not label or dd is None:
            # sometimes wrapped in a div
            parent = dt.parent
            if parent:
                dd = parent.find("dd")
        if dd is not None:
            out[label.rstrip(":").strip()] = dd.get_text(" ", strip=True)
    return out


def stated_pills(root: Tag | BeautifulSoup) -> list[str]:
    pills = []
    for sp in root.find_all("span"):
        cls = " ".join(sp.get("class") or [])
        if "rounded-full" not in cls:
            continue
        text = sp.get_text(" ", strip=True)
        text = re.sub(r"\s+", " ", text).strip()
        if not text or len(text) > 80:
            continue
        if text.lower() in {"verified expert", "independent", "table"}:
            continue
        if re.fullmatch(r"\d+\s+(sources?|observations?|independent\s+sources?)", text, re.I):
            continue
        if re.fullmatch(r"SHE-[A-Z]+-\d{4}-\d+", text):
            continue
        pills.append(text)
    return pills


def strip_noise(tag: Tag) -> None:
    for sel in [
        "script",
        "style",
        "nav",
        "footer",
        "noscript",
        "iframe",
        "svg",
        "button",
        "form",
        '[aria-label="Field management with FieldLog"]',
        ".FieldLogInvitation_card__0KBKK",
    ]:
        for el in tag.select(sel):
            el.decompose()
    for c in tag.find_all(string=lambda x: isinstance(x, Comment)):
        c.extract()
    # Remove CTA / related sections by heading text
    for heading in list(tag.find_all(["h2", "h3"])):
        title = heading.get_text(" ", strip=True)
        if CTA_HEADING_RE.search(title or ""):
            section = heading.find_parent("section") or heading.find_parent("aside") or heading.parent
            if section and section is not tag:
                section.decompose()
            else:
                # remove heading and following siblings until next h2
                for sib in list(heading.find_all_next()):
                    if sib is heading:
                        continue
                    if sib.name in {"h2"} and sib.find_parent() is heading.find_parent():
                        break
                    if heading.parent and sib in heading.parent.descendants:
                        if getattr(sib, "decompose", None):
                            try:
                                sib.decompose()
                            except Exception:
                                pass
                heading.decompose()
    # Share / checking / $249 upsell leftovers
    for p in list(tag.find_all(["p", "div", "section", "aside"])):
        t = p.get_text(" ", strip=True)
        if re.match(r"^(Share|Checking…|Optional expert service)", t, re.I):
            p.decompose()
        elif "Turn program research into an application package" in t:
            p.decompose()


def element_to_markdown(tag: Tag | None) -> str:
    if tag is None:
        return ""
    # Insert separators between adjacent inline badges/links that have no whitespace.
    work = BeautifulSoup(str(tag), "lxml")
    root = work.find("body") or work
    for el in list(root.find_all(["a", "span"])):
        nxt = el.next_sibling
        if isinstance(nxt, Tag) and nxt.name in {"a", "span"}:
            el.insert_after(NavigableString(" · "))
    html = str(root)
    md = html_to_md(html, heading_style="ATX", bullets="-", strip=["img", "picture", "source"])
    md = re.sub(r"\n\s*\n\s*\n+", "\n\n", md)
    md = re.sub(r"(?: · ){2,}", " · ", md)
    return normalize_ws(md)


def find_article(soup: BeautifulSoup) -> Tag | None:
    return soup.find("article") or soup.find("main")


def person_from_ld(node: Any) -> str | None:
    if not node:
        return None
    if isinstance(node, list):
        names = [person_from_ld(x) for x in node]
        return ", ".join(n for n in names if n) or None
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        name = node.get("name")
        job = node.get("jobTitle")
        aff = ld_name(node.get("affiliation"))
        parts = [name] if name else []
        # Keep just the name as author/expert; job/aff go elsewhere if needed
        return name or job or aff
    return None


def extract_rsc_citations(html: str) -> list[dict]:
    """Pull citation objects out of Next.js RSC payloads when the HTML list is collapsed."""
    citations = []
    # Work on the raw HTML where quotes are often backslash-escaped.
    pattern = re.compile(
        r'\\"_type\\":\\"citation\\",'
        r'(?:\\"source\\":\\"(?P<source>.*?)\\")?'
        r'(?:,\\"title\\":\\"(?P<title>.*?)\\")?'
        r'(?:,\\"url\\":\\"(?P<url>.*?)\\")?'
        r'(?:,\\"year\\":\\"(?P<year>.*?)\\")?',
    )
    pattern2 = re.compile(
        r'"_type":"citation",'
        r'(?:"source":"(?P<source>.*?)")?'
        r'(?:,"title":"(?P<title>.*?)")?'
        r'(?:,"url":"(?P<url>.*?)")?'
        r'(?:,"year":"(?P<year>.*?)")?',
    )
    seen = set()
    for pat in (pattern, pattern2):
        for m in pat.finditer(html):
            rec = {k: (v.replace("\\/", "/") if v else None) for k, v in m.groupdict().items()}
            if not rec.get("title") and not rec.get("source"):
                continue
            key = (rec.get("title"), rec.get("url"), rec.get("source"))
            if key in seen:
                continue
            seen.add(key)
            citations.append({k: v for k, v in rec.items() if v})
    return citations


def format_references_md(citations: list[dict]) -> str:
    if not citations:
        return ""
    lines = ["## References", ""]
    for c in citations:
        title = c.get("title") or "Untitled"
        source = c.get("source")
        year = c.get("year")
        url = c.get("url")
        bits = [f"**{title}**"]
        if source:
            bits.append(source)
        if year:
            bits.append(year)
        line = " — ".join(bits)
        if url:
            line += f" — {url}"
        lines.append(f"- {line}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Source-specific parsers
# ---------------------------------------------------------------------------

def parse_she_qa(path: Path, url: str) -> dict:
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = load_html(path)
    blocks = jsonld_blocks(soup)
    article_ld = first_jsonld(blocks, "Article", "ScholarlyArticle") or {}
    qa_ld = first_jsonld(blocks, "QAPage") or {}
    question_ld = qa_ld.get("mainEntity") or {}
    answer_ld = question_ld.get("acceptedAnswer") or {}

    title = (
        article_ld.get("headline")
        or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else None)
        or meta_content(soup, "og:title")
    )
    identifier = article_ld.get("identifier") or answer_ld.get("identifier")
    expert = person_from_ld(article_ld.get("author")) or person_from_ld(answer_ld.get("author"))
    date = article_ld.get("datePublished") or question_ld.get("dateCreated") or answer_ld.get("dateCreated")
    date_modified = article_ld.get("dateModified")
    farmer_question = question_ld.get("text") or question_ld.get("name")
    display_question = question_ld.get("name") or title
    asker = person_from_ld(question_ld.get("author"))

    article = find_article(soup)
    dt_map = extract_dt_map(article or soup)
    location = dt_map.get("Location")
    crop_field = dt_map.get("Crop")
    acres = dt_map.get("Acres")

    # Challenge quote if JSON-LD question missing
    if not farmer_question:
        chal = soup.select_one("#challenge")
        if chal:
            farmer_question = chal.get_text(" ", strip=True)
            farmer_question = re.sub(r"^The challenge\s*", "", farmer_question)
            farmer_question = farmer_question.strip(" “”\"")

    prose = soup.select_one("#solution .prose") or soup.select_one(".prose")
    # Clone before strip
    answer_md = element_to_markdown(prose) if prose else normalize_ws(answer_ld.get("text") or "")

    citations = extract_rsc_citations(html)
    ref_md = format_references_md(citations)

    parts = [f"# {title}" if title else "# Expert answer"]
    meta_lines = []
    if expert:
        meta_lines.append(f"**Expert:** {expert}")
    if identifier:
        meta_lines.append(f"**Identifier:** {identifier}")
    if date:
        meta_lines.append(f"**Published:** {date}")
    if location:
        meta_lines.append(f"**Location (stated):** {location}")
    if crop_field:
        meta_lines.append(f"**Crop (stated):** {crop_field}")
    if acres:
        meta_lines.append(f"**Acres (stated):** {acres}")
    if asker:
        meta_lines.append(f"**Asked by:** {asker}")
    if meta_lines:
        parts.append("\n".join(meta_lines))
    if display_question or farmer_question:
        parts.append("## Question")
        if display_question and farmer_question and display_question.strip() != farmer_question.strip():
            parts.append(display_question)
            parts.append(f"> {farmer_question}")
        else:
            parts.append(farmer_question or display_question)
    parts.append("## Answer")
    parts.append(answer_md)
    if ref_md:
        parts.append(ref_md)
    text = normalize_ws("\n\n".join(p for p in parts if p))

    raw_tags = stated_pills(article or soup)
    if article_ld.get("about"):
        about = ld_name(article_ld["about"])
        if about:
            raw_tags = [about] + raw_tags
    if crop_field:
        raw_tags = [crop_field] + raw_tags
    classified = classify_stated_tags(raw_tags)

    states = parse_states_from_text(location) if location else []
    # Do not parse states from the body — only the stated Location field / location pill.

    rec = {
        "id": identifier or f"she_qa:{slug_from_url(url)}",
        "source": "she_qa",
        "url": url,
        "title": title,
        "date": date,
        "date_modified": date_modified,
        "author": expert,
        "expert": expert,
        "asker": asker,
        "question": farmer_question or display_question,
        "question_title": display_question,
        "states": states,
        "region": location,
        "crop": classified["crop"],
        "practice": classified["practice"],
        "tags": classified["tags"],
        "trust_tier": 1,
        "identifier": identifier,
        "text": text,
        "expert_answer": answer_md,
        "references": citations,
        "acres": acres,
    }
    return rec


def extract_journal_sources(soup: BeautifulSoup) -> list[dict]:
    """Visible numbered 'Sources' list on Journal articles (often client-toggled but present in HTML)."""
    citations = []
    for heading in soup.find_all(["h2", "h3", "p", "button", "span"]):
        label = heading.get_text(" ", strip=True)
        if re.fullmatch(r"Sources(?:\s*\(\s*\d+\s*\))?", label, re.I) or label.lower() == "sources":
            # find an ol/ul nearby
            root = heading.find_parent("section") or heading.parent
            lists = []
            if root:
                lists = root.find_all(["ol", "ul"])
            if not lists:
                sib = heading.find_next(["ol", "ul"])
                if sib:
                    lists = [sib]
            for lst in lists:
                lis = lst.find_all("li")
                items = [li.get_text(" ", strip=True) for li in lis]
                if items and (len(items) >= 3 or any(re.search(r"\(\d{4}\)|doi:|Extension|Journal", i) for i in items)):
                    for li in lis:
                        item = li.get_text(" ", strip=True)
                        item = re.sub(r"^\d+\s*\.\s*", "", item).strip()
                        if not item:
                            continue
                        rec = {"title": item}
                        a = li.find("a", href=True)
                        if a:
                            rec["url"] = a["href"]
                        citations.append(rec)
                    if citations:
                        return citations
    return citations


def parse_she_journal(path: Path, url: str) -> dict:
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = load_html(path)
    blocks = jsonld_blocks(soup)
    article_ld = first_jsonld(blocks, "ScholarlyArticle", "Article", "BlogPosting") or {}

    title = (
        article_ld.get("headline")
        or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else None)
        or meta_content(soup, "og:title")
    )
    identifier = article_ld.get("identifier") or meta_content(soup, "citation_id")
    date = (
        article_ld.get("datePublished")
        or meta_content(soup, "article:published_time", "citation_publication_date")
    )
    date_modified = article_ld.get("dateModified")
    author = person_from_ld(article_ld.get("author")) or meta_content(soup, "citation_author", "author", "article:author")
    keywords = article_ld.get("keywords")
    kw_list: list[str] = []
    if isinstance(keywords, str):
        kw_list = [k.strip() for k in re.split(r"[;,]", keywords) if k.strip()]
    elif isinstance(keywords, list):
        kw_list = [str(k).strip() for k in keywords if k]

    # Prefer the article prose body; fall back to a cleaned article clone.
    prose = soup.select_one("article .prose") or soup.select_one(".prose")
    work = None
    if prose:
        work = BeautifulSoup(str(prose), "lxml")
        work = work.find("body") or work
        strip_noise(work)
    elif soup.find("article"):
        work = BeautifulSoup(str(soup.find("article")), "lxml")
        work = work.find("article") or work.find("body") or work
        # drop header (title/byline already captured) and asides
        for sel in ["header", "aside", "figure"]:
            for el in work.select(sel):
                el.decompose()
        strip_noise(work)
        for h in list(work.find_all(["h2", "h3"])):
            if CTA_HEADING_RE.search(h.get_text(" ", strip=True) or "") or "References" in (h.get_text(" ", strip=True) or ""):
                parent = h.find_parent("section") or h.find_parent("aside")
                if parent:
                    parent.decompose()
    body_md = element_to_markdown(work)

    citations = extract_rsc_citations(html) or extract_journal_sources(soup)
    ref_md = format_references_md(citations)
    if ref_md and "## References" not in body_md and "References & Citation" not in body_md:
        body_md = normalize_ws(body_md + "\n\n" + ref_md)

    parts = [f"# {title}" if title else "# Journal article"]
    meta_lines = []
    if author:
        meta_lines.append(f"**Author:** {author}")
    if identifier:
        meta_lines.append(f"**Identifier:** {identifier}")
    if date:
        meta_lines.append(f"**Published:** {date}")
    if meta_lines:
        parts.append("\n".join(meta_lines))
    parts.append(body_md)
    text = normalize_ws("\n\n".join(p for p in parts if p))

    article = soup.find("article")
    pills = stated_pills(article or soup)
    raw_tags = kw_list + pills
    classified = classify_stated_tags(raw_tags)
    # Journal pages generally do not have a labeled Location field; do not infer states from keywords
    # unless a keyword is exactly a state name.
    state_tags = parse_states_from_text(*[t for t in classified["tags"] if t])
    # Only keep states if the tag itself is a state (not found inside a longer phrase via body)
    states = []
    for t in classified["tags"]:
        st = parse_states_from_text(t)
        # require the whole tag to be a state or nationwide
        if st and t.lower() in {s.lower() for s in st} or t.upper() in {
            "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC","PR"
        }:
            states.extend(st)
    # unique
    seen = set()
    states_u = []
    for s in states:
        if s not in seen:
            states_u.append(s)
            seen.add(s)

    rec = {
        "id": identifier or f"she_journal:{slug_from_url(url)}",
        "source": "she_journal",
        "url": url,
        "title": title,
        "date": date,
        "date_modified": date_modified,
        "author": author if author and author != "Soil Health Exchange" else (author or "Soil Health Exchange"),
        "expert": None,
        "states": states_u,
        "region": None,
        "crop": classified["crop"],
        "practice": classified["practice"],
        "tags": classified["tags"],
        "trust_tier": 3,
        "identifier": identifier,
        "text": text,
        "references": citations,
        "keywords": kw_list,
    }
    return rec


def parse_she_funding(path: Path, url: str) -> dict:
    soup = load_html(path)
    blocks = jsonld_blocks(soup)
    svc = first_jsonld(blocks, "GovernmentService", "Service", "GovernmentOrganization") or {}

    title = (
        svc.get("name")
        or (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else None)
        or meta_content(soup, "og:title")
    )
    article = find_article(soup)
    dt_map = extract_dt_map(article or soup)

    # Funder: JSON-LD provider, or the subtitle immediately under h1
    funder = ld_name((svc.get("provider") if svc else None))
    if not funder and article:
        h1 = article.find("h1")
        if h1:
            sib = h1.find_next_sibling("p")
            if sib:
                funder = sib.get_text(" ", strip=True)

    service_type = svc.get("serviceType")
    if not service_type and article:
        # eyebrow e.g. "Cost-share"
        eye = article.find("p", class_=re.compile(r"uppercase"))
        if eye:
            t = eye.get_text(" ", strip=True)
            if t and len(t) < 40 and t.lower() not in {"what you can get"}:
                service_type = t

    amount_text = None
    if article:
        for p in article.find_all("p"):
            label = p.get_text(" ", strip=True)
            if label.lower() == "what you can get":
                nxt = p.find_next_sibling("p")
                if nxt:
                    amount_text = nxt.get_text(" ", strip=True)
                break

    coverage = dt_map.get("Coverage")
    deadline = dt_map.get("Deadline & timing") or dt_map.get("Deadline") or dt_map.get("Deadline and timing")
    eligibility = dt_map.get("Eligibility")
    who = dt_map.get("Who can apply")
    readiness = dt_map.get("Readiness")

    official_url = svc.get("sameAs")
    if isinstance(official_url, list):
        official_url = official_url[0] if official_url else None
    if not official_url and article:
        link = article.find("a", string=re.compile(r"Visit official program", re.I))
        if link and link.get("href"):
            official_url = urljoin(url, link["href"])
        else:
            for a in article.find_all("a", href=True):
                if "official" in a.get_text(" ", strip=True).lower():
                    official_url = urljoin(url, a["href"])
                    break

    last_verified = None
    if article:
        m = re.search(r"Last verified\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", article.get_text(" ", strip=True))
        if m:
            last_verified = m.group(1)

    area = svc.get("areaServed")
    area_text = ld_name(area) if area else None
    states = parse_states_from_text(coverage, area_text)

    pills = stated_pills(article or soup)
    classified = classify_stated_tags(pills)

    # Build clean markdown from structured fields rather than leftover CTAs
    lines = [f"# {title}" if title else "# Funding program"]
    if funder:
        lines.append(f"**Funder:** {funder}")
    if service_type:
        lines.append(f"**Type:** {service_type}")
    if amount_text:
        lines.append(f"**What you can get:** {amount_text}")
    if readiness:
        lines.append(f"**Readiness:** {readiness}")
    if who:
        lines.append(f"**Who can apply:** {who}")
    if coverage:
        lines.append(f"**Coverage:** {coverage}")
    if deadline:
        lines.append(f"**Deadline & timing:** {deadline}")
    if eligibility:
        lines.append("## Eligibility")
        lines.append(eligibility)
    if classified["tags"]:
        lines.append("**Tags (stated on page):** " + ", ".join(classified["tags"]))
    if official_url:
        lines.append(f"**Official program URL:** {official_url}")
    if last_verified:
        lines.append(f"**Last verified:** {last_verified}")
    text = normalize_ws("\n\n".join(lines))

    rec = {
        "id": f"she_funding:{slug_from_url(url).replace('funding_','')}",
        "source": "she_funding",
        "url": url,
        "title": title,
        "date": None,
        "last_verified": last_verified,
        "author": None,
        "expert": None,
        "states": states,
        "region": coverage or area_text,
        "crop": classified["crop"],
        "practice": classified["practice"],
        "tags": classified["tags"],
        "trust_tier": 4,
        "text": text,
        "funder": funder,
        "amount": amount_text,
        "cost_share": service_type,
        "deadline": deadline,
        "eligibility_text": eligibility,
        "application_url": official_url,
        "who_can_apply": who,
        "readiness": readiness,
        "coverage": coverage,
    }
    return rec


def parse_farmdeck_product(path: Path, url: str) -> dict:
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = load_html(path)
    title = (
        (soup.find("h1").get_text(" ", strip=True) if soup.find("h1") else None)
        or meta_content(soup, "og:title")
    )
    description = meta_content(soup, "og:description", "description")

    # Category from URL / breadcrumb-like first words
    from urllib.parse import urlparse

    parts = [p for p in urlparse(url).path.split("/") if p]
    category = parts[1].replace("-", " ") if len(parts) >= 3 else None
    # Humanize from visible first line if present
    article = find_article(soup)

    sources_checked = None
    dt_map = extract_dt_map(article or soup)
    if "Sources checked" in dt_map:
        sources_checked = dt_map["Sources checked"]
        # Often "September 26, 2026 . Every figure..."
        m = re.search(r"([A-Za-z]+\s+\d{1,2},\s+\d{4})", sources_checked)
        last_verified = m.group(1) if m else sources_checked
    else:
        last_verified = None
        m = re.search(r"Sources checked\s+([A-Za-z]+\s+\d{1,2},\s+\d{4})", (article or soup).get_text(" ", strip=True))
        if m:
            last_verified = m.group(1)

    dois = []
    seen = set()
    for m in DOI_RE.finditer(html):
        doi = m.group(0).rstrip(").,;")
        # trim trailing HTML
        doi = doi.split("&")[0].split("<")[0]
        key = doi.lower()
        if key not in seen and "jswc" in doi or DOI_RE.fullmatch(doi):
            # normalize
            if key not in seen:
                dois.append(doi)
                seen.add(key)

    # Prefer DOI links
    doi_links = []
    if article:
        for a in article.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"(?:doi\.org/|doi:\s*)(10\.\d{4,9}/[-._;()/:A-Z0-9]+)", href, re.I)
            if not m and href.lower().startswith("doi:"):
                m = DOI_RE.search(href)
            if m:
                doi = m.group(1) if m.lastindex else m.group(0)
                doi = doi.rstrip(").,;")
                if doi.lower() not in seen:
                    dois.append(doi)
                    seen.add(doi.lower())
                doi_links.append({"doi": doi, "title": a.get_text(" ", strip=True), "url": href})

    work = None
    if article:
        work = BeautifulSoup(str(article), "lxml")
        work = work.find("article") or work.find("body") or work
        strip_noise(work)
        # Remove supplier CTA / "send a correction" blocks at the end if clearly CTA
        for h in list(work.find_all(["h2", "h3"])):
            if CTA_HEADING_RE.search((h.get_text(" ", strip=True) or "").strip()):
                parent = h.find_parent("section") or h.find_parent("aside")
                if parent:
                    parent.decompose()
    body_md = element_to_markdown(work)
    # Drop leftover "Find ... suppliers" tails
    body_md = re.split(r"\n(?=Find .+ suppliers|Not sure where to buy it)", body_md, maxsplit=1)[0]
    body_md = normalize_ws(body_md)

    parts = [f"# {title}" if title else "# Product profile"]
    meta_lines = []
    if category:
        meta_lines.append(f"**Category:** {category}")
    if last_verified:
        meta_lines.append(f"**Sources checked:** {last_verified}")
    if dois:
        meta_lines.append("**Cited DOIs:** " + ", ".join(dois))
    if meta_lines:
        parts.append("\n".join(meta_lines))
    if description:
        parts.append(description)
    parts.append(body_md)
    text = normalize_ws("\n\n".join(p for p in parts if p))

    pills = stated_pills(article or soup)
    structured_tags = []
    root = article or soup
    for sp in root.find_all("span"):
        cls = " ".join(sp.get("class") or [])
        if any(k in cls for k in ("pev-classification", "pev-tag", "pev-cat")):
            t = sp.get_text(" ", strip=True)
            if t and len(t) < 80:
                structured_tags.append(t)
    raw_tags = ([category] if category else []) + structured_tags + pills
    classified = classify_stated_tags(raw_tags)

    rec = {
        "id": f"farmdeck:{slug_from_url(url).replace('products_','')}",
        "source": "farmdeck_product",
        "url": url,
        "title": title,
        "date": None,
        "last_verified": last_verified,
        "author": "FarmDeck",
        "expert": None,
        "states": [],  # only if a state is a stated tag
        "region": None,
        "crop": classified["crop"],
        "practice": classified["practice"],
        "tags": classified["tags"],
        "trust_tier": 4,
        "text": text,
        "category": category,
        "cited_dois": dois,
        "description": description,
        "sources_checked": sources_checked,
    }
    # If a stated tag is a state, record it
    for t in classified["tags"]:
        st = parse_states_from_text(t)
        if st and (t.lower() in {s.lower() for s in st} or t.upper() in US_ABBRS):
            rec["states"] = list(dict.fromkeys(rec["states"] + st))
    return rec


US_ABBRS = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC","PR"
}


PARSERS = {
    "she_qa": parse_she_qa,
    "she_journal": parse_she_journal,
    "she_funding": parse_she_funding,
    "farmdeck_product": parse_farmdeck_product,
}


def infer_url(source: str, path: Path) -> str:
    # Reconstruct from filename if needed; prefer og:url later.
    slug = path.stem
    if source == "she_qa":
        # slug may be hub_foo
        rest = re.sub(r"^hub_", "", slug)
        return f"https://soilhealthexchange.com/hub/{rest}"
    if source == "she_journal":
        rest = re.sub(r"^blog_", "", slug)
        return f"https://soilhealthexchange.com/blog/{rest}"
    if source == "she_funding":
        rest = re.sub(r"^funding_", "", slug)
        return f"https://soilhealthexchange.com/funding/{rest}"
    if source == "farmdeck_product":
        rest = re.sub(r"^products_", "", slug)
        rest = rest.replace("_", "/", 1) if "_" in rest else rest
        # filename uses underscore for all slashes: products_cover-crops_cereal-rye-cover-crop
        parts = slug.split("_")
        if parts[0] == "products":
            rest = "/".join(parts[1:])
        return f"https://farmdeck.co/products/{rest}"
    return f"file://{path}"


def url_from_soup_or_name(source: str, path: Path, soup: BeautifulSoup) -> str:
    og = soup.find("meta", attrs={"property": "og:url"})
    if og and og.get("content"):
        return og["content"].strip()
    return infer_url(source, path)


def parse_all() -> list[dict]:
    ensure_dirs()
    records: list[dict] = []
    failures: list[dict] = []
    for source, parser in PARSERS.items():
        folder = RAW_DIR / source
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.html")):
            soup = load_html(path)
            url = url_from_soup_or_name(source, path, soup)
            try:
                rec = parser(path, url)
                rec["raw_path"] = str(path)
                records.append(rec)
            except Exception as exc:
                failures.append({"source": source, "path": str(path), "url": url, "error": str(exc)})
                print(f"PARSE FAIL {path}: {exc}", flush=True)
    write_jsonl(KB_DIR / "kb.jsonl", records)
    write_json(LOG_DIR / "parse_log.json", {
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "records": len(records),
        "failures": failures,
        "by_source": {s: sum(1 for r in records if r["source"] == s) for s in PARSERS},
    })
    print(f"Wrote {len(records)} records to kb.jsonl; {len(failures)} parse failures", flush=True)
    return records


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9“\"'])", text)
    return [p for p in parts if p.strip()]


def _force_windows(text: str, max_tok: int) -> list[str]:
    """Last-resort split by words so no piece exceeds max_tok."""
    words = text.split()
    if not words:
        return []
    out, buf = [], []
    for w in words:
        trial = (" ".join(buf + [w])).strip()
        if buf and estimate_tokens(trial) > max_tok:
            out.append(" ".join(buf))
            buf = [w]
        else:
            buf.append(w)
    if buf:
        out.append(" ".join(buf))
    return out


def split_to_max(text: str, max_tok: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if estimate_tokens(text) <= max_tok:
        return [text]
    # Prefer paragraph breaks, then markdown list items, then sentences, then words.
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    if len(paras) == 1:
        # Maybe a long list without blank lines
        items = re.split(r"(?=\n[-*] |\n\d+\. )", paras[0])
        paras = [i.strip() for i in items if i.strip()] or paras
    pieces: list[str] = []
    acc = ""
    for p in paras:
        if estimate_tokens(p) > max_tok:
            if acc:
                pieces.append(acc)
                acc = ""
            if estimate_tokens(p) <= max_tok:
                pieces.append(p)
                continue
            sent_acc = ""
            for s in split_sentences(p):
                trial_s = (sent_acc + " " + s).strip() if sent_acc else s
                if estimate_tokens(trial_s) <= max_tok:
                    sent_acc = trial_s
                else:
                    if sent_acc:
                        pieces.append(sent_acc)
                    if estimate_tokens(s) <= max_tok:
                        sent_acc = s
                    else:
                        pieces.extend(_force_windows(s, max_tok))
                        sent_acc = ""
            if sent_acc:
                acc = sent_acc
            continue
        trial = (acc + "\n\n" + p).strip() if acc else p
        if estimate_tokens(trial) <= max_tok:
            acc = trial
        else:
            if acc:
                pieces.append(acc)
            acc = p
    if acc:
        pieces.append(acc)
    # Final guard
    flat: list[str] = []
    for p in pieces:
        if estimate_tokens(p) <= max_tok:
            flat.append(p)
        else:
            flat.extend(_force_windows(p, max_tok))
    return flat


def pack_chunks(pieces: list[str], min_tok: int, max_tok: int) -> list[str]:
    merged: list[str] = []
    for p in pieces:
        p = p.strip()
        if not p:
            continue
        if not merged:
            merged.append(p)
            continue
        # Prefer merging undersized tails, and never leave a heading-only stub.
        undersized = estimate_tokens(merged[-1]) < min_tok or re.fullmatch(r"#{1,6} .+", merged[-1].strip())
        trial = merged[-1] + "\n\n" + p
        if undersized and estimate_tokens(trial) <= max_tok:
            merged[-1] = trial
        elif estimate_tokens(merged[-1]) < min_tok and estimate_tokens(trial) <= max_tok + 80:
            # Slight overflow is better than a 3-token leftover.
            merged[-1] = trial
        else:
            merged.append(p)
    # If the last chunk is tiny, fold it into the previous even if slightly over.
    if len(merged) >= 2 and estimate_tokens(merged[-1]) < min_tok:
        trial = merged[-2] + "\n\n" + merged[-1]
        if estimate_tokens(trial) <= max_tok + 80:
            merged[-2] = trial
            merged.pop()
    return merged


def chunk_record(rec: dict, target: int = 400, min_tok: int = 300, max_tok: int = 500) -> list[dict]:
    text = rec.get("text") or ""
    # Split on markdown headings, but keep each heading with the text that follows it.
    blocks: list[str] = []
    current_h = rec.get("title") or rec.get("id")
    buf: list[str] = []
    for line in text.splitlines():
        if re.match(r"^#{1,3} ", line) and buf:
            blocks.append("\n".join(buf).strip())
            buf = [line]
        else:
            buf.append(line)
    if buf:
        blocks.append("\n".join(buf).strip())

    packed: list[str] = []
    acc = ""
    for body in blocks:
        body = body.strip()
        if not body:
            continue
        if estimate_tokens(body) > max_tok:
            if acc:
                packed.append(acc)
                acc = ""
            packed.extend(split_to_max(body, max_tok))
            continue
        trial = (acc + "\n\n" + body).strip() if acc else body
        if estimate_tokens(trial) <= max_tok:
            acc = trial
        else:
            if acc:
                packed.append(acc)
            acc = body
    if acc:
        packed.append(acc)

    pieces: list[str] = []
    for body in packed:
        pieces.extend(split_to_max(body, max_tok))
    merged = pack_chunks(pieces, min_tok, max_tok)

    chunks = []
    parent = rec["id"]
    for i, body in enumerate(merged, 1):
        chunk = {
            "id": f"{parent}::c{i}",
            "parent_id": parent,
            "chunk_index": i,
            "n_chunks": len(merged),
            "source": rec.get("source"),
            "url": rec.get("url"),
            "title": rec.get("title"),
            "date": rec.get("date") or rec.get("last_verified"),
            "author": rec.get("author") or rec.get("expert"),
            "states": rec.get("states") or [],
            "region": rec.get("region"),
            "crop": rec.get("crop") or [],
            "practice": rec.get("practice") or [],
            "trust_tier": rec.get("trust_tier"),
            "token_estimate": estimate_tokens(body),
            "text": body,
        }
        chunks.append(chunk)
    return chunks



def chunk_all(records: list[dict] | None = None) -> list[dict]:
    ensure_dirs()
    if records is None:
        from common import read_jsonl

        records = read_jsonl(KB_DIR / "kb.jsonl")
    chunks: list[dict] = []
    for rec in records:
        chunks.extend(chunk_record(rec))
    write_jsonl(KB_DIR / "chunks.jsonl", chunks)
    toks = [c["token_estimate"] for c in chunks]
    stats = {
        "n_chunks": len(chunks),
        "n_parents": len({c["parent_id"] for c in chunks}),
        "token_min": min(toks) if toks else 0,
        "token_max": max(toks) if toks else 0,
        "token_avg": round(sum(toks) / len(toks), 1) if toks else 0,
        "outside_300_500": sum(1 for t in toks if t < 300 or t > 500),
    }
    write_json(LOG_DIR / "chunk_log.json", stats)
    print(f"Wrote {len(chunks)} chunks; token stats {stats}", flush=True)
    return chunks


if __name__ == "__main__":
    recs = parse_all()
    chunk_all(recs)
