from __future__ import annotations

import math

import pytest

from units import (
    LB_PER_AC_TO_KG_PER_HA,
    bu_ac_to_kg_ha,
    bu_ac_to_lb_ac,
    infer_crop,
    kg_ha_to_bu_ac,
    kg_ha_to_lb_ac,
    lb_ac_to_kg_ha,
    nearly_equal,
)


def test_lb_ac_round_trip():
    assert nearly_equal(kg_ha_to_lb_ac(lb_ac_to_kg_ha(105.0)), 105.0)


def test_corn_bu_uses_56_lb():
    assert bu_ac_to_lb_ac(200, "corn") == 200 * 56
    kg = bu_ac_to_kg_ha(200, "corn")
    assert nearly_equal(kg, 200 * 56 * LB_PER_AC_TO_KG_PER_HA)
    assert nearly_equal(kg_ha_to_bu_ac(kg, "corn"), 200)


def test_soy_bu_uses_60_lb():
    assert bu_ac_to_lb_ac(60, "soy") == 3600
    assert nearly_equal(bu_ac_to_kg_ha(60, "soybean"), 3600 * LB_PER_AC_TO_KG_PER_HA)


def test_unknown_crop_raises():
    with pytest.raises(ValueError):
        bu_ac_to_lb_ac(10, "wheat")


def test_infer_crop():
    assert infer_crop("corn yield was 200 bu/ac") == "corn"
    assert infer_crop("soybean fields lost 3.5%") == "soy"
    assert infer_crop("no crop named here") is None
