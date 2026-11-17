"""Chat tools: search_evidence, find_funding, get_weather.

find_funding never asserts eligibility. Weather spray windows are meteorological only.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests

from rag.features import QueryFeatures, chunk_is_nationwide, detect_states, state_match
from rag.retrieve import Retriever

# Boone County, Iowa (approximate geographic center). Used when the user does not give a point.
BOONE_COUNTY_IA = {
    "name": "Boone County, Iowa",
    "lat": 42.0365,
    "lon": -93.9313,
    "timezone": "America/Chicago",
}

TODAY = date(2026, 11, 17)
OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
# ~3–15 mph in km/h, a common meteorological window. NOT a label or legal limit.
WIND_KMH_MIN = 4.8
WIND_KMH_MAX = 24.1
TEMP_C_MIN = 4.0
TEMP_C_MAX = 29.4
RH_MIN = 30.0
RH_MAX = 90.0


def _chunk_preview(chunk: dict, *, limit: int = 1200) -> dict[str, Any]:
    from rag.funding_fields import visible_text

    text = visible_text(chunk)
    return {
        "id": chunk["id"],
        "parent_id": chunk.get("parent_id"),
        "source": chunk.get("source"),
        "title": chunk.get("title"),
        "url": chunk.get("application_url") or chunk.get("url"),
        "page_url": chunk.get("url"),
        "states": chunk.get("states") or [],
        "region": chunk.get("region"),
        "crop": chunk.get("crop") or [],
        "practice": chunk.get("practice") or [],
        "date": chunk.get("date"),
        "abstract_only": bool(chunk.get("abstract_only")),
        "trust_tier": chunk.get("trust_tier"),
        "as_of": chunk.get("as_of"),
        "deadline": chunk.get("deadline"),
        "deadline_iso": chunk.get("deadline_iso"),
        "expired": chunk.get("expired"),
        "funder": chunk.get("funder"),
        "coverage": chunk.get("coverage"),
        "eligibility_text": chunk.get("eligibility_text") or None,
        "section_path": chunk.get("section_path"),
        "text": text[:limit],
    }


def search_evidence(
    retriever: Retriever,
    query: str,
    *,
    query_vec=None,
    variant: str = "hybrid",
    top_k: int = 10,
    demote_farmdeck: bool = True,
    rerank_precomputed: dict | None = None,
) -> dict[str, Any]:
    result = retriever.search(
        query,
        variant=variant,  # type: ignore[arg-type]
        query_vec=query_vec,
        top_k=top_k,
        exclude_expired=True,
        demote_farmdeck_on_effectiveness=demote_farmdeck,
        rerank_precomputed=rerank_precomputed,
    )
    hits = []
    for h in result.hits:
        rec = _chunk_preview(h.chunk)
        rec["score"] = h.score
        rec["rank"] = h.rank
        hits.append(rec)
    return {
        "tool": "search_evidence",
        "query": query,
        "variant": variant,
        "features": {
            "states": result.features.states,
            "seasons": result.features.seasons,
            "crops": result.features.crops,
            "effectiveness": result.features.effectiveness,
        },
        "hits": hits,
        "chunks": retriever.store.as_retrieved([h.chunk_id for h in result.hits]),
    }


def _practice_match(practice: str | None, chunk: dict) -> bool:
    if not practice:
        return True
    needle = practice.lower()
    blob = " ".join(
        [
            " ".join(chunk.get("practice") or []),
            " ".join(chunk.get("tags") or []),
            chunk.get("title") or "",
            (chunk.get("text") or "")[:1500],
        ]
    ).lower()
    return needle in blob



def _named_program_boost(query: str, rec: dict) -> float:
    q = (query or "").lower()
    title = (rec.get("title") or "").lower()
    pid = (rec.get("parent_id") or rec.get("id") or "").lower()
    boost = 0.0
    for token in ("eqip", "csp", "acep", "rcee", "cspp", "climate smart", "cover crop cost-share", "cover crop cost share"):
        if token in q and (token in title or token.replace(" ", "-") in pid or token in pid):
            boost += 15.0
    if "eqip" in q and "usda-nrcs-eqip" in pid:
        boost += 40.0
    if "eqip" in q and "bridge-loan" in pid:
        boost -= 8.0
    return boost


def find_funding(
    retriever: Retriever,
    query: str,
    *,
    state: str | None = None,
    practice: str | None = None,
    deadline_before: str | None = None,
    query_vec=None,
    variant: str = "hybrid",
    top_k: int = 8,
    rerank_precomputed: dict | None = None,
) -> dict[str, Any]:
    """Filter funding programs by state / practice / deadline. Never decide eligibility.

    Fuses general retrieval with a field-level BM25 / name match over program
    name and state fields. The state argument is a TOOL-level filter (state or
    nationwide). General evidence search still only boosts, never filters, by state.
    """
    from rag.features import QueryFeatures
    from rag.funding_fields import is_funding_query  # noqa: F401
    from rag.retrieve import rrf_fuse, RRF_K

    feats = QueryFeatures.from_query(query)
    state = state or (feats.states[0] if feats.states else None)
    result = retriever.search(
        query,
        variant=variant,  # type: ignore[arg-type]
        query_vec=query_vec,
        top_k=max(40, top_k * 5),
        exclude_expired=True,
        demote_farmdeck_on_effectiveness=False,
        rerank_precomputed=rerank_precomputed,
    )
    field_ranked = retriever.rank_funding_fields(query, feats, top_n=80, exclude_expired=True)
    fused_ids = list(rrf_fuse(
        [[h.chunk_id for h in result.hits], [cid for cid, _ in field_ranked]],
        k=RRF_K,
    ).items())
    fused_ids.sort(key=lambda kv: (-kv[1], kv[0]))
    # Build a score map; include any field-only hits the hybrid window missed
    score_map = {cid: sc for cid, sc in fused_ids}
    for cid, sc in field_ranked:
        score_map.setdefault(cid, sc)

    deadline_cut = None
    if deadline_before:
        try:
            deadline_cut = date.fromisoformat(deadline_before)
        except ValueError:
            deadline_cut = None

    def _accept(ch: dict) -> bool:
        if ch.get("source") != "she_funding" and ch.get("trust_tier") != "program_page":
            return False
        if ch.get("expired") is True:
            return False
        if state:
            if not (state_match([state], ch) or chunk_is_nationwide(ch)):
                if state.lower() not in {"nationwide", "national", "us", "usa"}:
                    return False
        if not _practice_match(practice, ch):
            return False
        if deadline_cut and ch.get("deadline_iso"):
            try:
                if date.fromisoformat(str(ch["deadline_iso"])) > deadline_cut:
                    return False
            except ValueError:
                pass
        return True

    ordered_ids = [cid for cid, _ in sorted(score_map.items(), key=lambda kv: (-kv[1], kv[0]))]
    kept = []
    have = set()
    for cid in ordered_ids:
        ch = retriever.store.by_id.get(cid)
        if not ch or cid in have or not _accept(ch):
            continue
        rec = _chunk_preview(ch, limit=1600)
        rec["score"] = score_map.get(cid, 0.0)
        rec["criteria"] = rec.get("eligibility_text") or rec.get("text")
        rec["disclaimer"] = (
            "These are program criteria copied from the program page. "
            "This is not a determination that any person or farm qualifies or is eligible."
        )
        kept.append(rec)
        have.add(cid)
        if len(kept) >= top_k:
            break

    # Named-program injection (EQIP etc.) still applies after the state filter.
    if len(kept) < top_k:
        for ch in retriever.store.chunks:
            if ch["id"] in have or not _accept(ch):
                continue
            rec = _chunk_preview(ch, limit=1600)
            boost = _named_program_boost(query, rec)
            if boost <= 0:
                continue
            rec["score"] = boost
            rec["criteria"] = rec.get("eligibility_text") or rec.get("text")
            rec["disclaimer"] = (
                "These are program criteria copied from the program page. "
                "This is not a determination that any person or farm qualifies or is eligible."
            )
            kept.append(rec)
            have.add(ch["id"])
            if len(kept) >= top_k:
                break

    if kept:
        kept.sort(key=lambda rec: (-_named_program_boost(query, rec), -(rec.get("score") or 0)))
        for i, rec in enumerate(kept, 1):
            rec["rank"] = i
    return {
        "tool": "find_funding",
        "query": query,
        "filters": {"state": state, "practice": practice, "deadline_before": deadline_before, "expired_excluded": True},
        "hits": kept,
        "chunks": retriever.store.as_retrieved([k["id"] for k in kept]),
        "note": "Never treat these hits as an eligibility determination.",
    }




def _parse_latlon(query: str) -> dict[str, Any] | None:
    m = re.search(r"(-?\d{1,2}\.\d+)\s*[, ]\s*(-?\d{1,3}\.\d+)", query or "")
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return {"name": f"{lat:.4f}, {lon:.4f}", "lat": lat, "lon": lon, "timezone": "America/Chicago"}
    return None


def resolve_place(query: str | None = None, lat: float | None = None, lon: float | None = None) -> dict[str, Any]:
    if lat is not None and lon is not None:
        return {"name": f"{lat:.4f}, {lon:.4f}", "lat": float(lat), "lon": float(lon), "timezone": "America/Chicago"}
    if query:
        parsed = _parse_latlon(query)
        if parsed:
            return parsed
        q = query.lower()
        if "boone" in q or "ames" in q or re.search(r"\bia\b", query) or "iowa" in q:
            return dict(BOONE_COUNTY_IA)
    return dict(BOONE_COUNTY_IA)


def _hour_ok(row: dict) -> bool:
    precip = float(row.get("precipitation") or 0)
    wind = row.get("wind_speed_10m")
    temp = row.get("temperature_2m")
    rh = row.get("relative_humidity_2m")
    if precip > 0.05:
        return False
    if wind is None or not (WIND_KMH_MIN <= float(wind) <= WIND_KMH_MAX):
        return False
    if temp is None or not (TEMP_C_MIN <= float(temp) <= TEMP_C_MAX):
        return False
    if rh is not None and not (RH_MIN <= float(rh) <= RH_MAX):
        return False
    return True


def get_weather(
    query: str | None = None,
    *,
    lat: float | None = None,
    lon: float | None = None,
    forecast_days: int = 7,
) -> dict[str, Any]:
    place = resolve_place(query, lat, lon)
    params = {
        "latitude": place["lat"],
        "longitude": place["lon"],
        "timezone": place.get("timezone") or "America/Chicago",
        "forecast_days": forecast_days,
        "hourly": "temperature_2m,relative_humidity_2m,precipitation,precipitation_probability,wind_speed_10m,wind_gusts_10m",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max,wind_speed_10m_max,wind_gusts_10m_max",
        "wind_speed_unit": "kmh",
        "temperature_unit": "celsius",
        "precipitation_unit": "mm",
    }
    r = requests.get(
        OPEN_METEO,
        params=params,
        timeout=30,
        headers={"User-Agent": "OpenAgronomyLocal/0.1 (local research; no contact)"},
    )
    r.raise_for_status()
    data = r.json()
    hourly = data.get("hourly") or {}
    times = hourly.get("time") or []
    windows = []
    for i, ts in enumerate(times[: 24 * 3]):  # next 72 hours
        row = {k: (hourly[k][i] if i < len(hourly[k]) else None) for k in hourly if k != "time"}
        row["time"] = ts
        if _hour_ok(row):
            windows.append(ts)
    daily_rows = []
    daily = data.get("daily") or {}
    dtimes = daily.get("time") or []
    for i, ds in enumerate(dtimes):
        daily_rows.append({k: (daily[k][i] if i < len(daily[k]) else None) for k in daily})
        daily_rows[-1]["date"] = ds
    issued = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    return {
        "tool": "get_weather",
        "place": place,
        "source": "Open-Meteo",
        "source_url": r.url.split("&hourly=")[0],
        "issued_at": issued,
        "issued_at_zone": "America/New_York",
        "daily": daily_rows,
        "spray_weather_windows_next_72h": windows,
        "spray_window_rule": {
            "wind_kmh": [WIND_KMH_MIN, WIND_KMH_MAX],
            "temp_c": [TEMP_C_MIN, TEMP_C_MAX],
            "rh_pct": [RH_MIN, RH_MAX],
            "precip_mm": 0,
            "disclaimer": (
                "This is a simple meteorological weather window, not a pesticide recommendation, "
                "not a label rate, and not a legal or crop-specific spray decision. "
                "Always follow the current product label and local regulations."
            ),
        },
        "hourly_sample": [
            {
                "time": (hourly.get("time") or [None])[i],
                "temp_c": (hourly.get("temperature_2m") or [None])[i],
                "rh": (hourly.get("relative_humidity_2m") or [None])[i],
                "precip_mm": (hourly.get("precipitation") or [None])[i],
                "wind_kmh": (hourly.get("wind_speed_10m") or [None])[i],
            }
            for i in range(0, min(len(hourly.get("time") or []), 24), 3)
        ],
    }
