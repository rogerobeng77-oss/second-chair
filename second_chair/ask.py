"""Asking the record a question.

The model is allowed to do exactly one thing here: choose which utterances to show. It
never writes the answer. What comes back on screen is verbatim text pulled from storage
by index, so the worst a confused model can do is show you the wrong true sentence. It
cannot invent a dose, because it is not holding the pen.

That is the shape the upgrade brief asks for when a model must not change an outcome: it
ranks, it does not author. And before any of it runs, the question itself is checked. A
question asking for advice is refused, logged, and answered with the clinic's number,
because a published study found potential harms in 18% of model-generated discharge
instructions and this is the surface where that failure would land.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .bedrock import Bedrock, parse_json_block
from .guardrail import REFUSAL, check_question

SYSTEM = """You are given a numbered transcript and a question about it.

Return JSON only: {"indices": [n, ...]} listing at most four utterance numbers that
contain the answer, most relevant first. If nothing in the transcript addresses the
question, return {"indices": []}.

Do not write an answer. Do not summarise. Do not explain. Return only the numbers."""


@dataclass
class Answer:
    refused: bool
    text: str
    quotes: list[tuple[int, str]]
    how: str

    def as_dict(self) -> dict[str, object]:
        return {
            "refused": self.refused,
            "text": self.text,
            "quotes": [{"index": i, "text": t} for i, t in self.quotes],
            "how": self.how,
        }


_STOP = {
    "the", "a", "an", "is", "it", "my", "do", "does", "to", "in", "on", "for", "of", "and",
    "or", "if", "what", "when", "how", "was", "were", "did", "they", "that", "this", "me",
    "about", "with", "said", "say", "tell", "told", "again", "i", "you",
}


def answer(question: str, utterances: list[tuple[int, str]], bedrock: Bedrock | None = None) -> Answer:
    refusal = check_question(question)
    if refusal:
        return Answer(refused=True, text=refusal, quotes=[], how="Refused before anything was searched.")

    picked: list[int] = []
    how = "Picked by word overlap in ask.py."
    if bedrock is not None:
        numbered = "\n".join(f"[{i}] {t}" for i, t in utterances)
        try:
            raw, call = bedrock.complete(SYSTEM, f"{numbered}\n\nQUESTION\n{question}", max_tokens=200)
            data = parse_json_block(raw)
            if isinstance(data, dict):
                valid = {i for i, _ in utterances}
                picked = [n for n in data.get("indices", []) if isinstance(n, int) and n in valid][:4]
                how = (
                    f"{call.model_id} on Amazon Bedrock chose which lines to show. "
                    "The words below are copied from the transcript, not written by it."
                )
        except Exception:  # noqa: BLE001
            picked = []

    if not picked:
        picked = _overlap(question, utterances)
    picked = _with_the_reply(picked, utterances)

    by_index = dict(utterances)
    quotes = [(i, by_index[i]) for i in picked if i in by_index]
    if not quotes:
        return Answer(
            refused=False,
            text="Nothing in this appointment's record covers that. The clinic can answer it.",
            quotes=[],
            how=how,
        )
    return Answer(
        refused=False,
        text="This is what was recorded in the room. Second Chair will not add to it.",
        quotes=quotes,
        how=how,
    )


_QUESTION_SHAPED = re.compile(
    r"^\s*(do|does|did|can|could|should|is|are|was|were|what|when|where|how|why|will|would|and if)\b",
    re.I,
)


def _with_the_reply(picked: list[int], utterances: list[tuple[int, str]]) -> list[int]:
    """If a matched line is a question, the line after it is the answer.

    Word overlap finds "does she still need the blood test every month" and stops there,
    which is the patient's words rather than the reply. In a consultation the reply is
    almost always the next utterance, so it comes along. Cheap, and it is the difference
    between showing someone their own question back and showing them the answer.
    """
    by_index = dict(utterances)
    order = [i for i, _ in utterances]
    out: list[int] = []
    for i in picked:
        out.append(i)
        text = by_index.get(i, "")
        if "?" in text or _QUESTION_SHAPED.match(text):
            pos = order.index(i) if i in order else -1
            if pos != -1 and pos + 1 < len(order):
                out.append(order[pos + 1])
    seen: set[int] = set()
    return [i for i in out if not (i in seen or seen.add(i))][:4]


def _overlap(question: str, utterances: list[tuple[int, str]]) -> list[int]:
    q = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if w not in _STOP and len(w) > 2}
    if not q:
        return []
    scored = []
    for i, text in utterances:
        t = {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}
        hits = len(q & t)
        if hits:
            scored.append((hits, -i, i))
    scored.sort(reverse=True)
    return [i for _, _, i in scored[:3]]


__all__ = ["Answer", "answer", "REFUSAL"]
