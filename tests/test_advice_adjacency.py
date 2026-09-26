"""Two holes found by a corpus written without looking at the guard.

`GUARD-STANDARD.md` §7 class 2: "variants of everything the guard does catch". Both of
these are holes in rules that already existed and already had tests — the rules were
correct on the spellings their author pictured and silent on the ones next to them, which
is the whole subject of the standard.

Every sentence below was run against the guard before it changed, and again through
`recap.build` onto the rendered page, so the reproductions are facts rather than claims.
The command was:

    ./venv/bin/python -c "from second_chair.guardrail import advice_guard; \
        print(advice_guard('You really should stop the apixaban.').findings)"

and it printed `[]`.
"""

import json

import pytest

from second_chair.bedrock import Bedrock
from second_chair.bee import Utterance
from second_chair.guardrail import advice_guard
from second_chair import recap as recap_mod


# ---------------------------------------------------------------- A. adjacency

# The rule matched "you should" and "you need to" as fixed strings against the normalised
# copy, so it demanded the subject and the obligation be adjacent. One adverb defeated
# the most basic rule in the guard.
OBLIGATION_WITH_SOMETHING_IN_THE_WAY = [
    "You will need to stop the apixaban before the procedure.",
    "You'll need to stop the apixaban.",
    "You would need to stop the apixaban.",
    "You might want to stop the apixaban.",
    "You really should stop the apixaban.",
    "You just need to halve it.",
    "You are going to need to stop it.",
    "You probably should come off it.",
    "You will have to skip tonight's dose.",
    "She would need to stop the amlodipine first.",
    "They should really keep taking it.",
]


@pytest.mark.parametrize("sentence", OBLIGATION_WITH_SOMETHING_IN_THE_WAY)
def test_an_adverb_does_not_get_between_a_subject_and_an_obligation(sentence):
    assert advice_guard(sentence).findings, sentence


def test_the_adjacent_spellings_still_fire(sentence=None):
    """Non-vacuity: the cases the old rule did catch must not have been lost."""
    for s in ("You should stop the apixaban.", "You need to stop the apixaban."):
        assert advice_guard(s).findings, s


# The gap is an allowlist of auxiliaries and adverbs, not `\w+`, so the rule cannot leap
# a clause. Without this pair the rule above reads as "reject any sentence with 'you' and
# 'should' anywhere in it".
NOT_AN_OBLIGATION = [
    "You were given a leaflet about the blood test. [u3]",
    "You came in at nine and the clinic ran late. [u1]",
    "They were told the results would take a week. [u8]",
    "She was in the room for the whole appointment. [u2]",
]


@pytest.mark.parametrize("sentence", NOT_AN_OBLIGATION)
def test_a_subject_and_a_distant_verb_are_not_an_instruction(sentence):
    assert not advice_guard(sentence).findings, sentence


# ---------------------------------------------------------------- B. degree

# `SEVERITY` listed the positive degree only. This is §7's worked example — "unsteady and
# wobbly are listed; shaky is not" — landing in the class `advice_words.py` itself calls
# the one the product is least allowed to enter.
SEVERITY_IN_ANOTHER_DEGREE = [
    "Two point five is a safer dose for her.",
    "Five milligrams is the safest choice.",
    "Ten milligrams is more dangerous for her.",
    "Staying on ten milligrams would be riskier.",
    "The higher dose is worse for the kidneys.",
    "The lower dose is the better option here.",
    "Taking it with food is better.",
    "Coming off it is less risky than staying on.",
]


@pytest.mark.parametrize("sentence", SEVERITY_IN_ANOTHER_DEGREE)
def test_the_comparative_is_the_same_judgement_as_the_positive(sentence):
    assert advice_guard(sentence).findings, sentence


# A bare "better" or "worse" is ordinary English. Without this pair the rule above would
# empty the recap of every true sentence about a reading going the right way.
COMPARATIVES_THAT_REPORT = [
    "The readings were better overnight. [u5]",
    "The cholesterol number is worse than last time. [u17]",
    "Waiting times were worse than they had been. [u21]",
    "Her sleep has been better since the appointment. [u9]",
]


@pytest.mark.parametrize("sentence", COMPARATIVES_THAT_REPORT)
def test_a_comparative_about_a_reading_is_still_reportable(sentence):
    assert not advice_guard(sentence).findings, sentence


# ---------------------------------------------------------------- the rendered page

def _stub(payload: str):
    def invoke(model_id: str, body: str) -> str:
        return json.dumps({"content": [{"type": "text", "text": payload}]})

    return invoke


UTTERANCES = [
    Utterance(conversation_uuid="c", created_at_ms=1,
              text="we will bring the apixaban down before the procedure"),
    Utterance(conversation_uuid="c", created_at_ms=2,
              text="two point five milligrams is what we will use"),
]

REACHED_THE_PAGE = [
    "You will need to stop the apixaban before the procedure. [u0]",
    "You really should stop the apixaban before the procedure. [u0]",
    "Two point five is a safer dose for her. [u1]",
    "Five milligrams is the safest choice. [u1]",
]


@pytest.mark.parametrize("draft", REACHED_THE_PAGE)
def test_the_sentence_does_not_reach_the_recap_a_patient_reads(draft):
    """§5: the guard proved correct is not the guard proved applied.

    Each of these was rendered onto the page by `recap.build` with a valid citation
    before the rules changed. Asserting on `r.text` rather than on a finding is the
    point — the promise is about what a 71-year-old reads, not about a list.
    """
    r = recap_mod.build(UTTERANCES, Bedrock(invoke=_stub(draft)))
    body = draft.split(" [u")[0]
    assert body not in r.text
    assert all(body not in s for s in r.sentences)
    assert r.findings, "removed, but without saying why"
    # §6: the page learns which rule fired, never the sentence that fired it.
    for f in r.findings:
        assert "apixaban" not in f.as_dict().get("detail", "")
        assert "text" not in f.as_dict()
