"""A true dose was dropped, and a wrong one reached the carer's page.

Reproduced end to end against `relative.build`, which is the document a daughter three
hundred miles away acts on, before anything was changed:

    utterance: "we will bring the bisoprolol down from five milligrams to two point five"
    model row:  reduced, from_dose "2.5mg", to_dose "5mg"    (the pair swapped)
    rendered:   Dose goes down: bisoprolol: 5mg

The old dose, printed as the new one, under a true quote. A carer reading that keeps
giving 5mg of a beta blocker to someone whose dose was just halved.

Two faults, one upstream of the other:

1. `_doses_in` read only a number that carried its own unit, and nobody says the unit
   twice. "from five milligrams to two point five" grounded 5mg and not 2.5mg, so a dose
   the room really did give was dropped from a truthful row. `GUARD-STANDARD.md` §3 uses
   this very sentence as its worked example of a number that must ground.

2. Because 2.5mg would not ground, the model's `from_dose` was blanked for failing to
   ground — and `_direction_agrees`, which exists to blank both doses when they
   contradict the heading, needs two numbers and so never fired. The survivor went to the
   page carrying the authority of a checked value.

The fix judges the pair the model offered, before grounding can take one away.
"""

import pytest

from second_chair.bee import Utterance
from second_chair.meds import ChangeKind, MedChange, verify, _doses_in
from second_chair import relative as rel


SAID = "we will bring the bisoprolol down from five milligrams to two point five"
UTTERANCES = [Utterance(conversation_uuid="c", created_at_ms=1, text=SAID)]


def row(**kw) -> MedChange:
    base = dict(
        medicine="bisoprolol", kind=ChangeKind.REDUCED, from_dose="5mg", to_dose="2.5mg",
        timing=None, quote="bring the bisoprolol down", utterance_index=0,
    )
    base.update(kw)
    return MedChange(**base)


@pytest.mark.parametrize(
    "text,expected",
    [
        (SAID, {"5mg", "2.5mg"}),
        ("from two point five to five milligrams", {"2.5mg", "5mg"}),
        ("bring it down from 5mg to 2.5", {"5mg", "2.5mg"}),
        ("from ten milligrams to twenty five milligrams", {"10mg", "25mg"}),
        # The unit is only inherited across an explicit from/to. A bare number on its own
        # grounds nothing, or the allowlist would start accepting arithmetic.
        ("the dose stays at five milligrams", {"5mg"}),
        ("she has six tablets left", set()),
    ],
)
def test_a_unit_said_once_counts_for_both_halves_of_a_pair(text, expected):
    assert _doses_in(text) == expected


def test_a_whole_number_is_not_read_as_a_prefix_of_a_longer_one():
    """Non-vacuity for the pattern above: `10` must not offer `1`.

    Without the guards this grounded a phantom 1mg, which would have let a model write
    "1mg" beside a true quote and have it pass.
    """
    assert _doses_in("we are going from five milligrams to ten milligrams") == {"5mg", "10mg"}


def test_the_dose_the_room_gave_survives_a_truthful_row():
    """The half of this that is about not shrinking the product.

    Before the fix this row came back as ('5mg', None): the guard silently deleted a true
    dose because the room had not repeated the word "milligrams".
    """
    [got] = verify([row()], UTTERANCES)
    assert (got.from_dose, got.to_dose) == ("5mg", "2.5mg")


def test_a_swapped_pair_takes_both_doses_down_with_it():
    [got] = verify([row(from_dose="2.5mg", to_dose="5mg")], UTTERANCES)
    assert got.from_dose is None and got.to_dose is None
    assert got.quote, "the quote is still true and still shown"


def test_the_carer_is_never_sent_the_old_dose_as_the_new_one():
    """§5: assert the promise on the rendered artefact, not on a dataclass two layers up."""
    rows = verify([row(from_dose="2.5mg", to_dose="5mg")], UTTERANCES)
    text = rel.build("your daughter", "Cardiology follow-up", "18 March 2026", rows, []).as_text()
    assert "5mg" not in text
    assert "bisoprolol" in text, "the row is still reported"
    assert "bring the bisoprolol down" in text, "and so is what was actually said"


def test_an_invented_dose_does_not_take_a_true_one_with_it():
    """The paired false positive for the rule above.

    A contradiction is a reason to drop both. One invented number beside one true one is
    not: 2.5mg is what the room said and is worth printing whatever nonsense arrived
    next to it. Without this pair the rule above reads as "drop a dose whenever anything
    is wrong", which would empty the panel.
    """
    [got] = verify([row(from_dose="7.5mg")], UTTERANCES)
    assert got.from_dose is None
    assert got.to_dose == "2.5mg"


def test_a_doubled_beta_blocker_is_still_refused():
    """The §3 hazard this all started from, checked from the other direction."""
    [got] = verify([row(to_dose="10mg")], UTTERANCES)
    assert got.to_dose is None
