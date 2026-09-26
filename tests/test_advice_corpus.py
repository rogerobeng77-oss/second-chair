"""The shared adversarial corpus, mapped onto this app's guards.

§8. The rows are the standard's, copied not imported, so a defect found in one app
is checked in all ten. A row this app does not claim carries a written reason:
silently not running one is how a corpus becomes decoration.

Written against the hazard, never against the guard: `GUARD-STANDARD.md` §7 opens with
*"a guard test that feeds the guard its own list proves nothing."* Every blocked sentence
here was run before the guard changed, so the reproductions are facts, not claims.
"""

import json
import pathlib

import pytest

from second_chair.guardrail import advice_guard

CORPUS = pathlib.Path(__file__).parent / "fixtures" / "adversarial.jsonl"


# --------------------------------------------------------------------------- §8 corpus

ROWS = [json.loads(line) for line in CORPUS.read_text(encoding="utf-8").splitlines() if line.strip()]

# Rows this app does not claim, each with a reason, and the reason is reviewed. Being
# explicit is the point: silently not running a row is how a corpus becomes decoration.
NOT_OURS = {
    "apos-01": "no care-visit attendance claim in this app; the sentence is Threshold's",
    "apos-02": "same — an attendance accusation, which Second Chair has no channel for",
    "para-01": "an accusation by arithmetic about visits, which this app does not make",
    "para-02": "a courier claim; no delivery surface here",
    "legal-01": "no statutory field; Incident Book owns this one",
    "pii-01": "no physical-description field; Incident Book owns this one",
    "health-02": "a fabricated mobility claim, which is Steady's guard, not an instruction",
    "health-03": "an exercise-tier contradiction; this app holds no exercise tier",
    "inject-01": "no markdown destination — see TestTheDestinationsThisAppWritesTo",
    "uni-01": "an attendance accusation again; the unicode half is covered by "
              "test_the_guard_sees_through_the_spelling",
    "uni-02": "same",
}


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_the_shared_adversarial_corpus(row):
    if row["id"] in NOT_OURS:
        pytest.skip(NOT_OURS[row["id"]])
    findings = advice_guard(row["text"]).findings
    if row["expect"] == "blocked":
        assert findings, f'{row["id"]} passed the guard: {row["why"]}'
    else:
        assert not findings, f'{row["id"]} was a false positive: {row["why"]}'


def test_the_corpus_is_the_one_from_the_standard():
    """Non-vacuity again: a corpus file that shrank would make every skip above pass."""
    assert len(ROWS) == 19
    assert {r["id"] for r in ROWS} >= {"advice-01", "advice-02", "advice-03", "clean-02"}
    assert len(NOT_OURS) < len(ROWS), "an app that claims no rows is not running the corpus"
