"""Deterministic agronomic unit conversions used by the answer verifier."""

from __future__ import annotations

import math
import re
from typing import Iterable

# NIST: 1 lb = 0.45359237 kg; 1 acre = 0.40468564224 ha
LB_PER_AC_TO_KG_PER_HA = 0.45359237 / 0.40468564224  # ≈ 1.12085116
KG_PER_HA_TO_LB_PER_AC = 1.0 / LB_PER_AC_TO_KG_PER_HA

# Standard USDA test weights used only when the crop is stated or implied nearby.
LB_PER_BUSHEL = {
    "corn": 56.0,
    "maize": 56.0,
    "soy": 60.0,
    "soybean": 60.0,
    "soybeans": 60.0,
}

CROP_RE = re.compile(r"\b(corn|maize|soybeans?|soy)\b", re.I)


def infer_crop(context: str | None) -> str | None:
    if not context:
        return None
    m = CROP_RE.search(context)
    if not m:
        return None
    w = m.group(1).lower()
    if w in {"soy", "soybean", "soybeans"}:
        return "soy"
    return "corn"


def lb_ac_to_kg_ha(value: float) -> float:
    return value * LB_PER_AC_TO_KG_PER_HA


def kg_ha_to_lb_ac(value: float) -> float:
    return value * KG_PER_HA_TO_LB_PER_AC


def bu_ac_to_lb_ac(value: float, crop: str) -> float:
    crop = crop.lower()
    if crop not in LB_PER_BUSHEL:
        raise ValueError(f"No standard test weight for crop {crop!r}; use corn or soy")
    return value * LB_PER_BUSHEL[crop]


def bu_ac_to_kg_ha(value: float, crop: str) -> float:
    return lb_ac_to_kg_ha(bu_ac_to_lb_ac(value, crop))


def kg_ha_to_bu_ac(value: float, crop: str) -> float:
    return kg_ha_to_lb_ac(value) / LB_PER_BUSHEL[crop.lower()]


def nearly_equal(a: float, b: float, rel: float = 0.01, abs_tol: float = 0.51) -> bool:
    """Match after conversion. 0.51 abs lets 105 lb and 105.0 pass; 1% covers rounding."""
    return math.isclose(a, b, rel_tol=rel, abs_tol=abs_tol)


# Canonical unit families used when comparing a number in an answer to a chunk.
UNIT_ALIASES = {
    "percent": {"%", "percent", "percentage", "pct"},
    "ppm": {"ppm", "parts per million"},
    "lb_ac": {
        "lb/ac", "lb/acre", "lbs/ac", "lbs/acre", "lb per acre", "lbs per acre",
        "pounds per acre", "pound per acre", "lb ac-1", "lb ac^{-1}",
        "lb n/ac", "lb n per acre", "lbs n/ac", "lb n per acre",
    },
    "kg_ha": {
        "kg/ha", "kg ha-1", "kg ha^{-1}", "kg per hectare", "kg/hectare",
        "kg n/ha", "kg n ha-1", "kg n ha-1", "kg dm ha-1", "kg dry matter ha-1",
        "kg dm/ha",
    },
    "bu_ac": {
        "bu/ac", "bu/acre", "bu per acre", "bushels per acre", "bushel per acre",
        "bu ac-1", "bushels/acre",
    },
    "usd": {"$", "usd", "dollars", "dollar"},
    "usd_ac": {"$/ac", "$/acre", "dollars per acre", "dollar per acre", "$ per acre"},
    "ton_ac": {"ton/ac", "tons/ac", "ton per acre", "tons per acre", "t/ac"},
}


def canon_unit(unit: str | None) -> str | None:
    if not unit:
        return None
    u = unit.lower().strip()
    u = u.replace("·", " ").replace("–", "-").replace("—", "-")
    u = re.sub(r"\s+", " ", u)
    u = u.replace(" / ", "/").replace("ha-1", "ha-1")
    # normalize ha^{-1} already handled
    u = u.replace("acre", "ac") if u.endswith("acre") or "/acre" in u or "per acre" in u else u
    for family, aliases in UNIT_ALIASES.items():
        if u in aliases:
            return family
        # loose contains
        if u.replace(".", "") in {a.replace(".", "") for a in aliases}:
            return family
    # typographic variants like kg N ha-1
    compact = re.sub(r"[^a-z0-9%$]+", "", u)
    for family, aliases in UNIT_ALIASES.items():
        if compact in {re.sub(r"[^a-z0-9%$]+", "", a) for a in aliases}:
            return family
    return u or None


def convert(value: float, from_unit: str | None, to_unit: str | None, crop: str | None = None) -> float | None:
    """Return value expressed in to_unit, or None if no deterministic conversion exists."""
    src = canon_unit(from_unit)
    dst = canon_unit(to_unit)
    if src is None or dst is None:
        return None
    if src == dst:
        return value
    if src == "lb_ac" and dst == "kg_ha":
        return lb_ac_to_kg_ha(value)
    if src == "kg_ha" and dst == "lb_ac":
        return kg_ha_to_lb_ac(value)
    if src == "bu_ac" and dst in {"lb_ac", "kg_ha"}:
        if not crop:
            return None
        lb = bu_ac_to_lb_ac(value, crop)
        return lb if dst == "lb_ac" else lb_ac_to_kg_ha(lb)
    if dst == "bu_ac" and src in {"lb_ac", "kg_ha"}:
        if not crop:
            return None
        lb = value if src == "lb_ac" else kg_ha_to_lb_ac(value)
        return lb / LB_PER_BUSHEL[crop]
    return None


def equivalent_values(
    value: float,
    unit: str | None,
    context: str | None = None,
) -> list[tuple[float, str | None]]:
    """Value plus any deterministic conversions, as (number, canonical unit) pairs."""
    crop = infer_crop(context)
    family = canon_unit(unit)
    out: list[tuple[float, str | None]] = [(value, family)]
    if family == "lb_ac":
        out.append((lb_ac_to_kg_ha(value), "kg_ha"))
        if crop:
            try:
                out.append((value / LB_PER_BUSHEL[crop], "bu_ac"))
            except KeyError:
                pass
    elif family == "kg_ha":
        out.append((kg_ha_to_lb_ac(value), "lb_ac"))
        if crop:
            try:
                out.append((kg_ha_to_bu_ac(value, crop), "bu_ac"))
            except (KeyError, ValueError):
                pass
    elif family == "bu_ac" and crop:
        lb = bu_ac_to_lb_ac(value, crop)
        out.append((lb, "lb_ac"))
        out.append((lb_ac_to_kg_ha(lb), "kg_ha"))
    # de-dupe
    seen = set()
    uniq = []
    for v, u in out:
        key = (round(v, 6), u)
        if key not in seen:
            seen.add(key)
            uniq.append((v, u))
    return uniq
