"""The row's prose fields as spans the app slices out of the utterance.

`GUARD-STANDARD.md` §1 rung S3. The model says *which* stretch of the room matters; the
app does the slicing. Checking the model's string and then printing the model's string
leaves a gap wide enough for a lookalike letter — `bisoprоlol` with a Cyrillic о folds to
a match and then renders as a drug name nobody wrote.

`TestTheRuleBasedPathIsUnharmed` is the other half. A guard that tightens until the
offline path stops producing rows has broken the product in order to protect it.
"""

from meds_rows import REDUCING, row, utt
from second_chair.meds import rule_based, verify


class TestTheProseFieldsAreSlicedFromTheSource:
    """§1 S3. The model says *which* span; the app does the slicing.

    Checking the model's string and then printing the model's string leaves a gap wide
    enough for a lookalike letter: `bisoprоlol` with a Cyrillic о folds to a match and
    then renders as a drug name nobody wrote.
    """

    SOURCE = "right, we’ll bring the bisoprolol down from five milligrams, in the morning"

    def test_the_quote_comes_back_in_the_rooms_own_spelling(self):
        kept = verify([row(source=self.SOURCE, quote="we'll bring the bisoprolol down")], [utt(self.SOURCE)])
        assert kept[0].quote == "we’ll bring the bisoprolol down"

    def test_a_lookalike_in_a_drug_name_cannot_reach_the_page(self):
        kept = verify(
            [row(source=self.SOURCE, quote="bring the bisoprolol down", medicine="bisoprоlol")],
            [utt(self.SOURCE)],
        )
        assert kept[0].medicine == "bisoprolol"
        assert all(ord(c) < 128 for c in kept[0].medicine)

    def test_timing_is_a_span_too(self):
        kept = verify(
            [row(source=self.SOURCE, quote="bring the bisoprolol down", timing="in the morning")],
            [utt(self.SOURCE)],
        )
        assert kept[0].timing == "in the morning"

    def test_a_span_that_is_not_in_the_utterance_loses_the_row(self):
        kept = verify([row(source=self.SOURCE, quote="stop the bisoprolol altogether")], [utt(self.SOURCE)])
        assert kept == []

    def test_a_timing_that_is_not_in_the_utterance_loses_the_field_not_the_row(self):
        kept = verify(
            [row(source=self.SOURCE, quote="bring the bisoprolol down", timing="twice a day")],
            [utt(self.SOURCE)],
        )
        assert len(kept) == 1
        assert kept[0].timing is None


class TestTheRuleBasedPathIsUnharmed:
    """A guard that tightens until the offline path stops producing rows has broken the
    product to protect it. The fallback's kinds come from the same stems the agreement
    check reads, so it cannot fail that check by construction — this is what proves it."""

    UTTERANCES = [
        utt("right, we'll bring the bisoprolol down from five milligrams to two point five"),
        utt("the apixaban does not change, exactly as you are"),
        utt("and we'll start you on the ramipril, two point five milligrams"),
        utt("I want you to come off the amlodipine altogether"),
    ]

    def test_every_row_the_rules_produce_survives_the_verifier(self):
        rows = rule_based(self.UTTERANCES)
        assert rows, "the fallback produced nothing, so this test proves nothing"
        assert len(verify(rows, self.UTTERANCES)) == len(rows)

    def test_the_fallback_still_names_all_four_medicines(self):
        kept = verify(rule_based(self.UTTERANCES), self.UTTERANCES)
        assert {r.medicine for r in kept} == {"bisoprolol", "apixaban", "ramipril", "amlodipine"}
