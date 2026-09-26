"""`kind` as a closed set that has to agree with the quote beside it.

`GUARD-STANDARD.md` §1 puts this field on rung S1 **plus an agreement check**, and says
why: *"the enum is closed and nothing checks it agrees with the quote; flip it and the
heading reads 'Stopping' over a true quote."*

Every test here went red against the code before the change. The mutation check in
`test_the_agreement_check_is_load_bearing` is why the file exists: `_kind_agrees` could
be deleted with all 329 other tests green.
"""

import json

import pytest

from meds_rows import NO_DIRECTION, REDUCING, row, utt
from second_chair.meds import ChangeKind, HUMAN, verify, _parse


class TestTheHeadingHasToAgreeWithTheQuote:
    """Every other field on the row was grounded, and the heading was not.

    `HUMAN[kind]` is the largest word on the card. A model can copy a quote character for
    character and file it under a heading that says the opposite: "Stopping" over "bring
    the bisoprolol down from five to two point five" is a stopped beta blocker on a
    71-year-old's page, assembled entirely out of true parts.
    """

    @pytest.mark.parametrize(
        "kind",
        [ChangeKind.STOPPED, ChangeKind.STARTED, ChangeKind.INCREASED, ChangeKind.UNCHANGED],
        ids=lambda k: k.value,
    )
    def test_a_heading_the_utterance_contradicts_is_refused(self, kind):
        kept = verify([row(kind=kind)], [utt(REDUCING)])
        assert kept == [], f"{HUMAN[kind]!r} survived over a quote that says the opposite"

    def test_the_true_heading_survives(self):
        kept = verify([row(kind=ChangeKind.REDUCED)], [utt(REDUCING)])
        assert [HUMAN[r.kind] for r in kept] == ["Dose goes down"]

    def test_swapping_the_doses_does_not_rescue_a_wrong_heading(self):
        """The doubled beta blocker, from the other direction: both numbers were said, so
        grounding alone lets them through in either order."""
        kept = verify([row(kind=ChangeKind.INCREASED, from_dose="2.5mg", to_dose="5mg")], [utt(REDUCING)])
        assert kept == []

    def test_an_utterance_naming_no_direction_contradicts_nothing(self):
        """This refuses what it can prove wrong, not what it cannot read. An utterance
        with no direction in it supports every heading, because it denies none."""
        for kind in ChangeKind:
            kept = verify([row(source=NO_DIRECTION, kind=kind)], [utt(NO_DIRECTION)])
            assert len(kept) == 1, f"{kind.value} was refused with nothing to refuse it"

    def test_unclear_is_never_refused_by_this_rule(self):
        kept = verify([row(kind=ChangeKind.UNCLEAR)], [utt(REDUCING)])
        assert [r.kind for r in kept] == [ChangeKind.UNCLEAR]

    def test_the_agreement_check_is_load_bearing(self):
        """The mutation check from §7, written down so it does not have to be repeated by
        hand. Disable the rule and at least one case above has to go red."""
        import second_chair.meds as meds

        original = meds._kind_agrees
        meds._kind_agrees = lambda r, source: True
        try:
            leaked = verify([row(kind=ChangeKind.STOPPED)], [utt(REDUCING)])
        finally:
            meds._kind_agrees = original
        assert leaked, "the rule is a no-op — these tests would pass without it"
        assert HUMAN[leaked[0].kind] == "Stopping"


class TestAnUnknownMemberRefusesTheRow:
    """§1: *"Refuse, do not coerce."* Landing a member we did not write on `unclear`
    renders a row headed "Not clear from the recording", which reads as the model being
    careful when in fact it did something nobody modelled."""

    def _raw(self, kind: object) -> str:
        return json.dumps([{"medicine": "bisoprolol", "kind": kind,
                            "quote": REDUCING, "utterance_index": 0}])

    @pytest.mark.parametrize("kind", ["increase_a_lot", "", "REDUCED_SLIGHTLY", 3, None, True])
    def test_a_member_we_did_not_write_loses_the_row(self, kind):
        assert _parse(self._raw(kind), [utt(REDUCING)]) == []

    def test_a_member_we_did_write_is_kept(self):
        rows = _parse(self._raw("reduced"), [utt(REDUCING)])
        assert [r.kind for r in rows] == [ChangeKind.REDUCED]
