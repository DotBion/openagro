"""State, region, crop, and season detectors. Used as retrieval *boosts*, never hard filters."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

US_STATES: dict[str, str] = {
    "alabama": "Alabama", "al": "Alabama",
    "alaska": "Alaska", "ak": "Alaska",
    "arizona": "Arizona", "az": "Arizona",
    "arkansas": "Arkansas", "ar": "Arkansas",
    "california": "California", "ca": "California",
    "colorado": "Colorado", "co": "Colorado",
    "connecticut": "Connecticut", "ct": "Connecticut",
    "delaware": "Delaware", "de": "Delaware",
    "florida": "Florida", "fl": "Florida",
    "georgia": "Georgia", "ga": "Georgia",
    "hawaii": "Hawaii", "hi": "Hawaii",
    "idaho": "Idaho", "id": "Idaho",
    "illinois": "Illinois", "il": "Illinois",
    "indiana": "Indiana", "in": "Indiana",
    "iowa": "Iowa", "ia": "Iowa",
    "kansas": "Kansas", "ks": "Kansas",
    "kentucky": "Kentucky", "ky": "Kentucky",
    "louisiana": "Louisiana", "la": "Louisiana",
    "maine": "Maine", "me": "Maine",
    "maryland": "Maryland", "md": "Maryland",
    "massachusetts": "Massachusetts", "ma": "Massachusetts",
    "michigan": "Michigan", "mi": "Michigan",
    "minnesota": "Minnesota", "mn": "Minnesota",
    "mississippi": "Mississippi", "ms": "Mississippi",
    "missouri": "Missouri", "mo": "Missouri",
    "montana": "Montana", "mt": "Montana",
    "nebraska": "Nebraska", "ne": "Nebraska",
    "nevada": "Nevada", "nv": "Nevada",
    "new hampshire": "New Hampshire", "nh": "New Hampshire",
    "new jersey": "New Jersey", "nj": "New Jersey",
    "new mexico": "New Mexico", "nm": "New Mexico",
    "new york": "New York", "ny": "New York",
    "north carolina": "North Carolina", "nc": "North Carolina",
    "north dakota": "North Dakota", "nd": "North Dakota",
    "ohio": "Ohio", "oh": "Ohio",
    "oklahoma": "Oklahoma", "ok": "Oklahoma",
    "oregon": "Oregon", "or": "Oregon",
    "pennsylvania": "Pennsylvania", "pa": "Pennsylvania",
    "rhode island": "Rhode Island", "ri": "Rhode Island",
    "south carolina": "South Carolina", "sc": "South Carolina",
    "south dakota": "South Dakota", "sd": "South Dakota",
    "tennessee": "Tennessee", "tn": "Tennessee",
    "texas": "Texas", "tx": "Texas",
    "utah": "Utah", "ut": "Utah",
    "vermont": "Vermont", "vt": "Vermont",
    "virginia": "Virginia", "va": "Virginia",
    "washington": "Washington", "wa": "Washington",
    "west virginia": "West Virginia", "wv": "West Virginia",
    "wisconsin": "Wisconsin", "wi": "Wisconsin",
    "wyoming": "Wyoming", "wy": "Wyoming",
    "district of columbia": "District of Columbia", "dc": "District of Columbia",
    "puerto rico": "Puerto Rico", "pr": "Puerto Rico",
}

# Longer names first so "west virginia" wins over "virginia"
_STATE_NAMES = sorted(
    {k for k in US_STATES if len(k) > 2},
    key=len,
    reverse=True,
)
_STATE_ABBR = {k for k in US_STATES if len(k) == 2}

REGIONS: dict[str, str] = {
    "midwest": "Midwest",
    "mid-west": "Midwest",
    "corn belt": "Corn Belt",
    "cornbelt": "Corn Belt",
    "great plains": "Great Plains",
    "high plains": "High Plains",
    "palouse": "Palouse",
    "piedmont": "Piedmont",
    "delta": "Delta",
    "mississippi delta": "Delta",
    "northeast": "Northeast",
    "southeast": "Southeast",
    "southwest": "Southwest",
    "pacific northwest": "Pacific Northwest",
    "pnw": "Pacific Northwest",
    "northwest": "Northwest",
    "upper midwest": "Upper Midwest",
    "southern plains": "Southern Plains",
    "nationwide": "nationwide",
    "national": "nationwide",
}

SEASON_TERMS: dict[str, str] = {
    "spring": "spring",
    "preplant": "spring",
    "pre-plant": "spring",
    "burndown": "spring",
    "planting": "spring",
    "planted": "spring",
    "summer": "summer",
    "sidedress": "summer",
    "side-dress": "summer",
    "side dress": "summer",
    "tassel": "summer",
    "tasseling": "summer",
    "fall": "fall",
    "autumn": "fall",
    "harvest": "fall",
    "post-harvest": "fall",
    "postharvest": "fall",
    "after harvest": "fall",
    "winter": "winter",
    "frost seed": "winter",
    "frost-seed": "winter",
    "frost seeding": "winter",
    "dormant": "winter",
}

CROP_TERMS: dict[str, str] = {
    "corn": "corn", "maize": "corn",
    "soybean": "soybean", "soybeans": "soybean", "soy": "soybean", "beans": "soybean",
    "wheat": "wheat", "milling wheat": "wheat",
    "cotton": "cotton",
    "rice": "rice",
    "sorghum": "sorghum", "milo": "sorghum", "sudan": "sorghum-sudangrass",
    "rye": "rye", "cereal rye": "rye",
    "vetch": "vetch", "hairy vetch": "vetch",
    "clover": "clover", "red clover": "clover",
    "alfalfa": "alfalfa",
    "oat": "oats", "oats": "oats",
    "barley": "barley",
    "radish": "radish", "tillage radish": "radish",
    "turnip": "turnip", "turnips": "turnip",
    "brassica": "brassica", "brassicas": "brassica",
    "cover crop": "cover crop", "cover crops": "cover crop",
    "pasture": "pasture", "silage": "silage",
    "cattle": "cattle", "cow": "cattle", "cows": "cattle", "calf": "cattle",
    "sheep": "sheep",
    "sugarcane": "sugarcane", "sugarbeet": "sugarbeet", "sugar beet": "sugarbeet",
    "almond": "almond", "almonds": "almond",
    "coffee": "coffee",
}

EFFECTIVENESS_RE = re.compile(
    r"\b("
    r"yield|yields|effective|effectiveness|efficacy|work|works|working|"
    r"credit|n credit|nitrogen credit|leach|leaching|nitrate|"
    r"roi|pay ?off|pencil|pay back|payback|response|respond|"
    r"reduc(?:e|ed|es|ing|tion)|increas(?:e|ed|es|ing)|impact|"
    r"does it (?:help|work)|how well|how much n|"
    r"penalty|cut (?:yield|nitrate)|measured"
    r")\b",
    re.I,
)

INDEPENDENT_SOURCES = {"she_qa", "she_journal", "paper_abstract"}
FARMDECK_SOURCE = "farmdeck_product"


def _word_boundary_search(text_lc: str, term: str) -> bool:
    return re.search(rf"(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])", text_lc) is not None


def detect_states(text: str) -> list[str]:
    if not text:
        return []
    lc = text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for name in _STATE_NAMES:
        if _word_boundary_search(lc, name):
            canon = US_STATES[name]
            if canon not in seen:
                seen.add(canon)
                found.append(canon)
    # Abbreviations only when written in uppercase in the original (avoids "in", "me", "or", "hi")
    for m in re.finditer(r"\b([A-Z]{2})\b", text):
        abbr = m.group(1).lower()
        if abbr in _STATE_ABBR:
            canon = US_STATES[abbr]
            if canon not in seen:
                seen.add(canon)
                found.append(canon)
    return found


def detect_regions(text: str) -> list[str]:
    if not text:
        return []
    lc = text.lower()
    found, seen = [], set()
    for term in sorted(REGIONS, key=len, reverse=True):
        if _word_boundary_search(lc, term):
            canon = REGIONS[term]
            if canon not in seen:
                seen.add(canon)
                found.append(canon)
    return found


def detect_seasons(text: str) -> list[str]:
    if not text:
        return []
    lc = text.lower()
    found, seen = [], set()
    for term in sorted(SEASON_TERMS, key=len, reverse=True):
        if term in lc:
            canon = SEASON_TERMS[term]
            if canon not in seen:
                seen.add(canon)
                found.append(canon)
    return found


def detect_crops(text: str) -> list[str]:
    if not text:
        return []
    lc = text.lower()
    found, seen = [], set()
    for term in sorted(CROP_TERMS, key=len, reverse=True):
        if _word_boundary_search(lc, term):
            canon = CROP_TERMS[term]
            if canon not in seen:
                seen.add(canon)
                found.append(canon)
    return found


def is_effectiveness_question(text: str) -> bool:
    return bool(text and EFFECTIVENESS_RE.search(text))


def _as_list(v) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v else []
    return [str(x) for x in v if x]


def chunk_states(chunk: dict) -> list[str]:
    out = []
    seen = set()
    for raw in _as_list(chunk.get("states")) + _as_list(chunk.get("region")) + _as_list(chunk.get("coverage")):
        for s in detect_states(raw) or ([raw] if raw else []):
            key = s if not isinstance(s, str) else s
            # normalize statewide / nationwide
            low = key.lower() if isinstance(key, str) else ""
            if "nationwide" in low or low in {"national", "available nationwide", "all states"}:
                key = "nationwide"
            if key not in seen:
                seen.add(key)
                out.append(key)
    # also mine embed/header text for a state only if the field was empty? keep field-first.
    return out


def chunk_is_nationwide(chunk: dict) -> bool:
    blob = " ".join(_as_list(chunk.get("states")) + _as_list(chunk.get("region")) + _as_list(chunk.get("coverage")))
    return "nationwide" in blob.lower() or "available nationwide" in blob.lower() or "all states" in blob.lower()


def state_match(query_states: list[str], chunk: dict) -> bool:
    if not query_states:
        return False
    if chunk_is_nationwide(chunk):
        return True
    cstates = {s.lower() for s in chunk_states(chunk)}
    region = (chunk.get("region") or "") 
    region_l = region.lower() if isinstance(region, str) else " ".join(_as_list(region)).lower()
    for s in query_states:
        sl = s.lower()
        if sl in cstates or sl in region_l:
            return True
    return False


def season_match(query_seasons: list[str], chunk: dict) -> bool:
    if not query_seasons:
        return False
    blob = " ".join(
        [
            chunk.get("title") or "",
            chunk.get("section_path") or "",
            " ".join(_as_list(chunk.get("tags"))),
            (chunk.get("text") or "")[:1500],
        ]
    ).lower()
    return any(s in blob for s in query_seasons)


def crop_match(query_crops: list[str], chunk: dict) -> bool:
    if not query_crops:
        return False
    fields = " ".join(_as_list(chunk.get("crop")) + _as_list(chunk.get("tags")) + [chunk.get("title") or ""]).lower()
    for c in query_crops:
        if c.lower() in fields:
            return True
    return False


@dataclass
class QueryFeatures:
    text: str
    states: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    seasons: list[str] = field(default_factory=list)
    crops: list[str] = field(default_factory=list)
    effectiveness: bool = False

    @classmethod
    def from_query(cls, text: str) -> "QueryFeatures":
        return cls(
            text=text,
            states=detect_states(text),
            regions=detect_regions(text),
            seasons=detect_seasons(text),
            crops=detect_crops(text),
            effectiveness=is_effectiveness_question(text),
        )


def boost_factor(feats: QueryFeatures, chunk: dict, *, state_w: float = 0.18, season_w: float = 0.08, crop_w: float = 0.10) -> float:
    """Multiplicative boost (>= 1). Never a filter: unmatched chunks keep factor 1.0."""
    f = 1.0
    if state_match(feats.states, chunk):
        f += state_w
    if feats.regions:
        blob = " ".join(_as_list(chunk.get("region")) + _as_list(chunk.get("states")) + [chunk.get("title") or ""]).lower()
        if any(r.lower() in blob for r in feats.regions):
            f += state_w * 0.6
    if season_match(feats.seasons, chunk):
        f += season_w
    if crop_match(feats.crops, chunk):
        f += crop_w
    return f
