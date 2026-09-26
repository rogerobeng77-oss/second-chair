"""`normalise.py` against the outputs the guard standard states for it.

§2. The standard says every Python snippet in it was executed and the stated output
is what came back. These are those outputs, asserted here so that a change to the
module has to argue with the document.

"""

import pytest

from second_chair.normalise import normalise, prepare


# --------------------------------------------------------------------------- §2 module

class TestTheMatchingCopy:
    """`normalise.py` against the outputs the guard standard states for it.

    The standard says every Python snippet in it was executed and the stated output is
    what came back. These are those outputs, asserted here so a change to the module has
    to argue with the document.
    """

    @pytest.mark.parametrize(
        "text",
        [
            "She's not recorded at the front door at 8.",
            "She’s not recorded at the front door at 8.",
            "She' s not recorded at the front door at 8.",
            "S​he's not recorded",
            "Ѕhe's not recorded",
        ],
    )
    def test_every_spelling_of_a_contraction_reaches_the_same_comparison(self, text):
        assert prepare(text).has_word("she")

    def test_a_possessive_does_not_hide_the_word_inside_it(self):
        assert prepare("The carer’s booked time shows nothing").has_word("carer's")

    def test_a_hyphen_does_not_hide_the_word_inside_it(self):
        assert prepare("non-attendance").has_word("attendance")

    def test_an_apostrophe_in_the_middle_of_a_phrase_is_not_a_hole(self):
        assert prepare("did' not come").has_phrase("did not")
        assert prepare("The  carer   did  not   come").has_phrase("did not")

    def test_the_paired_false_positive(self):
        assert not prepare("o'clock is fine").has_word("she")

    def test_a_letter_the_fold_table_does_not_know_is_reported(self):
        assert prepare("dıd not").foreign_letters() == ["ı"]
        assert prepare("did not").foreign_letters() == []

    def test_normalising_twice_changes_nothing(self):
        """A property, over generated input rather than a hand-written table."""
        import itertools

        pieces = ["She's", "did’ not", "ca​rer", "Ѕhe", "non-attendance", "  ", "ａ"]
        for combo in itertools.permutations(pieces, 3):
            s = " ".join(combo)
            assert normalise(normalise(s)) == normalise(s)

    def test_no_invisible_character_can_hide_a_banned_word(self):
        for filler in ("", "x", "the carer ", "a" * 40):
            hidden = "".join(c + "​" for c in "did not come")
            assert prepare(filler + hidden).has_phrase("did not come")
