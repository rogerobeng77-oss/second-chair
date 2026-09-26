"""A sentence each rule must catch, beside one it must not touch.

§7 class 3, and class 6 underneath it. The pair is what makes a rule legible rather than
"reject anything that looks a bit like this", and what stops a stricter guard emptying
the recap into a blank page with a proud notice under it.

Written against the hazard, never against the guard: `GUARD-STANDARD.md` §7 opens with
*"a guard test that feeds the guard its own list proves nothing."* Every blocked sentence
here was run before the guard changed, so the reproductions are facts, not claims.
"""

import pytest

from second_chair.advice import RULES, findings_for_sentence
from second_chair.guardrail import advice_guard
from test_advice import LEAKED_THROUGH_THE_OLD_GUARD


PAIRS = [
    # (must be blocked, must stay clean, which rule the pair is about)
    ("Stop the amlodipine tonight.",
     "The amlodipine is stopping. [u12]",
     "an imperative opening, against a gerund reporting the same event"),
    ("Keep taking the apixaban.",
     "Taken together, the readings were low overnight. [u5]",
     "a bare imperative, against a participle that opens a clause"),
    ("Ring the clinic if it comes back.",
     "Waiting times at the clinic were mentioned. [u21]",
     "a directive verb, against the same subject reported"),
    ("She can stop the amlodipine whenever she likes.",
     "The plan is to stop the amlodipine. [u12]",
     "latitude granted, against the room's plan reported"),
    ("It is best to skip tonight's tablet.",
     "A blood test is booked for six weeks from today to check kidney function. [u19]",
     "a recommendation, against an arrangement being reported"),
    ("This is nothing to worry about. [u1]",
     "Your heart rate has been sitting in the forties overnight. [u5]",
     "reassurance, against the reading it would have been about"),
    ("The dizziness is caused by the bisoprolol. [u1]",
     "The morning dizziness was linked to the bisoprolol, not the heart rhythm. [u7]",
     "a clinical relationship asserted, against one reported from the room"),
    ("You can fly in December. [u1]",
     "Flights in December were mentioned. [u9]",
     "permission to resume an activity, against the activity being named"),
    ("Ring 999 if it gets worse.",
     "The number for the clinic is on the appointment letter. [u22]",
     "emergency direction, against a number reported"),
    ("Reduce the dose to 2.5mg. [u1]",
     "The bisoprolol is going from 5mg to 2.5mg. [u4]",
     "a dosing instruction, against the same change reported"),
]


@pytest.mark.parametrize("bad,good,rule", PAIRS, ids=[p[2] for p in PAIRS])
def test_each_rule_has_a_sentence_it_must_not_touch(bad, good, rule):
    assert advice_guard(bad).findings, f"{rule}: missed {bad!r}"
    assert advice_guard(good).clean, f"{rule}: false positive on {good!r}"


def test_repeating_what_the_room_said_is_still_the_product():
    """The exemption that keeps this app worth using, and its two limits.

    A quotation inside a reporting frame is exempt. A quotation with nothing around it is
    not a report, it is an instruction with quote marks on it — and `collisions.py` runs
    this guard over Bee's own summariser output, where no citation guard follows to ask
    whether anyone actually said it.
    """
    assert advice_guard('This was said in the room: "Just stop. Five milligrams is a low dose." [u14]').clean
    assert advice_guard('Recorded in the room: "take half a tablet until that box runs out". [u5]').clean
    assert advice_guard('"Stop immediately, no taper needed."').findings
    assert advice_guard('"take half a tablet" was said, and you should double it if you feel dizzy. [u5]').findings

# §7 class 6 — non-vacuity. An absence asserted needs proof the search ran and proof the
# thing it looked for exists.

def test_the_guard_actually_has_rules_to_run():
    assert len(RULES) >= 8, "a shrinking rule table would make the clean cases vacuous"
    assert findings_for_sentence("Stop immediately, no taper needed."), "the search runs"
    assert findings_for_sentence("") == [], "and an empty sentence is not a finding"


def test_every_rule_fires_on_something():
    """The mutation check, automated (§7).

    Deleting any one rule has to turn at least one blocked sentence in `PAIRS` clean.
    A rule no sentence exercises is a rule the suite is not testing.
    """
    import second_chair.advice as advice

    all_blocked = [b for b, _, _ in PAIRS] + LEAKED_THROUGH_THE_OLD_GUARD
    for i, rule in enumerate(RULES):
        without = RULES[:i] + RULES[i + 1:]
        original = advice.RULES
        advice.RULES = without
        try:
            still_caught = all(advice.findings_for_sentence(s) for s in all_blocked)
        finally:
            advice.RULES = original
        assert not still_caught, f"{rule.__name__} can be deleted with the corpus still green"
