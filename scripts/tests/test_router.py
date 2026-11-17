from chat.router import route_by_rules


def test_refuse_pesticide_rate():
    d = route_by_rules("How many ounces of glyphosate should I mix in a 500-gallon tank for tomorrow morning?")
    assert d and d.primary == "refuse" and d.refused_topic == "pesticide_rate"


def test_refuse_eligibility():
    d = route_by_rules("Do I qualify for EQIP on my Boone County farm?")
    assert d and d.primary == "refuse" and d.refused_topic == "eligibility_determination"


def test_refuse_farmx():
    d = route_by_rules("What corn yield did FarmX record on its Boone County, Iowa fields in 2024 and 2025?")
    assert d and d.primary == "refuse" and d.refused_topic == "farmx"


def test_refuse_tax():
    d = route_by_rules("Can I deduct my EQIP cost-share payment off my federal income tax as a grant, and how should I file it?")
    assert d and d.primary == "refuse" and d.refused_topic == "legal_tax"


def test_funding_deadline():
    d = route_by_rules("What's the EQIP signup deadline in Iowa?")
    assert d and d.primary == "funding"


def test_weather_and_funding_combo():
    d = route_by_rules("Will it rain this week in Boone County and is there an EQIP signup deadline?")
    assert d and d.primary == "funding" and d.extra == "weather"


def test_evidence_default_is_none_from_rules():
    # No rule match; pipeline defaults to evidence (or LLM).
    d = route_by_rules("How do I estimate a hairy vetch nitrogen credit before corn?")
    assert d is None


def test_refuse_transferred_payment_rate():
    d = route_by_rules("Can I just use the Iowa cover-crop cost-share payment rate on my California almond orchard and assume I'll get the same check?")
    assert d and d.primary == "refuse"
