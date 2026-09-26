"""The promise, asserted on the rendered page, and every destination this app writes to.

§5 and §4. Not "the guard returns a finding" — that is a fact about a value on the
way to the promise, and a value can be right while the promise is false.

Written against the hazard, never against the guard: `GUARD-STANDARD.md` §7 opens with
*"a guard test that feeds the guard its own list proves nothing."* Every blocked sentence
here was run before the guard changed, so the reproductions are facts, not claims.
"""

import pytest
from fastapi.testclient import TestClient

from conftest import stub_bedrock


# --------------------------------------------------------------------------- §5 promise

class TestTheAdviceGuardKeepsItsPromise:
    """The promise, in the user's terms, asserted on the rendered page.

    *Second Chair never tells anyone what to do about a medicine, how much of it to take,
    or how worried to be.*

    Not "the guard returns a finding" — that is a fact about a value on the way to the
    promise, and a value can be right while the promise is false. The model is stubbed
    with the worst thing it could say, the real pipeline runs, and the assertion is about
    what a 71-year-old would read.

    The web fixture in `test_web.py` sets `model_tier = "off"`, which the guard standard
    lists as a shape that looks like a test and is not: the original defect was found by
    opening a page that had model output on it. This one leaves the model on.
    """

    WORST = (
        "Stop immediately, no taper needed. "
        "She can stop the amlodipine whenever she likes. [u0] "
        "There's no reason to worry about the dizziness. [u0] "
        "Going to A&E is the right call if it comes back. [u0]"
    )

    @pytest.fixture
    def page(self):
        from second_chair.bedrock import Bedrock
        from second_chair.web import create_app

        app = create_app()
        state = app.state.sc
        stub = Bedrock(invoke=stub_bedrock(self.WORST), models=("stub",))
        state.bedrock = lambda tier=None: stub
        state.settings.model_tier = "fast"
        return TestClient(app).get("/").text

    @pytest.mark.parametrize(
        "phrase",
        [
            "no taper needed",
            "whenever she likes",
            "no reason to worry",
            "the right call",
            "A&amp;E",
        ],
    )
    def test_nothing_the_model_advised_reaches_the_page(self, page, phrase):
        assert phrase not in page

    def test_the_page_still_renders_the_record(self, page):
        """Fail-closed must not mean fail-blank. A stricter guard that empties the page
        has traded one bad product for another."""
        assert "bisoprolol" in page
        assert "not a medical record" in page

    def test_the_page_says_something_was_removed(self, page):
        """§6: the refusal is reported, and reported in our words.

        The count is asserted, not the word, so that static template text cannot satisfy
        this. Four sentences went in and four were refused.
        """
        assert "4 sentences removed before you saw this" in page

    def test_the_model_path_really_ran(self, page):
        """Non-vacuity (§7 class 6). Every absence asserted above is worthless if the
        stub never reached the pipeline, so this proves the page is showing model
        output and naming where it came from."""
        assert "stub" in page, "the page does not name the model it used"

    def test_each_refused_sentence_is_named_by_rule_and_not_by_text(self, page):
        """§6 again, from the other side: what the guard caught is described in our
        words. `tells the reader what may be done about a medicine` is app-authored;
        the sentence it caught is not on the page at all."""
        assert "opens by telling the reader to do something" in page
        assert "reassures the reader about a symptom" in page
