"""Medication changes, as structured facts, each one nailed to a verbatim quote.

This is the part of Second Chair most likely to hurt somebody if it is wrong, so it is
built to fail towards silence:

* Every row must carry a quote that appears verbatim in the transcript. A row whose
  quote cannot be found is dropped, not shown with a caveat.
* A row may record what was said about a medicine. It may not say what to do. The
  `instruction` field holds the quote; there is no generated instruction field.
* Ambiguity is a first-class outcome. "and the water tablet, we'll leave that for now"
  becomes an UNCLEAR row naming the words that were used, not a guess at furosemide.
* "No change" is a row. A patient who hears nothing about a medicine often assumes it
  stopped, so an explicit "this one was not changed" is worth as much as a change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from enum import Enum

from .bedrock import Bedrock, ModelCall, parse_json_block
from .bee import Utterance
from .guardrail import advice_guard, attribution_guard
from .normalise import normalise


class ChangeKind(str, Enum):
    REDUCED = "reduced"
    INCREASED = "increased"
    STOPPED = "stopped"
    STARTED = "started"
    UNCHANGED = "unchanged"
    UNCLEAR = "unclear"


HUMAN = {
    ChangeKind.REDUCED: "Dose goes down",
    ChangeKind.INCREASED: "Dose goes up",
    ChangeKind.STOPPED: "Stopping",
    ChangeKind.STARTED: "Starting",
    ChangeKind.UNCHANGED: "No change",
    ChangeKind.UNCLEAR: "Not clear from the recording",
}


@dataclass
class MedChange:
    medicine: str
    kind: ChangeKind
    from_dose: str | None
    to_dose: str | None
    timing: str | None
    quote: str
    utterance_index: int
    note: str = ""
    superseded_by: str | None = None

    def as_dict(self) -> dict[str, object]:
        d = asdict(self)
        d["kind"] = self.kind.value
        d["heading"] = HUMAN[self.kind]
        return d


SYSTEM = """You read a transcript of one medical appointment and list what was said about medicines.

Hard rules. Breaking any of them makes the output useless:
- Output JSON only. A list of objects. No prose, no code fence.
- Every object must have: medicine, kind, from_dose, to_dose, timing, quote, utterance_index.
- "kind" is one of: reduced, increased, stopped, started, unchanged, unclear.
- "quote" MUST be copied character for character from the utterance you cite. Do not tidy it,
  do not fix the grammar, do not join two utterances. If you cannot copy an exact span, omit the row.
- "utterance_index" is the [n] number printed beside the utterance the quote came from.
- Never say who spoke. The transcript does not record that and neither do you.
- Never add clinical reasoning, never explain why a change was made unless the reason was
  said out loud, and never state a consequence of taking or not taking something.
- If a medicine is referred to only by a nickname ("the water tablet"), use kind "unclear",
  put the words that were used in "medicine", and leave the doses null. Do not guess the drug.
- Include medicines that were explicitly NOT changed, with kind "unchanged".
- Write no free text of any kind. There is no notes field. Everything you have to say
  about a medicine must be one of the listed fields or a span copied into "quote"."""


def _numbered(utterances: list[Utterance]) -> str:
    return "\n".join(f"[{i}] {u.text}" for i, u in enumerate(utterances))


def extract(utterances: list[Utterance], bedrock: Bedrock | None = None) -> tuple[list[MedChange], ModelCall]:
    """Model first, rules as the fallback. Both outputs go through the same verifier."""
    if bedrock is not None:
        try:
            raw, call = bedrock.complete(SYSTEM, _numbered(utterances), max_tokens=1600)
            # Verify first. A model answer that is entirely hallucinated parses fine and
            # then verifies to nothing, and returning that empty list would show a judge
            # a blank medicines panel with "written by Bedrock" underneath it.
            rows = verify(_parse(raw, utterances), utterances)
            if rows:
                return rows, call
        except Exception:  # noqa: BLE001 - any model failure falls back to rules
            pass
    rows = rule_based(utterances)
    return verify(rows, utterances), ModelCall(
        model_id=None,
        used_model=False,
        fallback_reason="Bedrock was not reachable, so these rows come from the written rules in meds.py.",
    )


def _parse(raw: str, utterances: list[Utterance]) -> list[MedChange]:
    try:
        data = parse_json_block(raw)
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, list):
        return []
    out: list[MedChange] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            kind = ChangeKind(str(item.get("kind", "")).strip().lower())
        except ValueError:
            # Refuse the row, do not coerce it. An enum member we did not write means the
            # model is doing something we did not model, and landing it on `unclear`
            # renders a row headed "Not clear from the recording" that hides the fact.
            # GUARD-STANDARD.md §1, S1: "Refuse, do not coerce."
            continue
        idx = item.get("utterance_index")
        if not isinstance(idx, int) or not (0 <= idx < len(utterances)):
            continue
        out.append(
            MedChange(
                medicine=str(item.get("medicine", "")).strip(),
                kind=kind,
                from_dose=_opt(item.get("from_dose")),
                to_dose=_opt(item.get("to_dose")),
                timing=_opt(item.get("timing")),
                quote=str(item.get("quote", "")).strip(),
                utterance_index=idx,
                # Deliberately not read from the model. An earlier version let it write a
                # free-text note and it produced "Stop immediately, no taper needed" and
                # "Take half a five milligram tablet until current box runs out": generated
                # dosing instructions, on screen, beside a real medicine. The quote already
                # says everything true, so the field is now ours alone.
                note="",
            )
        )
    return out


def _opt(v: object) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


def verify(rows: list[MedChange], utterances: list[Utterance]) -> list[MedChange]:
    """Ground every field of a row in the utterance it cites, or lose that field.

    This is the only thing standing between a fluent model and a wrong dose on a screen,
    so it is a substring test on flattened text, not a similarity score.

    It used to check two things: that `quote` appeared in the cited utterance, and that
    `medicine` was non-empty. That was not enough, and the app's own test proved it while
    appearing to prove the opposite. A model can copy `quote` character for character,
    which passes, and invent `to_dose` beside it, which nothing looked at. `to_dose` is
    the largest element on the medicines panel and `relative.py` prints it into the
    document a carer is sent. "bring the bisoprolol down" is a true quote; "10mg" next to
    it is a doubled dose of a beta blocker, in bold, on a page for a 71-year-old.

    So every field a model can fill is now checked against the source:

    * `quote` must appear in the cited utterance. Unchanged, and still the first gate.
    * `medicine` must appear in it too. A drug name nobody said leaves nothing true in
      the row, so the row goes.
    * `from_dose` and `to_dose` must be doses readable out of that same utterance. A room
      says "two point five milligrams", so the utterance is digitised first and the
      model's dose normalised the same way before they are compared. A dose that fails is
      blanked rather than dropping the row: the quote is still true and worth showing, and
      the honest page says what was said and no number.
    * `timing` must be a span of the utterance, on the same rule as the quote.

    The rule-based path reads all four out of the utterance already, so nothing it
    produces can fail this. `test_the_rule_based_path_is_unharmed_by_the_grounding_check`
    is there to catch a future tightening that goes too far.
    """
    kept = []
    for r in rows:
        if not (0 <= r.utterance_index < len(utterances)):
            continue
        source = utterances[r.utterance_index].text
        quote = _span_of(r.quote, source) if r.quote else None
        if quote is None:
            continue
        medicine = _span_of(r.medicine, source) if r.medicine else None
        if medicine is None:
            continue
        # Every prose field on the row is now the utterance's own text, sliced by us. The
        # model chose which span; it did not get to write a single character of it.
        r.quote = quote
        r.medicine = medicine
        said = _doses_in(source)
        # Grounding a swapped pair is what makes `_direction_agrees` below able to do its
        # job. The model sent from 2.5mg / to 5mg for a room that said "down from five
        # milligrams to two point five". While `_doses_in` could not read the elided unit,
        # 2.5mg failed to ground, the model's `from_dose` was blanked, and the direction
        # check — which needs two numbers — never fired. The survivor went to the page
        # carrying the authority of a checked value, and the carer's document read
        # "Dose goes down: bisoprolol: 5mg": the old dose printed as the new one.
        #
        # Reading the elided unit fixes both halves of that at once. Both doses now
        # ground, so the contradiction is visible to the check that was always meant to
        # catch it. An explicit pre-grounding check was tried here and removed: with this
        # line correct it could not be made to change any rendered artefact, and §7 says
        # a guard no test can make fail is not a guard.
        r.from_dose = _norm_dose(r.from_dose) if r.from_dose and _norm_dose(r.from_dose) in said else None
        r.to_dose = _norm_dose(r.to_dose) if r.to_dose and _norm_dose(r.to_dose) in said else None
        r.timing = _span_of(r.timing, source) if r.timing else None
        if not _kind_agrees(r, source):
            # The row's heading is the largest word on its card, and `kind` is the only
            # thing that sets it. Grounding every other field still leaves a model free
            # to file a true quote under the wrong heading: "Stopping" printed over
            # "bring the bisoprolol down from five to two point five" is a stopped beta
            # blocker on a 71-year-old's page, assembled entirely out of true parts.
            #
            # So the closed set is checked for agreement with the source, using the same
            # stems the rule-based path classifies with (§1: "S1 + agreement check"). It
            # refuses only what it can prove wrong — a quote naming no direction at all
            # contradicts nothing and passes.
            continue
        if not _direction_agrees(r):
            # Both numbers were said, so grounding alone lets them through in either
            # order, and swapping them turns a halving into a doubling. `kind` is a
            # closed set and the only other thing that says which way it went, so when
            # the two disagree neither can be trusted and both doses go.
            r.from_dose = None
            r.to_dose = None
        if r.note and not _note_is_safe(r.note):
            r.note = ""
        kept.append(r)
    return kept


def _direction_agrees(r: MedChange) -> bool:
    """Does the pair of doses move the way the row says it moves?

    Only answerable when both doses are present and both parse as numbers. Anything else
    is not a disagreement, so it passes: this refuses what it can prove wrong, not what
    it cannot read.
    """
    if r.kind not in (ChangeKind.REDUCED, ChangeKind.INCREASED):
        return True
    lo, hi = _mg(r.from_dose), _mg(r.to_dose)
    if lo is None or hi is None:
        return True
    return hi < lo if r.kind is ChangeKind.REDUCED else hi > lo


def _mg(dose: str | None) -> float | None:
    if not dose:
        return None
    m = re.match(r"^(\d+(?:\.\d+)?)mg$", dose.strip().lower())
    return float(m.group(1)) if m else None


def _doses_in(text: str) -> set[str]:
    """Every dose the room can be shown to have said, normalised.

    Spoken first, so "five milligrams" and "two point five milligrams" have to become
    "5mg" and "2.5mg" before a model's "5mg" can be matched against them. `_digitise`
    already does that for the rule-based path; this is the same reading, used as a
    whitelist instead of as a source.
    """
    digitised = _digitise(text)
    return {_norm_dose(m.group(0)) for m in _DOSE.finditer(digitised)} | _elided_unit_doses(digitised)


# A dose whose unit was said once and left off the second time.
#
# "from five milligrams to two point five" is how the sentence is actually spoken, and
# `_DOSE` alone reads only the first half of it — so a dose the room really did give was
# not in the grounded set, the model's true "2.5mg" was blanked for failing to ground,
# and GUARD-STANDARD.md §3 uses this very sentence as its worked example of a number that
# must ground. Both patterns require a number that *does* carry a unit in the same
# clause, so the unit is read off the room's own words rather than assumed, and nothing
# is grounded that was not said.
# Same spellings  accepts: the room says "milligrams" and 
# only turns the number into digits, it does not shorten the unit.
_UNIT_ALT = r"milligrams?|mg|mcg|ml|units?"
# A whole number, never a prefix of a longer one: without the guards, `10` offers `1`
# to the pattern and "from five milligrams to ten milligrams" grounded a phantom 1mg.
_N = r"(?<![\d.])(\d+(?:\.\d+)?)(?![\d.])"
_FROM_UNIT_TO_BARE = re.compile(
    rf"{_N}\s*({_UNIT_ALT})\b[^.]{{0,40}}?\bto\s+{_N}(?!\s*(?:{_UNIT_ALT})\b)",
    re.I,
)
_FROM_BARE_TO_UNIT = re.compile(
    rf"{_N}(?!\s*(?:{_UNIT_ALT})\b)[^.]{{0,40}}?\bto\s+{_N}\s*({_UNIT_ALT})\b",
    re.I,
)


def _elided_unit_doses(digitised: str) -> set[str]:
    """Doses the room said with the unit stated once for the pair."""
    out: set[str] = set()
    for m in _FROM_UNIT_TO_BARE.finditer(digitised):
        out.add(_norm_dose(f"{m.group(3)}{m.group(2)}"))
    for m in _FROM_BARE_TO_UNIT.finditer(digitised):
        out.add(_norm_dose(f"{m.group(1)}{m.group(3)}"))
    return out


_IMPERATIVE = re.compile(
    r"^\s*(take|stop|start|keep|continue|split|halve|double|skip|reduce|increase|raise|"
    r"lower|use|avoid|call|ring|go|wait|check|swap|switch)\b",
    re.I,
)


def _note_is_safe(note: str) -> bool:
    """Belt and braces over the one free-text field a row can carry.

    Nothing writes a note from a model any more, but if something ever does, it has to
    get past the advice and attribution guards and must not open with an imperative.
    """
    if _IMPERATIVE.match(note):
        return False
    return advice_guard(note).clean and attribution_guard(note).clean


def _flat_map(s: str) -> tuple[str, list[int]]:
    """Flatten `s` to bare letters and digits, keeping each one's offset in the original.

    Normalised a character at a time (§2), so that a curly apostrophe, a non-breaking
    space or a Cyrillic о in the model's copy still lands on the same flattened letter as
    the transcript's. The offsets are what make `_span_of` possible: the app can find
    what the model meant and then render the room's own bytes instead of the model's.
    """
    chars: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        for c in re.sub(r"[^a-z0-9]", "", normalise(ch)):
            chars.append(c)
            idx.append(i)
    return "".join(chars), idx


def _flat(s: str) -> str:
    """Flattened for comparison only. Never rendered."""
    return _flat_map(s)[0]


def _span_of(needle: str, source: str) -> str | None:
    """The stretch of `source` that the model was pointing at, in the source's own bytes.

    This is the §1 ladder applied to the three fields on this row that are prose: rather
    than checking the model's string and then printing the model's string, the app finds
    where in the utterance it occurs and prints *that*. A lookalike letter, a smart quote
    or a doubled space in the model's copy cannot survive, because the model's copy is
    thrown away once it has done its job of pointing.

    Returns None when the needle is not in the source, which is the caller's signal to
    drop the field or the row.
    """
    flat_source, offsets = _flat_map(source)
    flat_needle = _flat(needle)
    if not flat_needle:
        return None
    at = flat_source.find(flat_needle)
    if at == -1:
        return None
    start = offsets[at]
    end = offsets[at + len(flat_needle) - 1] + 1
    return source[start:end]


def _signals() -> tuple[tuple[re.Pattern[str], ChangeKind], ...]:
    """The stems the rule-based classifier reads a direction out of, paired with the
    member of the closed set each one means. A function because the patterns are defined
    further down the file, next to the classifier that is their other caller."""
    return (
        (_STOP_RE, ChangeKind.STOPPED),
        (_SAME_RE, ChangeKind.UNCHANGED),
        (_UP_RE, ChangeKind.INCREASED),
        (_DOWN_RE, ChangeKind.REDUCED),
        (_START_RE, ChangeKind.STARTED),
    )


_DIRECTIONAL = frozenset({
    ChangeKind.REDUCED, ChangeKind.INCREASED, ChangeKind.STOPPED,
    ChangeKind.STARTED, ChangeKind.UNCHANGED,
})


def _kind_agrees(r: MedChange, source: str) -> bool:
    """Does the utterance support the heading the row claims?

    Same discipline as `_direction_agrees`: refuse what can be proven wrong, not what
    cannot be read. An utterance naming no direction supports every heading, because it
    contradicts none of them, and the rule-based path cannot fail this by construction —
    its kinds come from these same stems.
    """
    if r.kind not in _DIRECTIONAL:
        return True
    low = normalise(source)
    supported = {kind for pattern, kind in _signals() if pattern.search(low)}
    return not supported or r.kind in supported


# ------------------------------------------------------------------ deterministic fallback

_KNOWN = [
    "bisoprolol", "apixaban", "amlodipine", "atorvastatin", "furosemide", "metformin",
    "ramipril", "warfarin", "digoxin", "simvastatin", "lisinopril", "gliclazide",
]
_NICKNAME = re.compile(r"\b(the (water|blood pressure|heart|sugar|cholesterol|blood thinning) tablets?)\b", re.I)

# Nobody in a consulting room says "2.5 mg". They say "two point five milligrams", and
# Bee hands us the words. So doses are read off a digitised copy of the utterance while
# the quote stays verbatim.
_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
         "eighty": 80, "ninety": 90}
_NUMWORD = "|".join(list(_TENS) + sorted(_UNITS, key=len, reverse=True))


def _digitise(text: str) -> str:
    def tens_units(m: re.Match[str]) -> str:
        return str(_TENS[m.group(1).lower()] + _UNITS[m.group(2).lower()])

    out = re.sub(rf"\b({'|'.join(_TENS)})[\s-]({'|'.join(k for k, v in _UNITS.items() if 1 <= v <= 9)})\b",
                 tens_units, text, flags=re.I)

    def word(m: re.Match[str]) -> str:
        w = m.group(0).lower()
        return str(_UNITS.get(w, _TENS.get(w, w)))

    out = re.sub(rf"\b({_NUMWORD})\b", word, out, flags=re.I)
    # "2 point 5" -> "2.5"
    return re.sub(r"\b(\d+)\s+point\s+(\d+)\b", r"\1.\2", out, flags=re.I)


_DOSE = re.compile(r"\b(\d+(?:\.\d+)?|half)\s*(milligrams?|mg)\b", re.I)
_TIMING = re.compile(r"\b(in the morning|at night|twice a day|once a day|morning and evening|with food|before bed)\b", re.I)


def rule_based(utterances: list[Utterance]) -> list[MedChange]:
    """Readable rules. Works with the network off and is what the tests pin behaviour to."""
    out: list[MedChange] = []
    for i, u in enumerate(utterances):
        text = u.text
        digits = _digitise(text)
        low = text.lower()
        for name in _KNOWN:
            if name not in low:
                continue
            doses = [_norm_dose(m.group(0)) for m in _DOSE.finditer(digits)]
            timing = _TIMING.search(text)
            kind, frm, to = _classify(low, doses)
            out.append(
                MedChange(
                    medicine=name,
                    kind=kind,
                    from_dose=frm,
                    to_dose=to,
                    timing=timing.group(0) if timing else None,
                    quote=text,
                    utterance_index=i,
                )
            )
        nick = _NICKNAME.search(text)
        if nick and not any(n in low for n in _KNOWN):
            out.append(
                MedChange(
                    medicine=nick.group(0),
                    kind=ChangeKind.UNCLEAR,
                    from_dose=None,
                    to_dose=None,
                    timing=None,
                    quote=text,
                    utterance_index=i,
                    note="Named only by nickname in the recording. Which medicine this is was not said out loud.",
                )
            )
    return _dedupe(out)


# No trailing \b on any of these. They are stems, and "increasing" does not end at
# "increas", so a closing boundary makes every one of them silently never match. Same
# defect as the refusal pattern in consent.py; both are pinned by tests now.
_STOP_RE = re.compile(r"\b(stop|come off|discontinu)")
_SAME_RE = re.compile(r"\b(does not change|doesn't change|no change|stays? the same|exactly as you are|keep taking|don't want touched)")
_UP_RE = re.compile(r"\b(increas|up from|put(ting)? you up|rais)")
_DOWN_RE = re.compile(r"\b(bring .{0,25}down|reduc|lower|down from|halv|cut)")
_START_RE = re.compile(r"\b(start|begin|put you on)")


def _classify(low: str, doses: list[str]) -> tuple[ChangeKind, str | None, str | None]:
    if _SAME_RE.search(low):
        return ChangeKind.UNCHANGED, doses[0] if doses else None, doses[0] if doses else None
    if _STOP_RE.search(low):
        return ChangeKind.STOPPED, doses[0] if doses else None, None
    if _UP_RE.search(low) and len(doses) >= 2:
        return ChangeKind.INCREASED, doses[0], doses[1]
    if _DOWN_RE.search(low) and len(doses) >= 2:
        return ChangeKind.REDUCED, doses[0], doses[1]
    if _START_RE.search(low):
        return ChangeKind.STARTED, None, doses[0] if doses else None
    return ChangeKind.UNCLEAR, doses[0] if doses else None, doses[1] if len(doses) > 1 else None


def _norm_dose(raw: str) -> str:
    s = raw.lower().replace("milligrams", "mg").replace("milligram", "mg")
    return re.sub(r"\s+", "", s)


def _dedupe(rows: list[MedChange]) -> list[MedChange]:
    """One row per medicine, keeping the most specific statement about it.

    A consultation says a drug's name several times. The row worth keeping is the one
    that names a change, then the one that names a dose, then whichever came first.
    """
    rank = {
        ChangeKind.REDUCED: 0, ChangeKind.INCREASED: 0, ChangeKind.STOPPED: 0,
        ChangeKind.STARTED: 0, ChangeKind.UNCHANGED: 1, ChangeKind.UNCLEAR: 2,
    }
    best: dict[str, MedChange] = {}
    for r in rows:
        key = r.medicine.lower()
        cur = best.get(key)
        if cur is None or (rank[r.kind], -bool(r.to_dose)) < (rank[cur.kind], -bool(cur.to_dose)):
            best[key] = r
    return sorted(best.values(), key=lambda r: r.utterance_index)
