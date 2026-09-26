"""The guards. These are the tests that stop the app practising medicine."""

import json

import pytest

from conftest import utt
from second_chair.guardrail import (
    REFUSAL,
    advice_guard,
    attribution_guard,
    check_question,
    citation_guard,
    split_sentences,
)


class TestAdviceGuard:
    @pytest.mark.parametrize(
        "text",
        [
            "You should take half a tablet in the morning. [u1]",
            "You need to stop the amlodipine today. [u1]",
            "Reduce the dose to 2.5mg. [u1]",
            "This is nothing to worry about. [u1]",
            "That's normal after a change like this. [u1]",
            "You can fly in December. [u1]",
            "The dizziness is caused by the bisoprolol. [u1]",
        ],
    )
    def test_generated_advice_is_caught(self, text):
        assert advice_guard(text).findings

    def test_a_verbatim_quote_of_an_instruction_is_allowed_through(self):
        """The whole product is repeating what was said. Quoting is not advising."""
        text = 'Recorded in the room: "take half a tablet until that box runs out". [u5]'
        assert advice_guard(text).clean

    def test_a_quote_does_not_launder_a_sentence_outside_it(self):
        text = '"take half a tablet" was said, and you should double it if you feel dizzy. [u5]'
        assert advice_guard(text).findings

    def test_plain_reporting_passes(self):
        assert advice_guard("The bisoprolol is going from 5mg to 2.5mg. [u4]").clean


class TestAttributionGuard:
    @pytest.mark.parametrize(
        "text",
        [
            "The doctor said the dose is halving. [u4]",
            "Your consultant explained the reason. [u4]",
            "She told you to ring the clinic. [u4]",
            "The patient asked about the blood test. [u4]",
        ],
    )
    def test_naming_a_speaker_is_caught(self, text):
        assert attribution_guard(text).findings

    def test_the_permitted_frame_passes(self):
        assert attribution_guard("This was said in the room at 09:14. [u4]").clean

    def test_bees_own_unknown_label_is_why(self):
        """Every speaker in the wire data is Unknown, so any attribution is invented."""
        assert attribution_guard("Someone in the room said it. [u4]").clean


class TestCitationGuard:
    def test_an_uncited_sentence_is_removed(self):
        us = [utt("the rate's been sitting lower than I'd like")]
        r = citation_guard("Your rate has been low.", us)
        assert r.text == ""
        assert r.findings[0].kind == "uncited"

    def test_a_citation_out_of_range_is_removed(self):
        us = [utt("the rate's been sitting lower than I'd like")]
        r = citation_guard("Your rate has been low. [u7]", us)
        assert r.text == ""
        assert r.findings[0].kind == "bad-citation"

    def test_a_citation_that_does_not_support_the_sentence_is_removed(self):
        us = [utt("we're moving to room 214 next week")]
        r = citation_guard("Your cholesterol number has crept up. [u0]", us)
        assert r.text == ""
        assert r.findings[0].kind == "unsupported"

    def test_a_supported_sentence_survives(self):
        us = [utt("the rate's been sitting lower than I'd like, forties overnight")]
        r = citation_guard("Your rate has been in the forties overnight. [u0]", us)
        assert "forties" in r.text
        assert r.clean

    def test_a_fabricated_quote_is_removed_even_with_a_valid_citation(self):
        """A model that invents a quote and staples a real index to it is the failure
        this guard exists for."""
        us = [utt("I'm going to stop the amlodipine altogether")]
        r = citation_guard('It said "stop the apixaban altogether". [u0]', us)
        assert r.text == ""

    def test_a_sentence_may_draw_on_two_utterances(self):
        us = [utt("we'll do one in six weeks to check the kidney function"), utt("From today.")]
        r = citation_guard("A blood test is booked six weeks from today. [u0][u1]", us)
        assert r.clean


class TestQuestionRefusal:
    @pytest.mark.parametrize(
        "q",
        [
            "Should I stop the amlodipine?",
            "Can I take ibuprofen with this?",
            "Is it safe to fly?",
            "What happens if I skip a dose?",
            "Is this serious?",
            "Do I need to worry about the dizziness?",
        ],
    )
    def test_advice_questions_are_refused_before_anything_is_searched(self, q):
        assert check_question(q) == REFUSAL

    @pytest.mark.parametrize(
        "q",
        [
            "What was said about the blood test?",
            "When is the next appointment?",
            "What did they say about the amlodipine?",
            "Was the apixaban mentioned?",
        ],
    )
    def test_questions_about_the_record_are_allowed(self, q):
        assert check_question(q) is None


class TestSentenceSplitting:
    def test_consecutive_citations_stay_with_their_sentence(self):
        out = split_sentences("A test is booked. [u15][u17] Another thing. [u20]")
        assert len(out) == 2
        assert "[u15]" in out[0] and "[u17]" in out[0]

    def test_a_full_stop_inside_a_quotation_is_not_a_boundary(self):
        out = split_sentences('It said "ring the clinic. The number is on the letter." [u19] Done. [u20]')
        assert len(out) == 2
        assert out[0].count(".") >= 2

    def test_an_abbreviation_is_not_a_boundary(self):
        assert len(split_sentences("Dr. Halvorsen was not named here. [u0]")) == 1


class TestTheRefusalIsNotAnOutputChannel:
    """A guard that shows its work is good. A guard that reprints what it caught is a
    second way for that text to reach the page.

    The recap pipeline used to run the advice guard only over the sentences the citation
    guard had already kept, so an uncited dosing instruction was removed without ever
    being scanned for advice, and then printed in full under "N sentences removed before
    you saw this". These pin both halves: every sentence is scanned by every guard, and
    no refused sentence crosses into the template.
    """

    DANGEROUS = "Stop the apixaban immediately, it is causing the bleeding."

    @pytest.fixture
    def retained(self, session, client):
        from second_chair.consent import establish, scope_filter

        in_scope, _ = scope_filter(session, list(client.stream()))
        _, kept = establish(session, in_scope)
        return kept

    def _recap(self, utterances):
        from second_chair.bedrock import ModelCall
        from second_chair.recap import _finish

        return _finish(
            f"{self.DANGEROUS} Your heart rate has been low overnight. [u0]",
            ModelCall("m", True, None),
            utterances,
        )

    def test_an_uncited_advice_sentence_is_also_recorded_as_advice(self, retained):
        r = self._recap(retained)
        kinds = {f.kind for f in r.findings if f.ordinal == 1}
        assert "uncited" in kinds, "the citation guard still fires"
        assert "advice" in kinds, "and the advice guard is no longer skipped by ordering"

    def test_the_refused_sentence_never_reaches_the_page(self, retained):
        from second_chair.pipeline import Result, derived_dict
        from second_chair.consent import Session

        r = self._recap(retained)
        result = Result(session=Session("c", 0, 1, "x"), consent=None, retained=retained, dropped=[])
        result.recap = r
        payload = derived_dict(result)

        assert payload["recap"]["findings"], "the finding is still reported"
        for f in payload["recap"]["findings"]:
            assert "text" not in f
        assert "apixaban" not in json.dumps(payload["recap"]["findings"])

    def test_the_refused_sentence_is_not_in_the_recap_either(self, retained):
        r = self._recap(retained)
        assert self.DANGEROUS not in r.text
        assert all(self.DANGEROUS not in s for s in r.sentences)


class TestTheAdviceGuardAgainstSentencesItWasNotBuiltFrom:
    """A denylist tested only against its own entries proves the list contains the list.

    These are written the other way round: sentences a model plausibly produces, none of
    them lifted from `_ADVICE`, including the one from the friction log that started this
    whole thread and that every earlier rule missed.
    """

    @pytest.mark.parametrize(
        "sentence",
        [
            "Stop immediately, no taper needed.",
            "Take half a five milligram tablet until current box runs out.",
            "Stop the apixaban immediately, it is causing the bleeding.",
            "Do not wait for the six weeks.",
            "Ring the clinic if it comes back.",
            "Keep taking the apixaban.",
            "Don't stop the tablets before speaking to anyone.",
            "Skip tonight's and start again tomorrow.",
        ],
    )
    def test_generated_instructions_are_caught(self, sentence):
        assert advice_guard(sentence).findings, sentence

    @pytest.mark.parametrize(
        "sentence",
        [
            "The morning dizziness was linked to the bisoprolol, not the heart rhythm. [u7]",
            "A blood test is booked for six weeks from today to check kidney function. [u19]",
            "Taken together, the readings were low overnight. [u5]",
            "Waiting times at the clinic were mentioned. [u21]",
            "The amlodipine is stopping. [u12]",
            "Your heart rate has been sitting in the forties overnight. [u5]",
            "The cholesterol number has gone up. [u17]",
        ],
    )
    def test_reporting_what_was_said_is_not_advice(self, sentence):
        """The guard has to stay narrow or the recap empties itself and the product is a
        blank page with a proud explanation underneath it."""
        assert not advice_guard(sentence).findings, sentence

    def test_a_verbatim_quote_is_still_allowed_to_instruct(self):
        """Repeating what the room actually said is the product. Only generated prose is
        held to this."""
        said = 'This was said in the room: "Just stop. Five milligrams is a low dose." [u14]'
        assert not advice_guard(said).findings
