"""The sentences the old advice guard let through.

§7 classes 1 and 2: an external corpus, and variants of everything the guard does
catch. The paired false positives that keep these rules honest are in
`test_advice_pairs.py`, and the promise they are all on the way to is asserted in
`test_advice_boundary.py`.

Written against the hazard, never against the guard: `GUARD-STANDARD.md` §7 opens with
*"a guard test that feeds the guard its own list proves nothing."* Every blocked sentence
here was run before the guard changed, so the reproductions are facts, not claims.
"""

import pytest

from second_chair.guardrail import advice_guard


# §7 class 1 — an external corpus. No sentence here contains a token from the old
# denylist, and none was written by looking at the new one.

LEAKED_THROUGH_THE_OLD_GUARD = [
    # The founding incident, moved off byte zero. `^` was anchored to the whole string,
    # so the guard only ever judged the first sentence of a paragraph.
    "The plan was agreed. Stop immediately, no taper needed.",
    # The register a model actually writes in: third person, hedged, permissive.
    "She can stop the amlodipine whenever she likes.",
    "He is fine to stop the bisoprolol now.",
    "There is no need to keep taking the apixaban.",
    "The apixaban can be stopped tonight.",
    "The safest thing is to come off it straight away.",
    "Doubling up on the amlodipine would be unwise.",
    # Severity and reassurance, which is the clinic's call and not a summariser's.
    "There's no reason to worry about the dizziness.",
    "Nothing to be concerned about here.",
    "This is not an emergency.",
    # Sending somebody to hospital without using a listed verb.
    "Going to A&E is the right call if the bleeding starts again.",
    # Permission to resume an activity.
    "She is able to drive again from Monday.",
    # Spellings of sentences the old suite listed as caught.
    "Don’t stop the tablets before speaking to anyone.",       # curly apostrophe
    "Stоp immediately, no taper needed.",                      # Cyrillic о
    "S​top immediately, no taper needed.",                     # zero-width space
    "“Stop immediately, no taper needed.”",               # nothing but a quotation
]


@pytest.mark.parametrize("sentence", LEAKED_THROUGH_THE_OLD_GUARD)
def test_every_sentence_the_old_guard_missed(sentence):
    assert advice_guard(sentence).findings, sentence


def test_the_founding_incident_is_caught_wherever_it_sits_in_a_paragraph():
    """The founding incident behind this guard, in every position a model might put it.

    A free-text field once let a model write a generated dosing instruction
    ("Stop immediately, no taper needed.") straight onto a patient-facing screen. This
    is the reproduction that mattered most. A guard anchored to the start of a string is
    a guard on sentence one, and a recap has six.
    """
    sentence = "Stop immediately, no taper needed."
    for prefix in ("", "The dose was discussed. ", "This was agreed today. It is short. "):
        assert advice_guard(prefix + sentence).findings, prefix

# §7 class 2 — variants of everything the guard does catch. A generated table, not forty
# hand-written tests, and the ones that fail are the guard's real perimeter.

VARIANTS = [
    ("plain", lambda s: s),
    ("curly", lambda s: s.replace("'", "’")),
    ("spaced-apostrophe", lambda s: s.replace("'", "' ")),
    ("zero-width", lambda s: s.replace(" ", "​ ", 1)),
    ("lookalike", lambda s: s.replace("o", "о", 1)),
    ("double-space", lambda s: s.replace(" ", "  ")),
    ("full-width", lambda s: s.replace("a", "ａ")),
    ("upper", lambda s: s.upper()),
    ("non-breaking-space", lambda s: s.replace(" ", " ")),
]

BLOCKED = [
    "Stop immediately, no taper needed.",
    "Don't stop the tablets before speaking to anyone.",
    "She can stop the amlodipine whenever she likes.",
    "You should take half a tablet in the morning.",
    "There's no reason to worry about the dizziness.",
]


@pytest.mark.parametrize("name,mutate", VARIANTS, ids=[v[0] for v in VARIANTS])
@pytest.mark.parametrize("sentence", BLOCKED)
def test_the_guard_sees_through_the_spelling(sentence, name, mutate):
    assert advice_guard(mutate(sentence)).findings, f"{name}: {mutate(sentence)!r}"


def test_a_script_the_guard_cannot_read_is_refused_rather_than_folded():
    """A fold table is a denylist wearing different clothes (§2).

    `dıd` with a dotless Turkish ı survives the lookalike map and so will the next one.
    The allowlist version refuses anything still outside a-z after folding, which is why
    this test does not name a rule: the point is that no rule had to.
    """
    assert advice_guard("The bisoprolol ıs beıng stopped.").findings
