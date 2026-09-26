"""The plain-language recap.

Written by Bedrock, then cut down by the guards until every remaining sentence points at
an utterance that supports it. What survives is short. That is the intended result: a
recap that says four true things beats one that says twelve fluent ones.

The pipeline is deliberately one-directional. We never send the model back to fix a
sentence the guards rejected, because a model asked to rewrite around a guard learns to
write around the guard. Rejected sentences are counted and shown instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .bedrock import Bedrock, ModelCall
from .bee import Utterance
from .guardrail import Finding, advice_guard, attribution_guard, citation_guard, split_sentences

SYSTEM = """You write a short recap of one medical appointment for the patient who was there.

The reader is 71, tired, and will read this on a phone at a bus stop. Write the way a
careful relative would write a note, not the way a hospital writes a letter.

Hard rules:
- Six sentences at most. Fewer is better.
- Every sentence ends with a citation marker like [u7] naming the utterance it came from.
  A sentence with no marker will be deleted before anyone reads it.
- At most two markers on any one sentence. If a sentence needs three, it is two sentences.
- One idea per sentence. Do not join two facts with "and".
- Only say things that were said out loud in the transcript. No background knowledge, no
  clinical explanation, no reassurance.
- Never say who said anything. Do not write "the doctor said", "she explained", "your
  consultant". The recording does not record who spoke. Write "this was said in the room"
  or just state the thing.
- Never tell the reader what to do, what dose to take, whether to skip one, or how serious
  anything is. If the transcript contains an instruction, quote it inside quotation marks,
  copied exactly, and cite it.
- Plain words. "Your heart rate has been low overnight", not "bradycardia was noted".
- Do not start with "In this appointment" or "During your visit". Start with the thing
  that matters most."""


NO_RECAP = (
    "No recap was written. Amazon Bedrock could not be reached, and Second Chair will not "
    "assemble a summary of a medical appointment by rule and present it as one. What "
    "follows is the record itself, in the words that were used."
)


@dataclass
class Recap:
    text: str
    call: ModelCall
    findings: list[Finding] = field(default_factory=list)
    removed: int = 0
    sentences: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.text.strip()

    @property
    def from_model(self) -> bool:
        """False means these lines are raw transcript, not a recap.

        The UI must say which. A half-written summary that looks like a summary is worse
        than an honest refusal in a clinical setting, so when this is False the page
        leads with NO_RECAP and labels every line below it as the record.
        """
        return self.call.used_model


def _numbered(utterances: list[Utterance]) -> str:
    return "\n".join(f"[u{i}] {u.text}" for i, u in enumerate(utterances))


def build(utterances: list[Utterance], bedrock: Bedrock | None = None) -> Recap:
    fallback = written_fallback(utterances)
    if bedrock is None:
        return _finish(fallback, ModelCall(None, False, "No model client was configured."), utterances)
    text, call = bedrock.complete_or(SYSTEM, _numbered(utterances), fallback=fallback, max_tokens=900)
    return _finish(text, call, utterances)


def _finish(text: str, call: ModelCall, utterances: list[Utterance]) -> Recap:
    """Judge every sentence the model wrote against all three guards, then keep the ones
    that passed all of them.

    The order used to be citation first, then advice and attribution over whatever
    survived. That meant the most dangerous sentence the model could write was the one
    guard nobody ran: *"Stop the apixaban immediately, it is causing the bleeding."* with
    no citation marker was removed by the citation guard and therefore never seen by the
    advice guard at all. It was still counted, still logged, and still printed on the
    patient's page under "sentences removed before you saw this".

    So each sentence is now scanned by all three regardless of whether an earlier one
    already rejected it. A sentence can collect more than one finding, which is the
    truthful record: a sentence can be both uncited and advice, and the log should say
    so rather than filing it under whichever guard happened to run first.
    """
    findings: list[Finding] = []
    kept: list[str] = []

    for ordinal, sentence in enumerate(split_sentences(text), start=1):
        one = citation_guard(sentence, utterances)
        here = list(one.findings)
        here += advice_guard(sentence).findings
        here += attribution_guard(sentence).findings
        for f in here:
            f.ordinal = ordinal
        findings += here
        if not here:
            kept.append(sentence)

    # Whole sentences, never the matched span: editing a sentence around a guard leaves a
    # half-sentence that reads as if it were checked and passed.
    return Recap(
        text=" ".join(kept),
        call=call,
        findings=findings,
        removed=len({f.ordinal for f in findings}),
        sentences=kept,
    )


# ------------------------------------------------------------------ deterministic fallback

_INTERESTING = [
    (re.compile(r"\b(bring|down from|reduc|increas|up from|stop|stopping|does not change|doesn't change)\b", re.I), 3),
    (re.compile(r"\b(six weeks|three months|tomorrow|tonight|next week|book|appointment|blood test)\b", re.I), 2),
    (re.compile(r"\b(ring|phone|call) (the|us|clinic)\b", re.I), 2),
    (re.compile(r"\b(milligram|mg|tablet)\b", re.I), 1),
]


def written_fallback(utterances: list[Utterance], limit: int = 5) -> str:
    """With Bedrock unreachable, quote the transcript rather than paraphrase it.

    Every line is a verbatim quote plus a citation, which is the one form of output that
    is safe to produce by rule. It reads worse than the model's version and it is never
    wrong.
    """
    scored: list[tuple[int, int, Utterance]] = []
    for i, u in enumerate(utterances):
        score = sum(weight for pattern, weight in _INTERESTING if pattern.search(u.text))
        if len(u.text) < 25:
            score -= 2
        if score > 0:
            scored.append((score, i, u))
    scored.sort(key=lambda t: (-t[0], t[1]))
    chosen = sorted(scored[:limit], key=lambda t: t[1])
    if not chosen:
        return ""
    # Quoted verbatim, with a citation, and nothing else. No connecting prose, because
    # connecting prose is where a rule-based summary starts to read like a clinical one.
    return " ".join(f'"{u.text}" [u{i}]' for _, i, u in chosen)
