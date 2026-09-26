"""Question reconciliation, the ask surface, and the family summary."""

import json

import pytest

from conftest import stub_bedrock
from second_chair import ask as ask_mod
from second_chair.bedrock import Bedrock
from second_chair.consent import establish, scope_filter
from second_chair.guardrail import REFUSAL
from second_chair.meds import ChangeKind, MedChange
from second_chair.questions import Coverage, carry_forward, overlap_based, reconcile
from second_chair.relative import build as build_relative


@pytest.fixture
def retained(session, stream):
    in_scope, _ = scope_filter(session, stream)
    _, kept = establish(session, in_scope)
    return kept


class TestReconciliation:
    def test_an_answered_question_is_covered(self, retained, week):
        out = {o.id: o for o in overlap_based(week["questions"], retained)}
        assert out["q3"].coverage is Coverage.COVERED  # the monthly blood test
        assert out["q3"].evidence_quote

    def test_a_question_never_raised_is_not_covered(self, retained, week):
        out = {o.id: o for o in overlap_based(week["questions"], retained)}
        assert out["q2"].coverage is Coverage.NOT_COVERED  # flying to Accra
        assert out["q2"].evidence_index is None

    def test_every_question_gets_exactly_one_outcome(self, retained, week):
        out = overlap_based(week["questions"], retained)
        assert len(out) == len(week["questions"])
        assert {o.id for o in out} == {q["id"] for q in week["questions"]}

    def test_the_patients_own_marking_is_the_only_ranking(self, retained, week):
        """Second Chair does not decide which of your questions is clinically important."""
        out = overlap_based(week["questions"], retained)
        todos = carry_forward(out)
        missed = [o for o in out if o.coverage is not Coverage.COVERED]
        important = [o for o in missed if o.marked_important]
        assert todos[: len(important)] == [f"Ask at the next appointment: {o.text}" for o in important]

    def test_carry_forward_never_explains_why_a_question_matters(self, retained, week):
        for t in carry_forward(overlap_based(week["questions"], retained)):
            assert t.startswith("Ask at the next appointment: ")
            for word in ("because", "important", "risk", "interact", "danger"):
                assert word not in t.lower()


class TestReconciliationModelPath:
    def test_a_model_answer_is_used_when_it_covers_every_question(self, retained, week):
        payload = json.dumps(
            [{"id": q["id"], "coverage": "not_covered", "utterance_index": None} for q in week["questions"]]
        )
        out, call = reconcile(week["questions"], retained, Bedrock(invoke=stub_bedrock(payload), models=["m"]))
        assert call.used_model
        assert all(o.coverage is Coverage.NOT_COVERED for o in out)

    def test_a_partial_model_answer_falls_back(self, retained, week):
        payload = json.dumps([{"id": "q1", "coverage": "covered", "utterance_index": 0}])
        out, call = reconcile(week["questions"], retained, Bedrock(invoke=stub_bedrock(payload), models=["m"]))
        assert call.used_model is False
        assert len(out) == len(week["questions"])

    def test_covered_without_evidence_is_downgraded(self, retained, week):
        """A model may not mark something covered and decline to say where."""
        payload = json.dumps(
            [{"id": q["id"], "coverage": "covered", "utterance_index": None} for q in week["questions"]]
        )
        out, _ = reconcile(week["questions"], retained, Bedrock(invoke=stub_bedrock(payload), models=["m"]))
        assert all(o.coverage is Coverage.NOT_COVERED for o in out)


class TestAsk:
    def test_an_advice_question_is_refused_and_nothing_is_searched(self):
        a = ask_mod.answer("Should I stop the amlodipine?", [(0, "I'm going to stop that altogether")])
        assert a.refused
        assert a.text == REFUSAL
        assert a.quotes == []

    def test_a_record_question_returns_verbatim_lines(self):
        us = [(0, "we'll do one in six weeks to check the kidney function"), (1, "unrelated")]
        a = ask_mod.answer("What was said about the blood test in six weeks?", us)
        assert not a.refused
        assert a.quotes
        assert a.quotes[0][1] in [t for _, t in us]

    def test_the_model_only_chooses_indices_and_never_writes_the_answer(self):
        us = [(0, "the rate's been in the forties overnight"), (1, "filler")]
        b = Bedrock(invoke=stub_bedrock('{"indices": [0]}'), models=["m"])
        a = ask_mod.answer("what about the rate?", us, b)
        assert [t for _, t in a.quotes] == ["the rate's been in the forties overnight"]

    def test_an_index_the_model_invented_is_discarded(self):
        us = [(0, "the rate's been in the forties overnight")]
        b = Bedrock(invoke=stub_bedrock('{"indices": [42, 99]}'), models=["m"])
        a = ask_mod.answer("what about the rate?", us, b)
        assert all(i == 0 for i, _ in a.quotes)

    def test_nothing_relevant_says_so_rather_than_guessing(self):
        a = ask_mod.answer("What was said about the parking?", [(0, "the cholesterol number has crept up")])
        assert not a.refused
        assert a.quotes == []
        assert "Nothing in this appointment's record" in a.text

    def test_a_matched_question_brings_its_reply_along(self):
        """Word overlap finds the patient's own question. The answer is the next line."""
        us = [
            (0, "does she still need the blood test every month"),
            (1, "Not monthly, no. We'll do one in six weeks to check the kidney function."),
        ]
        a = ask_mod.answer("What was said about the blood test?", us)
        assert [i for i, _ in a.quotes] == [0, 1]

    def test_a_statement_does_not_drag_the_next_line_in(self):
        us = [(0, "the cholesterol number has crept up"), (1, "unrelated filler")]
        a = ask_mod.answer("what about the cholesterol number?", us)
        assert [i for i, _ in a.quotes] == [0]


class TestRelativeSummary:
    def test_the_stopped_medicine_comes_first(self):
        meds = [
            MedChange("atorvastatin", ChangeKind.INCREASED, "20mg", "40mg", "at night", "q1", 0),
            MedChange("amlodipine", ChangeKind.STOPPED, "5mg", None, None, "q2", 1),
        ]
        s = build_relative("your daughter", "Cardiology", "22 September 2026", meds, [])
        assert s.lines[0].heading == "Stopping"

    def test_every_line_carries_its_quote(self):
        meds = [MedChange("amlodipine", ChangeKind.STOPPED, "5mg", None, None, "the words used", 1)]
        s = build_relative("d", "l", "d", meds, [])
        assert 'Recorded in the room: "the words used"' in s.as_text()

    def test_an_unclear_row_does_not_pretend_to_be_clear(self):
        meds = [MedChange("the water tablet", ChangeKind.UNCLEAR, None, None, None, "leave that for now", 1)]
        s = build_relative("d", "l", "d", meds, [])
        assert "not said clearly enough" in s.lines[0].body

    def test_it_states_its_own_expiry(self):
        s = build_relative("d", "l", "d", [], [], retention_days=14)
        assert s.expires_at
        assert "stops working on" in s.as_text()
