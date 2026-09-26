"""The questions you meant to ask.

Everyone writes a list on the back of an envelope and comes out having asked two of them.
This reconciles the list against what was actually said, and the only claim it makes is
about the recording: covered, partly covered, or not covered.

The temptation here is to rank the unasked questions by how clinically important they
look. Second Chair does not do that, and refusing to is the point. Importance comes from
one place: whether the person ticked the box themselves when they wrote the question
down. An app that decides your ibuprofen question matters more than your holiday question
has started practising medicine.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .bedrock import Bedrock, JUDGEMENT_MODELS, ModelCall, parse_json_block
from .bee import Utterance


class Coverage(str, Enum):
    COVERED = "covered"
    PARTLY = "partly"
    NOT_COVERED = "not_covered"


HUMAN = {
    Coverage.COVERED: "Covered in the room",
    Coverage.PARTLY: "Something near it was said",
    Coverage.NOT_COVERED: "Not covered",
}


@dataclass
class QuestionOutcome:
    id: str
    text: str
    marked_important: bool
    coverage: Coverage
    evidence_index: int | None
    evidence_quote: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "text": self.text,
            "marked_important": self.marked_important,
            "coverage": self.coverage.value,
            "heading": HUMAN[self.coverage],
            "evidence_index": self.evidence_index,
            "evidence_quote": self.evidence_quote,
        }


SYSTEM = """You compare a list of questions a patient wrote before an appointment against the
transcript of that appointment, and say for each question whether it was covered.

Output JSON only: a list of {"id", "coverage", "utterance_index"}.
"coverage" is one of "covered", "partly", "not_covered".

- "covered" means the substance of the question was addressed out loud. It does not have to
  have been asked in the patient's words.
- "partly" means something adjacent was said but the question itself was not answered.
- "not_covered" means nothing in the transcript addresses it.
- "utterance_index" is the [n] of the best supporting utterance, or null for not_covered.
- Judge only what is in the transcript. Do not use any medical knowledge. Do not rank the
  questions, do not say which matters, do not add any field beyond the three named."""


_STOP = {
    "the", "a", "an", "is", "it", "i", "my", "do", "does", "to", "in", "on", "for", "of",
    "and", "or", "if", "still", "need", "can", "should", "what", "when", "how", "be",
    "are", "was", "that", "this", "at", "with", "you", "me", "again", "keep", "alright",
    "under", "over", "every", "any",
}


def _tokens(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", s.lower()) if w not in _STOP and len(w) > 2}


def reconcile(
    questions: list[dict[str, object]],
    utterances: list[Utterance],
    bedrock: Bedrock | None = None,
) -> tuple[list[QuestionOutcome], ModelCall]:
    if bedrock is not None:
        try:
            prompt = _prompt(questions, utterances)
            raw, call = bedrock.complete(SYSTEM, prompt, max_tokens=900)
            outcomes = _parse(raw, questions, utterances)
            if len(outcomes) == len(questions):
                return outcomes, call
        except Exception:  # noqa: BLE001
            pass
    return overlap_based(questions, utterances), ModelCall(
        None, False, "Bedrock was not reachable, so coverage comes from word overlap in questions.py."
    )


def judgement_client(invoke=None) -> Bedrock:
    """Opus for this one. Deciding 'partly' from 'covered' is the judgement call in the app."""
    kw = {"models": JUDGEMENT_MODELS}
    if invoke is not None:
        kw["invoke"] = invoke
    return Bedrock(**kw)  # type: ignore[arg-type]


def _prompt(questions: list[dict[str, object]], utterances: list[Utterance]) -> str:
    qs = "\n".join(f'{q["id"]}: {q["text"]}' for q in questions)
    us = "\n".join(f"[{i}] {u.text}" for i, u in enumerate(utterances))
    return f"QUESTIONS\n{qs}\n\nTRANSCRIPT\n{us}"


def _parse(raw: str, questions: list[dict[str, object]], utterances: list[Utterance]) -> list[QuestionOutcome]:
    data = parse_json_block(raw)
    if not isinstance(data, list):
        return []
    by_id = {str(d.get("id")): d for d in data if isinstance(d, dict)}
    out: list[QuestionOutcome] = []
    for q in questions:
        d = by_id.get(str(q["id"]))
        if d is None:
            return []
        try:
            coverage = Coverage(str(d.get("coverage", "not_covered")).lower())
        except ValueError:
            coverage = Coverage.NOT_COVERED
        idx = d.get("utterance_index")
        idx = idx if isinstance(idx, int) and 0 <= idx < len(utterances) else None
        if coverage is not Coverage.NOT_COVERED and idx is None:
            coverage = Coverage.NOT_COVERED
        out.append(
            QuestionOutcome(
                id=str(q["id"]),
                text=str(q["text"]),
                marked_important=bool(q.get("marked_important")),
                coverage=coverage,
                evidence_index=idx,
                evidence_quote=utterances[idx].text if idx is not None else None,
            )
        )
    return out


def overlap_based(questions: list[dict[str, object]], utterances: list[Utterance]) -> list[QuestionOutcome]:
    """Fallback. Content-word overlap, with two thresholds rather than one.

    Strong overlap is "covered", weak overlap is "something near it was said", nothing is
    "not covered". Two thresholds because the honest answer to "was my question
    answered?" is often "not quite", and a product that only says yes or no about that
    will be wrong in the direction that matters.
    """
    out: list[QuestionOutcome] = []
    for q in questions:
        q_tokens = _tokens(str(q["text"]))
        best_score = 0.0
        best_idx: int | None = None
        for i, u in enumerate(utterances):
            u_tokens = _tokens(u.text)
            if not q_tokens or not u_tokens:
                continue
            score = len(q_tokens & u_tokens) / len(q_tokens)
            if score > best_score:
                best_score, best_idx = score, i
        if best_score >= 0.5:
            coverage = Coverage.COVERED
        elif best_score >= 0.25:
            coverage = Coverage.PARTLY
        else:
            coverage, best_idx = Coverage.NOT_COVERED, None
        out.append(
            QuestionOutcome(
                id=str(q["id"]),
                text=str(q["text"]),
                marked_important=bool(q.get("marked_important")),
                coverage=coverage,
                evidence_index=best_idx,
                evidence_quote=utterances[best_idx].text if best_idx is not None else None,
            )
        )
    return out


def carry_forward(outcomes: list[QuestionOutcome]) -> list[str]:
    """Todo text for the questions that were not covered.

    Phrased as a reminder to ask, never as a reason to ask. The person marked these
    important themselves; that is the only ordering Second Chair applies.
    """
    pending = [o for o in outcomes if o.coverage is not Coverage.COVERED]
    pending.sort(key=lambda o: (not o.marked_important, o.id))
    return [f"Ask at the next appointment: {o.text}" for o in pending]
