"""Medication extraction. The part that could hurt somebody if it were wrong."""

import json

import pytest

from conftest import stub_bedrock, utt
from second_chair.bedrock import Bedrock
from second_chair.consent import establish, scope_filter
from second_chair.meds import ChangeKind, MedChange, extract, rule_based, verify


@pytest.fixture
def retained(session, stream):
    in_scope, _ = scope_filter(session, stream)
    _, kept = establish(session, in_scope)
    return kept


class TestRuleBasedFallback:
    """Runs with no network. The app is usable in this state and the tests pin it."""

    def test_the_reduction_is_found(self, retained):
        rows = {m.medicine: m for m in rule_based(retained)}
        b = rows["bisoprolol"]
        assert b.kind is ChangeKind.REDUCED
        assert b.from_dose == "5mg"
        assert b.to_dose == "2.5mg"

    def test_a_stop_is_found(self, retained):
        assert {m.medicine: m for m in rule_based(retained)}["amlodipine"].kind is ChangeKind.STOPPED

    def test_an_increase_is_found(self, retained):
        a = {m.medicine: m for m in rule_based(retained)}["atorvastatin"]
        assert a.kind is ChangeKind.INCREASED
        assert (a.from_dose, a.to_dose) == ("20mg", "40mg")

    def test_no_change_is_a_row_in_its_own_right(self, retained):
        """A patient who hears nothing about a medicine assumes it stopped."""
        assert {m.medicine: m for m in rule_based(retained)}["apixaban"].kind is ChangeKind.UNCHANGED

    def test_a_nickname_is_unclear_not_a_guess(self, retained):
        rows = rule_based(retained)
        nick = [m for m in rows if "water tablet" in m.medicine]
        assert len(nick) == 1
        assert nick[0].kind is ChangeKind.UNCLEAR
        assert nick[0].from_dose is None
        assert not any("furosemide" in m.medicine.lower() for m in rows)

    def test_spoken_numbers_are_normalised(self):
        rows = rule_based([utt("bring the bisoprolol down from five milligrams to two point five milligrams")])
        assert rows[0].to_dose == "2.5mg"

    def test_one_row_per_medicine(self, retained):
        names = [m.medicine for m in rule_based(retained)]
        assert len(names) == len(set(names))


class TestVerifier:
    def test_a_row_whose_quote_is_not_in_the_transcript_is_dropped(self):
        us = [utt("we're reducing the bisoprolol")]
        row = MedChange("bisoprolol", ChangeKind.REDUCED, "10mg", "5mg", None, "we are doubling it", 0)
        assert verify([row], us) == []

    def test_a_row_citing_an_index_out_of_range_is_dropped(self):
        us = [utt("we're reducing the bisoprolol")]
        row = MedChange("bisoprolol", ChangeKind.REDUCED, None, None, None, "reducing", 9)
        assert verify([row], us) == []

    def test_a_row_with_no_medicine_name_is_dropped(self):
        us = [utt("we're reducing it")]
        assert verify([MedChange("", ChangeKind.REDUCED, None, None, None, "reducing", 0)], us) == []

    def test_punctuation_and_case_do_not_matter(self):
        us = [utt("The apixaban does not change. Five milligrams twice a day")]
        row = MedChange("apixaban", ChangeKind.UNCHANGED, None, None, None, "apixaban does NOT change", 0)
        assert len(verify([row], us)) == 1


class TestModelPath:
    def test_a_good_model_answer_is_used(self, retained):
        payload = json.dumps(
            [
                {
                    "medicine": "bisoprolol",
                    "kind": "reduced",
                    "from_dose": "5mg",
                    "to_dose": "2.5mg",
                    "timing": "in the morning",
                    "quote": "bring the bisoprolol down from five milligrams",
                    "utterance_index": next(
                        i for i, u in enumerate(retained) if "bring the bisoprolol down" in u.text
                    ),
                    "note": "",
                }
            ]
        )
        rows, call = extract(retained, Bedrock(invoke=stub_bedrock(payload), models=["m"]))
        assert call.used_model
        assert rows[0].medicine == "bisoprolol"

    def test_a_hallucinated_quote_from_the_model_is_rejected_and_the_rules_take_over(self, retained):
        payload = json.dumps(
            [{"medicine": "warfarin", "kind": "started", "from_dose": None, "to_dose": "3mg",
              "timing": None, "quote": "start the warfarin at three milligrams",
              "utterance_index": 0, "note": ""}]
        )
        rows, _ = extract(retained, Bedrock(invoke=stub_bedrock(payload), models=["m"]))
        assert not any(r.medicine == "warfarin" for r in rows)
        assert any(r.medicine == "bisoprolol" for r in rows)

    def test_garbage_from_the_model_falls_back_to_the_rules(self, retained):
        rows, call = extract(retained, Bedrock(invoke=stub_bedrock("I'd rather not."), models=["m"]))
        assert call.used_model is False
        assert any(r.medicine == "bisoprolol" for r in rows)

    def test_no_model_at_all_still_produces_rows(self, retained):
        rows, call = extract(retained, None)
        assert rows
        assert call.used_model is False
        assert "Bedrock was not reachable" in (call.fallback_reason or "")


class TestTheNoteFieldCannotCarryAdvice:
    """A model given a free-text field beside a medicine will write a dosing instruction
    into it. Ours wrote 'Stop immediately, no taper needed'. So the field is no longer
    read from the model, and anything that does reach it is checked."""

    def test_the_model_cannot_write_a_note_at_all(self, retained):
        idx = next(i for i, u in enumerate(retained) if "bring the bisoprolol down" in u.text)
        payload = json.dumps(
            [{"medicine": "bisoprolol", "kind": "reduced", "from_dose": "5mg", "to_dose": "2.5mg",
              "timing": None, "quote": "bring the bisoprolol down from five milligrams to two point five milligrams",
              "utterance_index": idx,
              "note": "Stop immediately, no taper needed."}]
        )
        rows, call = extract(retained, Bedrock(invoke=stub_bedrock(payload), models=["m"]))
        assert call.used_model
        assert rows[0].note == ""


    @pytest.mark.parametrize(
        "note",
        [
            "Stop immediately, no taper needed.",
            "Take half a tablet until the box runs out.",
            "You should reduce the dose.",
            "The doctor said to keep going.",
            "It is safe to stop this one.",
        ],
    )
    def test_an_instructive_note_is_stripped_by_the_verifier(self, retained, note):
        from second_chair.meds import MedChange, verify

        # Cite the utterance that actually names the medicine. This test used to point at
        # retained[0] ("thank you. Mum, you can sit here."), which said nothing about
        # bisoprolol; the row only survived to reach the note check because verify() was
        # not yet grounding the medicine name.
        idx = next(i for i, u in enumerate(retained) if "bisoprolol" in u.text.lower())
        row = MedChange("bisoprolol", ChangeKind.REDUCED, None, None, None, retained[idx].text, idx, note=note)
        assert verify([row], retained)[0].note == ""

    def test_the_apps_own_nickname_note_survives(self, retained):
        rows = rule_based(retained)
        nick = next(r for r in rows if "water tablet" in r.medicine)
        assert "Named only by nickname" in verify([nick], retained)[0].note

    def test_no_row_on_the_record_page_carries_generated_prose(self, retained):
        """Whatever route produced them, notes are app-authored or empty."""
        rows, _ = extract(retained, None)
        for r in rows:
            assert r.note in ("", ) or r.note.startswith("Named only by nickname")


class TestADoseMustHaveBeenSaidOutLoud:
    """The `note` fix was real and it was not enough.

    `medicine`, `from_dose`, `to_dose` and `timing` were read straight off the model and
    `verify()` looked at none of them. A quote can be copied faithfully and the dose
    beside it invented, and `to_dose` is the largest thing on the medicines panel and
    goes into the carer's summary as well. These tests pin the grounding rule: a value is
    kept only if the room said it.
    """

    def _row(self, retained, **over):
        """One model row against the utterance that carries the bisoprolol reduction.

        That utterance is 'bring the bisoprolol down from five milligrams to two point
        five milligrams, one tablet in the morning, starting tomorrow', so 5mg, 2.5mg and
        'in the morning' are all things the room really said. Anything else in a payload
        here is the model inventing.
        """
        idx = next(i for i, u in enumerate(retained) if "bring the bisoprolol down" in u.text)
        payload = {
            "medicine": "bisoprolol",
            "kind": "reduced",
            "from_dose": "5mg",
            "to_dose": "2.5mg",
            "timing": "in the morning",
            "quote": "bring the bisoprolol down",
            "utterance_index": idx,
        }
        payload.update(over)
        rows, call = extract(retained, Bedrock(invoke=stub_bedrock(json.dumps([payload])), models=["m"]))
        return rows, call

    def test_a_dose_the_room_never_said_is_dropped_not_shown(self, retained):
        """A faithful quote and a number out of nowhere.

        Grounding is against the cited utterance rather than the shorter quote shown on
        screen, because the utterance is the verified source and the quote is an excerpt
        of it. 7.5mg is in neither.
        """
        rows, call = self._row(retained, from_dose="7.5mg")
        assert call.used_model
        assert rows, "the row survives; it is the dose that must not"
        assert rows[0].from_dose is None
        assert rows[0].to_dose == "2.5mg", "the dose that was said is untouched"

    def test_a_doubled_dose_never_reaches_the_page(self, retained):
        """The adversarial case: a real quote, a beta blocker, and ten milligrams."""
        rows, call = self._row(retained, to_dose="10mg")
        assert call.used_model
        assert rows[0].to_dose is None

    def test_a_dose_that_was_said_survives(self, retained):
        """Grounding must not cost the true rows. Both doses are in the utterance, said
        as words, and `_digitise` is what lets them match."""
        rows, call = self._row(retained)
        assert call.used_model
        assert (rows[0].from_dose, rows[0].to_dose) == ("5mg", "2.5mg")

    def test_a_reduction_that_climbs_is_refused(self, retained):
        """Both numbers were said, so grounding alone passes them. Swapping them turns a
        halving into a doubling and the kind is the only thing that still says which way
        it went, so the two have to agree."""
        rows, _ = self._row(retained, from_dose="2.5mg", to_dose="5mg")
        assert (rows[0].from_dose, rows[0].to_dose) == (None, None)

    def test_a_medicine_nobody_named_drops_the_whole_row(self, retained):
        """A dose can be blanked and leave a true row behind. A wrong drug name cannot:
        there is nothing left that is true, so the row goes and the rules run instead."""
        rows, call = self._row(retained, medicine="warfarin")
        assert not any(r.medicine == "warfarin" for r in rows)
        assert call.used_model is False, "losing every row falls back to the rules"

    def test_a_timing_nobody_said_is_dropped(self, retained):
        rows, _ = self._row(retained, timing="at night")
        assert rows[0].timing is None

    def test_a_timing_that_was_said_survives(self, retained):
        rows, _ = self._row(retained, timing="in the morning")
        assert rows[0].timing == "in the morning"

    def test_the_rule_based_path_is_unharmed_by_the_grounding_check(self, retained):
        """The rules read every field out of the utterance already, so nothing they
        produce should ever fail this. If this breaks, the check is too strict."""
        rows = verify(rule_based(retained), retained)
        by_name = {m.medicine: m for m in rows}
        assert by_name["bisoprolol"].from_dose == "5mg"
        assert by_name["bisoprolol"].to_dose == "2.5mg"
        assert by_name["atorvastatin"].to_dose == "40mg"

    def test_the_carers_summary_cannot_carry_an_ungrounded_dose(self, retained):
        """`relative.py` prints doses into the document that gets sent to a daughter, so
        the grounding has to hold at the row, before anything downstream reads it."""
        from second_chair.relative import build as build_share

        rows, _ = self._row(retained, to_dose="10mg")
        summary = build_share("Grace", "Cardiology follow-up", "22 September 2026", rows, [])
        assert "10mg" not in summary.as_text()
