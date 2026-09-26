"""Three guards that sit between the model and the person reading the screen.

A 2026 study of model-generated discharge instructions found potential harms in 18% of
them. That is the whole reason this file exists. Second Chair is allowed to be a better
record of what was said. It is not allowed to be a worse doctor.

1. `advice_guard`  - generated prose may not tell anyone what to do with a medicine or
                     how worried to be. Verbatim quotes are exempt, because repeating
                     what was actually said in the room is the product. The rules it
                     applies are in `advice.py` and the words they are built from in
                     `advice_words.py`; what lives here is the sentence-by-sentence
                     driver, because judging a paragraph as one string is how the
                     founding incident got back in.
2. `attribution_guard` - nothing may say who said a thing. Bee labels every speaker
                     `Unknown`; an attribution would be invented.
3. `citation_guard` - every generated sentence must carry a marker pointing at the
                     utterance it came from, and the utterance must support it. Sentences
                     that do not are removed before anyone sees them, and the removal is
                     counted and shown.

Each guard returns the cleaned text plus the findings, and the findings are displayed.
A guard that silently fixes things teaches you nothing about the model behind it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .advice import findings_for_sentence
from .bee import Utterance

_ATTRIBUTION = [
    re.compile(r"\b(the|your) (doctor|clinician|consultant|nurse|gp|physician|cardiologist)\s+"
               r"(said|told|explained|advised|confirmed|recommended|wants|asked)\b", re.I),
    re.compile(r"\b(he|she|they) (said|told you|explained|advised|confirmed|recommended)\b", re.I),
    re.compile(r"\b(dr\.?|doctor)\s+[A-Z][a-z]+\s+(said|told|advised)\b"),
    re.compile(r"\bthe (patient|daughter|carer|caregiver) (said|asked|told)\b", re.I),
]

# How Second Chair is allowed to phrase it instead. This is the Threshold discipline
# applied to a clinic: report what was recorded, never who produced it.
PERMITTED_FRAME = "this was said in the room"

_CITATION = re.compile(r"\[u(\d+)\]")

# A full stop that closes one of these is not the end of a sentence. Short list on
# purpose: it only has to cover what turns up in a clinic note.
_ABBREV = re.compile(r"(?:^|\s)(?:Dr|Mr|Mrs|Ms|St|Prof|No|approx|e\.g|i\.e|vs|[A-Z])$")


def split_sentences(text: str) -> list[str]:
    """Split on sentence ends, but not inside a quotation and not before a citation.

    Written by hand because the obvious regex gets both of those wrong on exactly the
    output this app produces. A model writes `... don't wait." [u19]` and a naive split
    leaves the closing quote and the citation stranded as a sentence of their own, which
    then fails the citation check and gets deleted. The deletion looks like the guard
    working. It is the splitter being wrong, and it cost a round of debugging.
    """
    out: list[str] = []
    buf: list[str] = []
    depth = 0  # inside a double quote
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        buf.append(ch)
        if ch in '"“”':
            depth = 0 if depth else 1
        elif ch in ".!?":
            if ch == "." and _ABBREV.search("".join(buf[:-1])):
                i += 1
                continue
            # swallow the closing punctuation that belongs to this sentence. A closing
            # quote here ends the quotation, so a stop inside quote marks is a boundary
            # once the quote has shut: `"Ring the clinic." [u19] A summary ...`
            j = i + 1
            while j < n and text[j] in '."”\')':
                if text[j] in '"”':
                    depth = 0
                buf.append(text[j])
                j += 1
            if depth:
                i = j
                continue
            k = j
            while k < n and text[k].isspace():
                k += 1
            # citations after the stop belong to the sentence just closed. Plural: a
            # sentence drawing on two utterances is written `... today. [u15][u17]`, and
            # absorbing only the first leaves the second to start a sentence of its own.
            while k < n and text[k] == "[":
                close = text.find("]", k)
                if close == -1 or not _CITATION.fullmatch(text[k : close + 1]):
                    break
                buf.append(" " if buf and not buf[-1].isspace() else "")
                buf.append(text[k : close + 1])
                k = close + 1
                while k < n and text[k].isspace():
                    k += 1
            if k >= n or text[k].isupper() or text[k] in '"“':
                out.append("".join(buf).strip())
                buf = []
            i = k
            continue
        i += 1
    tail = "".join(buf).strip()
    if tail:
        out.append(tail)
    return [s for s in out if s]


@dataclass
class Finding:
    """One guard firing on one sentence.

    `text` is the thing the guard caught, and it never leaves the server. Showing the
    guard's work is worth doing — a guard that silently fixes things teaches you nothing
    about the model behind it — but rendering what it caught is a second output path
    around the guard, and the text that was too dangerous to show gets shown one
    paragraph lower with a label saying it was blocked. A rejection is not metadata; it
    is model output with a frame around it.

    So the page gets `kind`, `detail` and `ordinal`, which are ours and say which rule
    fired on which sentence. `text` stays here for the log and for `_strip_offending`.
    `pipeline.derived_dict` is the boundary that enforces it, and
    `test_web.py::TestTheRefusalIsNotAnOutputChannel` is what stops it coming back.
    """

    kind: str
    detail: str
    text: str
    ordinal: int | None = None

    def as_dict(self) -> dict[str, object]:
        """What a page is allowed to see. Deliberately missing `text`."""
        return {"kind": self.kind, "detail": self.detail, "ordinal": self.ordinal}


@dataclass
class GuardResult:
    text: str
    findings: list[Finding] = field(default_factory=list)
    removed: int = 0

    @property
    def clean(self) -> bool:
        return not self.findings


def _quoted_spans(text: str) -> list[tuple[int, int]]:
    spans = []
    for m in re.finditer(r"[\"“]([^\"”]{3,})[\"”]", text):
        spans.append((m.start(1), m.end(1)))
    return spans


def _inside_quote(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= pos < b for a, b in spans)


def advice_guard(text: str) -> GuardResult:
    """Refuse generated advice. Quoted material passes through untouched.

    Every sentence is judged on its own. The rule that catches a bare imperative has to
    see the start of the sentence it is judging, and the version of this function that
    ran one `^`-anchored regex over the whole string only ever saw the first sentence of
    a paragraph: `"The plan was agreed. Stop immediately, no taper needed."` came back
    clean. The rules themselves live in `advice.py`; this is the part that decides what a
    sentence is.

    `Finding.text` is the sentence, and it is for the log. What the page is allowed to
    see is `kind` and `detail`, both of which are ours — see `Finding` below.
    """
    findings: list[Finding] = []
    for sentence in split_sentences(text) or [text]:
        for why in findings_for_sentence(sentence):
            findings.append(Finding("advice", why, sentence))
    return GuardResult(text=text, findings=findings)


def attribution_guard(text: str) -> GuardResult:
    spans = _quoted_spans(text)
    findings: list[Finding] = []
    for pattern in _ATTRIBUTION:
        for m in pattern.finditer(text):
            if _inside_quote(m.start(), spans):
                continue
            findings.append(
                Finding("attribution", f"names a speaker; say '{PERMITTED_FRAME}' instead", m.group(0))
            )
    return GuardResult(text=text, findings=findings)


def _supports(sentence: str, cited: list[Utterance]) -> bool:
    """Do the cited utterances, together, carry this sentence's content?

    Together, because a recap sentence legitimately draws on two or three utterances and
    checking each one alone rejects every synthesis. Cheap and strict otherwise: if the
    sentence quotes, the quote must be a substring of one of them, and a sentence with no
    quote must share real content words with them, so a citation cannot be stapled onto an
    invented sentence.
    """
    quotes = re.findall(r"[\"“]([^\"”]{3,})[\"”]", sentence)
    hay = "".join(_flatten(u.text) for u in cited)
    for q in quotes:
        if _flatten(q) not in hay:
            return False
    if quotes:
        return True
    stop = {
        "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "for", "was",
        "were", "is", "are", "be", "been", "that", "this", "it", "you", "your", "from",
        "with", "will", "not", "no", "so", "as", "by", "has", "have", "had", "they",
    }
    words = {w for w in re.findall(r"[a-z0-9]+", sentence.lower()) if w not in stop and len(w) > 2}
    joined = " ".join(u.text for u in cited).lower()
    hay_words = {w for w in re.findall(r"[a-z0-9]+", joined) if w not in stop and len(w) > 2}
    if not words:
        return False
    return len(words & hay_words) >= max(2, round(len(words) * 0.3))


def _flatten(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def citation_guard(text: str, utterances: list[Utterance]) -> GuardResult:
    """Drop every sentence that is not cited, or whose citation does not support it."""
    sentences = split_sentences(text)
    kept: list[str] = []
    findings: list[Finding] = []
    for sentence in sentences:
        refs = [int(n) for n in _CITATION.findall(sentence)]
        if not refs:
            findings.append(Finding("uncited", "no [uN] marker, so it cannot be checked", sentence))
            continue
        bad = [n for n in refs if n >= len(utterances) or n < 0]
        if bad:
            findings.append(Finding("bad-citation", f"cites {bad}, which is not in the transcript", sentence))
            continue
        if not _supports(sentence, [utterances[n] for n in refs]):
            findings.append(Finding("unsupported", f"cites {refs} but the utterance does not carry it", sentence))
            continue
        kept.append(sentence)
    return GuardResult(text=" ".join(kept), findings=findings, removed=len(findings))


REFUSAL = (
    "Second Chair will not answer that. It can only repeat what was recorded in the room. "
    "The clinic can answer it, and the number is on your appointment letter."
)


def check_question(question: str) -> str | None:
    """Questions the app must not answer. Returns the refusal, or None to proceed."""
    asking_for_advice = [
        re.compile(r"\b(should|shall|can|may|ought) i\b", re.I),
        re.compile(r"\b(is it|would it be) (safe|ok|okay|alright|dangerous|bad)\b", re.I),
        re.compile(r"\bhow (much|many)\b.{0,30}\b(should|do) i (take|have)\b", re.I),
        re.compile(r"\b(what|which) (dose|amount|strength)\b.{0,20}\bshould\b", re.I),
        re.compile(r"\b(do i need to|should i) (worry|go to|call|stop|skip)\b", re.I),
        re.compile(r"\bis (this|that|it) (serious|normal|dangerous|urgent|an emergency)\b", re.I),
        re.compile(r"\bwhat happens if i (stop|skip|double|miss)\b", re.I),
    ]
    for p in asking_for_advice:
        if p.search(question):
            return REFUSAL
    return None
