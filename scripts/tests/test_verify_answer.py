"""Pass and fail cases for verify_answer, using real chunks from chunks_v2.jsonl."""

from __future__ import annotations

import pytest

from verify_answer import verify_answer

NCREDIT_C1 = "SHE-FA-2026-0008::c1"
NCREDIT_C2 = "SHE-FA-2026-0008::c2"
EQIP = "she_funding:usda-nrcs-eqip::c1"
PANTOJA = "paper:10.2136/sssaj2015.02.0084::c1"
RYE_59 = "farmdeck:cover-crops_cereal-rye-cover-crop::c4"


def _ids(chunks_by_id, *ids):
    return [chunks_by_id[i] for i in ids]


def test_pass_ncredit_numbers_and_citation(chunks_by_id):
    answer = (
        "A reasonable estimate counts about half of the legume biomass nitrogen as the credit to the following corn "
        f"[c:{NCREDIT_C1}]. In the worked example, a 3-ton hairy vetch stand gives a credit of 105 lb N per acre, "
        f"so a planned 200 lb N per acre rate is adjusted to 95 lb N per acre [c:{NCREDIT_C1}]."
    )
    report = verify_answer(answer, _ids(chunks_by_id, NCREDIT_C1, NCREDIT_C2))
    assert report["pass"], report


def test_pass_psnt_threshold_from_second_chunk(chunks_by_id):
    answer = (
        "University of Wisconsin Extension guidance cited in the answer says a pre-sidedress soil nitrate test "
        f"result over 21 ppm nitrate-N means no additional N is needed [c:{NCREDIT_C2}]."
    )
    report = verify_answer(answer, _ids(chunks_by_id, NCREDIT_C1, NCREDIT_C2))
    assert report["pass"], report


def test_fail_uncited_number(chunks_by_id):
    answer = (
        f"Count about half of the biomass nitrogen as the credit [c:{NCREDIT_C1}]. "
        "The credit in the worked example is 105 lb N per acre."
    )
    report = verify_answer(answer, _ids(chunks_by_id, NCREDIT_C1))
    assert report["pass"] is False
    uncited = next(c for c in report["checks"] if c["name"] == "uncited_numbers")
    assert uncited["pass"] is False
    assert any("105" in x for x in uncited["uncited"])


def test_fail_cited_id_not_retrieved(chunks_by_id):
    answer = (
        f"The credit in the worked example is 105 lb N per acre [c:{NCREDIT_C1}]."
    )
    # Retrieve only the second chunk, not the one cited
    report = verify_answer(answer, _ids(chunks_by_id, NCREDIT_C2))
    assert report["pass"] is False
    missing = next(c for c in report["checks"] if c["name"] == "cited_chunks_retrieved")
    assert missing["pass"] is False
    assert NCREDIT_C1 in missing["missing_ids"]


def test_fail_number_not_in_cited_chunk(chunks_by_id):
    # 21 ppm is in c2, but we only cite c1
    answer = (
        f"A pre-sidedress soil nitrate test result over 21 ppm nitrate-N means no additional N is needed [c:{NCREDIT_C1}]."
    )
    report = verify_answer(answer, _ids(chunks_by_id, NCREDIT_C1, NCREDIT_C2))
    assert report["pass"] is False
    nums = next(c for c in report["checks"] if c["name"] == "numbers_supported_by_cited_chunks")
    assert nums["pass"] is False


def test_fail_cited_but_unsupported_claim(chunks_by_id):
    answer = (
        "Virtual fence collars reliably replace all permanent fencing on every ranch in Kentucky "
        f"and always raise stocking rate by a large margin [c:{NCREDIT_C1}]."
    )
    report = verify_answer(answer, _ids(chunks_by_id, NCREDIT_C1))
    assert report["pass"] is False
    flag = next(c for c in report["checks"] if c["name"] == "cited_but_unsupported")
    assert flag["pass"] is False
    assert flag["flags"]


def test_fail_factual_sentence_without_citation(chunks_by_id):
    answer = (
        f"Estimated N credit equals total biomass N times 0.50 [c:{NCREDIT_C1}]. "
        "Legume residue typically has a carbon-to-nitrogen ratio well below 20 to 1."
    )
    # The second sentence is factual (ratio claim) and has a number (20) without a citation.
    report = verify_answer(answer, _ids(chunks_by_id, NCREDIT_C1))
    assert report["pass"] is False
    # Either uncited number or uncited factual sentence should trip
    uncited_n = next(c for c in report["checks"] if c["name"] == "uncited_numbers")
    uncited_s = next(c for c in report["checks"] if c["name"] == "factual_sentences_cited")
    assert uncited_n["pass"] is False or uncited_s["pass"] is False


def test_pass_funding_with_as_of_and_url_no_eligibility(chunks_by_id):
    answer = (
        "The Environmental Quality Incentives Program page lists coverage as available nationwide "
        f"[c:{EQIP}]. Applications are accepted year-round and considered at state funding cutoffs; "
        f"there is no single national application deadline [c:{EQIP}]. "
        "As of 2026-11-14 (last verified on the Soil Health Exchange page), confirm current terms "
        f"at https://www.nrcs.usda.gov/programs-initiatives/environmental-quality-incentives-program [c:{EQIP}]. "
        "This is what the program page states, not a decision that any particular farm qualifies."
    )
    report = verify_answer(answer, _ids(chunks_by_id, EQIP), is_funding=True)
    assert report["pass"], report


def test_fail_funding_you_qualify(chunks_by_id):
    answer = (
        "EQIP is available nationwide [c:" + EQIP + "]. As of 2026-11-14 you qualify for EQIP. "
        "See https://www.nrcs.usda.gov/programs-initiatives/environmental-quality-incentives-program."
    )
    report = verify_answer(answer, _ids(chunks_by_id, EQIP), is_funding=True)
    assert report["pass"] is False
    elig = next(c for c in report["checks"] if c["name"] == "funding_no_eligibility_assertion")
    assert elig["pass"] is False


def test_fail_funding_missing_as_of_or_url(chunks_by_id):
    answer = (
        f"EQIP coverage is listed as available nationwide [c:{EQIP}]. "
        "There is no single national application deadline."
    )
    report = verify_answer(answer, _ids(chunks_by_id, EQIP), is_funding=True)
    assert report["pass"] is False
    check = next(c for c in report["checks"] if c["name"] == "funding_as_of_and_url")
    assert check["pass"] is False


def test_pass_abstract_effect_size_present(chunks_by_id):
    answer = (
        "In the Iowa no-till corn–soybean trial, the rye cover crop reduced corn grain yield by 6% "
        f"at the economic optimum N rate [c:{PANTOJA}]. Maximum rye biomass was 1280 kg dry matter ha-1 "
        f"[c:{PANTOJA}]."
    )
    report = verify_answer(answer, _ids(chunks_by_id, PANTOJA))
    assert report["pass"], report


def test_fail_abstract_only_effect_size_absent(chunks_by_id):
    # 59% nitrate cut is from a different study, not the Pantoja abstract
    answer = (
        "The Pantoja rye trial shows that a rye cover crop cut nitrate leaching 59% in Iowa tile drainage "
        f"and also reduced corn yield 19 bu/ac [c:{PANTOJA}]."
    )
    report = verify_answer(answer, _ids(chunks_by_id, PANTOJA))
    assert report["pass"] is False
    abscheck = next(c for c in report["checks"] if c["name"] == "abstract_only_effect_sizes")
    assert abscheck["pass"] is False
    assert abscheck["violations"]


def test_pass_unit_conversion_lb_ac_to_kg_ha(chunks_by_id):
    # 105 lb N per acre ≈ 117.7 kg/ha
    answer = (
        "In the worked example the estimated credit is about 117.7 kg/ha of nitrogen "
        f"(converted from 105 lb per acre at 1.12085 kg/ha per lb/ac) [c:{NCREDIT_C1}]."
    )
    report = verify_answer(answer, _ids(chunks_by_id, NCREDIT_C1))
    assert report["pass"], report


def test_pass_farmdeck_measured_59_percent(chunks_by_id):
    answer = (
        "The FarmDeck cereal rye profile cites Rogovska et al. (USDA-ARS) as finding a 59% cut in nitrate "
        f"leaching over 19 years of tile-drained corn–soybean plots in Iowa [c:{RYE_59}]."
    )
    report = verify_answer(answer, _ids(chunks_by_id, RYE_59))
    assert report["pass"], report
