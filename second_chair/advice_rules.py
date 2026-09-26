"""The nine rules of the advice guard, each one a combination of semantic classes.

The combinations are the point. `ACT` alone would reject "the plan is to stop the
amlodipine", which reports the room and has to stay. `ACT` at the head of a sentence, or
`ACT` under a frame that hands somebody permission, is an instruction whatever words
follow it — and that is what the old eleven regexes could not express, which is why a
paraphrase in the register a model actually writes in matched none of them.

Every rule returns an app-authored sentence or None. §6 of the guard standard: a
rejection is model output with a frame around it, so nothing the model wrote may appear
in what the page is shown.

Each rule is paired with a sentence it must **not** touch, in
`tests/test_advice_pairs.py`, and `test_every_rule_fires_on_something` deletes each one
in turn and requires the corpus to go red.
"""

from __future__ import annotations

import re

from . import advice_words as w
from .normalise import Normalised


def about_treatment(n: Normalised) -> bool:
    return bool(
        n.has_any_word(w.TREATMENT)
        or n.has_any_word(w.MEDICINE)
        or n.has_any_phrase(w.TREATMENT_PHRASES)
    )


def acts_or_acted(n: Normalised) -> bool:
    return bool(
        n.has_any_word(w.ACT) or n.has_any_phrase(w.ACT_PHRASES) or n.has_any_word(w.ACTED)
    )


# Each rule takes the normalised sentence plus its opening word, and returns the sentence
# the page will show if it fires. The returned string is app-authored: §6 of the standard
# says a rejection is model output with a frame around it, so nothing the model wrote may
# appear in it.


def r_unexpected_script(n: Normalised, _opener: str | None) -> str | None:
    """A fold table is a denylist wearing different clothes.

    `dıd not` with a dotless Turkish ı survives the lookalike map, and so will the next
    one. The allowlist version: after folding, a letter outside a-z in an English-only
    guarded field is either a language this guard was never written for or somebody
    probing it. Refuse, and do not extend the map (§2).
    """
    if n.foreign_letters():
        return "uses letters this guard cannot read, so it could not be checked"
    return None


def _obligation_after_subject(n: Normalised) -> bool:
    """A subject, a short run of auxiliaries or adverbs, then an obligation.

    This replaces a substring test against fixed pairs like "you should", which demanded
    the two words be adjacent. One adverb defeated it: "You should stop the apixaban" was
    refused, and "You really should stop the apixaban" was printed on the page — along
    with "You will need to", "You'll need to", "You would need to", "You just need to"
    and "You are going to need to", each of which reached the rendered recap.

    The gap comes from a closed set rather than being any word, so the rule cannot leap a
    clause and fire on "you asked whether she should stop", which reports the room.
    """
    tokens = [t for t in re.split(r"[^a-z0-9]+", n.spaced) if t]
    for i, token in enumerate(tokens):
        if token not in w.OBLIGATION_SUBJECT:
            continue
        for gap in range(0, 4):
            rest = " ".join(tokens[i + 1 + gap:])
            if any(rest == o or rest.startswith(o + " ") for o in w.OBLIGATION):
                return True
            nxt = tokens[i + 1 + gap] if i + 1 + gap < len(tokens) else None
            if nxt is None or nxt not in w.OBLIGATION_GAP:
                break
    return False


def r_second_person_obligation(n: Normalised, _opener: str | None) -> str | None:
    """Telling somebody what they have to do, whatever sits between the two words."""
    if _obligation_after_subject(n):
        return "tells the reader what to do"
    return None


def r_imperative_opening(n: Normalised, opener: str | None) -> str | None:
    """A generated sentence that opens by telling the reader to do something is advice
    whatever it goes on to say.

    "Stop immediately, no taper needed." has no dose noun, no drug name and no "you".
    The sentence is pure instruction, and that is what makes it dangerous: the reader
    supplies the object from the medicine name printed beside it.
    """
    if opener in w.ACT or opener in w.DIRECT:
        return "opens by telling the reader to do something"
    if n.spaced.startswith(tuple(w.ACT_PHRASES)):
        return "opens by telling the reader to do something"
    return None


def r_permission_about_treatment(n: Normalised, _opener: str | None) -> str | None:
    """Someone else's latitude with their own medicine is not ours to grant.

    This is the rule the hedged third person needed. A model does not write "stop the
    amlodipine"; it writes "she can stop the amlodipine whenever she likes", which is the
    same instruction with a pronoun in front of it.
    """
    if n.has_any_phrase(w.DEONTIC_STRONG) and acts_or_acted(n):
        return "tells the reader what may be done about a medicine"
    if n.has_any_phrase(w.DEONTIC_WEAK) and acts_or_acted(n) and about_treatment(n):
        return "tells the reader what may be done about a medicine"
    return None


def r_reassurance(n: Normalised, _opener: str | None) -> str | None:
    if n.has_any_phrase(w.REASSURANCE):
        return "reassures the reader about a symptom"
    return None


def r_severity(n: Normalised, _opener: str | None) -> str | None:
    if n.has_any_phrase(w.SEVERITY):
        return "judges how serious something is"
    # The comparative and superlative of the same class. "is safe" was refused while
    # "safer" and "safest" were printed, which is the near-miss shape §7 names.
    if n.has_any_phrase(w.SEVERITY_DEGREE):
        return "judges how serious something is"
    # A bare "better" or "worse" only judges when a treatment is what is being judged,
    # or "the readings were better overnight" stops being reportable.
    if n.has_any_phrase(w.EVALUATIVE_WEAK) and (about_treatment(n) or acts_or_acted(n)):
        return "judges one course of treatment against another"
    return None


def r_emergency(n: Normalised, _opener: str | None) -> str | None:
    at_emergency = n.has_any_word(w.EMERGENCY) or n.has_any_phrase(w.EMERGENCY_PHRASES)
    if at_emergency and (n.has_any_word(w.SEND) or n.has_any_phrase(w.DEONTIC_STRONG)):
        return "directs the reader to emergency care"
    return None


def r_activity_permission(n: Normalised, _opener: str | None) -> str | None:
    if n.has_any_word(w.ACTIVITY) and n.has_any_phrase(w.PERMISSION):
        return "rules on what the reader may do"
    return None


def r_clinical_relationship(n: Normalised, _opener: str | None) -> str | None:
    if n.has_any_phrase(w.CAUSATION) and (
        n.has_any_word(w.MEDICINE) or n.has_any_word(w.TREATMENT)
    ):
        return "asserts a clinical relationship between medicines"
    return None
