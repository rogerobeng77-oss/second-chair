"""The advice guard.

**The promise, in the reader's terms:** Second Chair never tells anyone what to do about
a medicine, how much of it to take, or how worried to be. Only the room does that, and
only in the room's own words.

`TestTheAdviceGuardKeepsItsPromise` asserts that sentence against the rendered page. A
rule can be right while the promise is false, which is why the test is there and not on a
dataclass two layers up, per this project's shared guard-design standard. A 2026 study found
potential harms in 18% of model-generated discharge instructions: Second Chair may be a
better record of what was said, and may not be a worse doctor.

## What was here before, and why it was replaced

Eleven regexes, and a test file holding one case per regex, which proves only that the
list contains the list. Sixteen sentences written without looking at it walked straight
through, each reproduced at a terminal first and each listed with its reason in
`tests/test_advice.py::LEAKED_THROUGH_THE_OLD_GUARD` — the only copy, because a table
kept in two places disagrees with itself by the second change.

Three faults underneath them, three of the four the guard standard names:

1. The bare-imperative rule was anchored `^` against the whole string, so it judged the
   first sentence of a paragraph and nothing else: `"The plan was agreed. Stop
   immediately, no taper needed."` came back clean. The recap path fed it one sentence at
   a time and so was covered; `collisions.py`, which runs this guard over Bee's own
   summariser output, was not.
2. Every rule matched raw bytes the model chose, so an apostrophe or a Cyrillic letter
   walked through (§2). The suite listed `"Don't stop the tablets..."` as caught; the
   same sentence with a curly apostrophe was not.
3. The list enumerated sentences rather than classes, so a paraphrase in the register a
   model actually writes in — third person, hedged, passive — matched nothing (§3).

## The shape now

Split into sentences and judge each on its own. Mask quoted material, because repeating
what the room said is the product. Match the normalised copy, never the original. The
rules are in `advice_rules.py` and the classes they combine in `advice_words.py`; what
lives here is the part that decides what a sentence is.
"""

from __future__ import annotations

import re

from . import advice_rules as rules
from . import advice_words as w
from .normalise import Normalised, prepare

_QUOTED = re.compile(r"[\"“]([^\"”]{3,})[\"”]")
_CITATION = re.compile(r"\[u\d+\]")
RULES = (
    rules.r_unexpected_script,
    rules.r_second_person_obligation,
    rules.r_imperative_opening,
    rules.r_permission_about_treatment,
    rules.r_reassurance,
    rules.r_severity,
    rules.r_emergency,
    rules.r_activity_permission,
    rules.r_clinical_relationship,
)


def mask_quotes(sentence: str) -> str:
    """Blank the inside of every quotation, keeping the marks and the length.

    Offsets are preserved so a caller can still point at the original. Repeating an
    instruction the room gave is exactly what this app is for, so quoted material is
    exempt — but only the inside of the quotation, which is what stops
    `"take half a tablet" was said, and you should double it` from laundering its second
    half through its first.
    """
    out = list(sentence)
    for m in _QUOTED.finditer(sentence):
        for i in range(m.start(1), m.end(1)):
            out[i] = " "
    return "".join(out)


def opening_word(n: Normalised) -> str | None:
    """The first word that carries the sentence's mood, or None.

    Leaders that do not change whether a sentence instructs are skipped: "Do not wait"
    instructs as much as "Wait". Capped, so a long preamble cannot push the verb out of
    reach.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", n.spaced) if t]
    for token in tokens[:4]:
        if token not in w.OPENER_SKIP:
            return token
    return None


def _about_treatment(n: Normalised) -> bool:
    return bool(
        n.has_any_word(w.TREATMENT)
        or n.has_any_word(w.MEDICINE)
        or n.has_any_phrase(w.TREATMENT_PHRASES)
    )


def _acts_on_treatment(n: Normalised) -> bool:
    return bool(n.has_any_word(w.ACT) or n.has_any_phrase(w.ACT_PHRASES))


def _acts_or_acted(n: Normalised) -> bool:
    return bool(_acts_on_treatment(n) or n.has_any_word(w.ACTED))


def findings_for_sentence(sentence: str) -> list[str]:
    """Every rule that fires on one sentence, as app-authored reasons.

    Quoted material is masked first, then the masked copy is normalised, and every
    comparison below happens against the normalised copy. The original is never matched
    against and never altered.
    """
    masked = mask_quotes(sentence)
    if not prepare(_CITATION.sub(" ", masked)).tokens:
        # The sentence is nothing but a quotation. A quotation is exempt because of the
        # frame around it — the app reporting what the room said. With no frame there is
        # no report, only an instruction with quote marks on it, and a model that learns
        # this exemption learns to open every sentence with a quotation mark. So judge it
        # unmasked. `collisions.py` calls this guard on Bee's own summariser output,
        # where there is no citation guard downstream to catch a quotation nobody said.
        masked = sentence
    n = prepare(masked)
    if not n.tokens:
        return []
    opener = opening_word(n)
    out: list[str] = []
    for rule in RULES:
        why = rule(n, opener)
        if why and why not in out:
            out.append(why)
    return out
