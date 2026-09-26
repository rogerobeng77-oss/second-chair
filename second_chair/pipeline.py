"""One appointment, start to finish.

The order here is the argument. Consent is established before a single utterance is
retained, and every later stage takes its input from what consent allowed rather than
from the raw stream. If the answer in the room was no, the functions below run on an
empty list and produce nothing, which is the correct behaviour and is what the tests
assert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import collisions as collide
from . import meds as meds_mod
from . import questions as questions_mod
from . import recap as recap_mod
from . import relative as relative_mod
from .bedrock import Bedrock, ModelCall
from .bee import BeeClient, Fact, Utterance
from .consent import ConsentRecord, ConsentState, Dropped, Session, establish, scope_filter
from .store import Store


@dataclass
class Result:
    session: Session
    consent: ConsentRecord
    retained: list[Utterance]
    dropped: list[Dropped]
    recap: recap_mod.Recap | None = None
    meds: list[meds_mod.MedChange] = field(default_factory=list)
    meds_call: ModelCall | None = None
    questions: list[questions_mod.QuestionOutcome] = field(default_factory=list)
    questions_call: ModelCall | None = None
    collisions: list[collide.Collision] = field(default_factory=list)
    relative: relative_mod.RelativeSummary | None = None
    todos: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.consent.state is not ConsentState.GRANTED


def run(
    client: BeeClient,
    session: Session,
    questions: list[dict[str, object]],
    facts: list[Fact] | None = None,
    fast: Bedrock | None = None,
    judgement: Bedrock | None = None,
    relative_name: str = "your daughter",
) -> Result:
    stream = list(client.stream())
    in_scope, dropped = scope_filter(session, stream)
    consent, retained = establish(session, in_scope)

    result = Result(session=session, consent=consent, retained=retained, dropped=dropped)
    if result.blocked:
        # Nothing further runs. This is the branch that makes the consent gate real
        # rather than decorative.
        return result

    result.recap = recap_mod.build(retained, fast)
    result.meds, result.meds_call = meds_mod.extract(retained, fast)
    result.questions, result.questions_call = questions_mod.reconcile(questions, retained, judgement or fast)
    result.collisions = collide.dedupe(
        collide.find(facts if facts is not None else client.facts(), result.questions, result.meds, retained)
    )
    result.relative = relative_mod.build(
        for_name=relative_name,
        appointment_label=session.label,
        appointment_date=_pretty(session.starts_at_ms),
        meds=result.meds,
        outcomes=result.questions,
    )
    result.todos = questions_mod.carry_forward(result.questions)
    return result


def _pretty(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%d %B %Y")


def persist(store: Store, appointment_id: str, result: Result, retention_days: int = 30) -> None:
    store.save_appointment(
        appointment_id=appointment_id,
        label=result.session.label,
        held_at=_pretty(result.session.starts_at_ms),
        consent=result.consent.as_dict(),
        utterances=[
            (i, u.text, u.conversation_uuid, u.created_at_ms) for i, u in enumerate(result.retained)
        ],
        dropped=[(d.utterance.text, d.reason) for d in result.dropped],
        retention_days=retention_days,
    )
    store.save_derived(appointment_id, derived_dict(result))


def derived_dict(result: Result) -> dict:
    return {
        "recap": {
            "from_model": result.recap.from_model if result.recap else False,
            "no_recap_notice": recap_mod.NO_RECAP,
            "text": result.recap.text if result.recap else "",
            "sentences": result.recap.sentences if result.recap else [],
            "removed": result.recap.removed if result.recap else 0,
            "provenance": result.recap.call.provenance if result.recap else "",
            # f.as_dict() has no `text`. See guardrail.Finding for why the refused
            # sentence must not cross this boundary.
            "findings": [f.as_dict() for f in (result.recap.findings if result.recap else [])],
        },
        "meds": [m.as_dict() for m in result.meds],
        "meds_provenance": result.meds_call.provenance if result.meds_call else "",
        "questions": [q.as_dict() for q in result.questions],
        "questions_provenance": result.questions_call.provenance if result.questions_call else "",
        "collisions": [c.as_dict() for c in result.collisions],
        "relative": result.relative.as_dict() if result.relative else {},
        "relative_text": result.relative.as_text() if result.relative else "",
        "todos": result.todos,
    }


def push_to_bee(client: BeeClient, result: Result) -> tuple[list[str], list[str]]:
    """Write the outcome back as Bee todos and facts.

    This is the half of the argument the microphone does not make. The consultation does
    not stay in an app; it lands in the same facts and todos the rest of the week is made
    of, which is why next Tuesday's conversation can collide with it.
    """
    todo_ids, fact_ids = [], []
    for text in result.todos:
        todo_ids.append(client.create_todo(text).id)
    for m in result.meds:
        fact_ids.append(
            client.create_fact(
                f'Recorded at {result.session.label}: {m.medicine}, {meds_mod.HUMAN[m.kind].lower()}. '
                f'The words used were "{m.quote}"'
            ).id
        )
    if result.consent.state is ConsentState.GRANTED:
        fact_ids.append(
            client.create_fact(
                f"Consent to record {result.session.label} was announced out loud and a response "
                f'was recorded: "{result.consent.response_text}"'
            ).id
        )
    return todo_ids, fact_ids
