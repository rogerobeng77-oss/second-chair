"""Where the appointment meets the rest of the week.

This is the only feature in Second Chair that a phone with a record button cannot do, and
it is here because the research file's strongest objection deserved an answer in code
rather than in prose. A recorder holds one room. Bee holds the week: `GET /v1/facts`
already contains what your sister said on Tuesday and what you said on Sunday about your
knee. Setting the consultation against that is the argument for the wearable.

The discipline is the same as everywhere else. A collision states two recorded things and
the relationship between them. It never draws the conclusion. "Something was said on
Sunday about taking ibuprofen most days, and the ibuprofen question you wrote down was
not covered in the room" is a fact about two recordings. "Ask about the ibuprofen, it can
interact with your blood thinner" is medicine, and Second Chair does not practise it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .bee import Fact, Utterance
from .guardrail import advice_guard, attribution_guard
from .meds import ChangeKind, MedChange
from .questions import Coverage, QuestionOutcome


class CollisionKind(str, Enum):
    UNASKED_BUT_DISCUSSED = "unasked_but_discussed"
    STOCK_ON_HAND = "stock_on_hand"
    LOGISTICS = "logistics"
    PLAN_AHEAD = "plan_ahead"


@dataclass
class Collision:
    kind: CollisionKind
    heading: str
    from_the_week: str
    week_quote: str
    week_date: str
    from_the_room: str
    room_quote: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "heading": self.heading,
            "from_the_week": self.from_the_week,
            "week_quote": self.week_quote,
            "week_date": self.week_date,
            "from_the_room": self.from_the_room,
            "room_quote": self.room_quote,
        }


def _pretty(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%A %d %B")
    except ValueError:
        return iso


def _topic_words(s: str) -> set[str]:
    stop = {"the", "a", "an", "is", "it", "my", "for", "and", "to", "of", "in", "on", "she", "her", "has", "been"}
    return {w for w in re.findall(r"[a-z]+", s.lower()) if w not in stop and len(w) > 3}


def find(
    facts: list[Fact],
    outcomes: list[QuestionOutcome],
    meds: list[MedChange],
    utterances: list[Utterance],
) -> list[Collision]:
    facts = [f for f in facts if _fact_is_safe(f)]
    out: list[Collision] = []
    out += _unasked_but_discussed(facts, outcomes)
    out += _stock_on_hand(facts, meds)
    out += _logistics(facts, utterances)
    out += _plan_ahead(facts, outcomes)
    return out


def _fact_is_safe(fact: Fact) -> bool:
    """A Bee fact is somebody else's model output, and it lands on a patient's page.

    `text` is written by Bee's own summariser, not by us and not by the room: "The clinic
    asked Ada to bring the heart monitor readings" is a sentence no one said. Every other
    channel into this app is guarded and this one was not, so a fact reading "she said to
    stop the ibuprofen" would have rendered verbatim, in quotation marks, under a heading
    saying it touches this appointment. The guards we already own are the right ones.

    A fact with no `source_quote` is dropped rather than shown, because the page presents
    `week_quote` inside quotation marks. On a real Bee account `/v1/facts` carries no
    provenance field at all, so the fallback would have put Bee's generated sentence on
    screen as though somebody had said it. A fabricated quotation on a medical page is
    the failure this product exists to prevent; having none is merely a smaller page.
    """
    if not fact.source_quote:
        return False
    return (
        advice_guard(fact.text).clean
        and attribution_guard(fact.text).clean
        and advice_guard(fact.source_quote).clean
    )


def _unasked_but_discussed(facts: list[Fact], outcomes: list[QuestionOutcome]) -> list[Collision]:
    """A question you did not get to, about something you were recorded talking about."""
    found: list[Collision] = []
    unasked = [o for o in outcomes if o.coverage is not Coverage.COVERED]
    for o in unasked:
        q_words = _topic_words(o.text)
        for f in facts:
            if len(q_words & _topic_words(f.text)) < 1:
                continue
            found.append(
                Collision(
                    kind=CollisionKind.UNASKED_BUT_DISCUSSED,
                    heading="You wrote this down, and it came up earlier in the week",
                    from_the_week=f.text,
                    week_quote=f.source_quote,
                    week_date=_pretty(f.created_at),
                    from_the_room=f"Your question was not covered in the room: {o.text}",
                    room_quote=o.evidence_quote,
                )
            )
            break
    return found


def _stock_on_hand(facts: list[Fact], meds: list[MedChange]) -> list[Collision]:
    """A medicine that stopped, that you were recorded collecting."""
    found: list[Collision] = []
    stopping = [m for m in meds if m.kind is ChangeKind.STOPPED]
    for m in stopping:
        for f in facts:
            if m.medicine.lower() not in f.text.lower():
                continue
            found.append(
                Collision(
                    kind=CollisionKind.STOCK_ON_HAND,
                    heading="You have some of this at home",
                    from_the_week=f.text,
                    week_quote=f.source_quote,
                    week_date=_pretty(f.created_at),
                    from_the_room=f"{m.medicine} was recorded in the room as stopping.",
                    room_quote=m.quote,
                )
            )
            break
    return found


_LOGISTICS_HINT = re.compile(r"\b(pharmacy|chemist|closes|opening|reception|book|readings?|monitor|printouts?)\b", re.I)


def _logistics(facts: list[Fact], utterances: list[Utterance]) -> list[Collision]:
    """A practical detail from the week that the room touched on too.

    Two rules beyond the word match, both learned from looking at the rendered page.

    One shared word was enough, so "the pharmacy on Oxford Street closes at five on
    Fridays" was paired with "take half a tablet until that box runs out, then the
    pharmacy will give you the two point five strength" — a dosing instruction, printed
    under a heading calling it practical and a line calling it related. Neither is true of
    it, and putting a dose beside an unrelated errand is exactly the juxtaposition that
    invents a meaning nobody said. So an utterance that reads as an instruction about a
    medicine is never the anchor for a logistics pairing; it belongs to the medicines
    panel, where it is nailed to its row.

    And the app does not claim a relationship it has only inferred from vocabulary. It
    names the words the two have in common and lets the reader decide, which is the same
    discipline as everywhere else here.
    """
    found: list[Collision] = []
    for f in facts:
        if not _LOGISTICS_HINT.search(f.text):
            continue
        anchor = None
        shared: set[str] = set()
        for u in utterances:
            if not _LOGISTICS_HINT.search(u.text):
                continue
            if not advice_guard(u.text).clean:
                continue
            overlap = _topic_words(f.text) & _topic_words(u.text)
            if len(overlap) >= 2:
                anchor, shared = u, overlap
                break
        if anchor is None:
            continue
        found.append(
            Collision(
                kind=CollisionKind.LOGISTICS,
                heading="Something practical from earlier in the week",
                from_the_week=f.text,
                week_quote=f.source_quote,
                week_date=_pretty(f.created_at),
                from_the_room="The room used the same words: " + ", ".join(sorted(shared)) + ".",
                room_quote=anchor.text,
            )
        )
    return found


def _plan_ahead(facts: list[Fact], outcomes: list[QuestionOutcome]) -> list[Collision]:
    """A date in the week's facts that an unasked question depends on."""
    found: list[Collision] = []
    dated = [f for f in facts if re.search(r"\b(december|january|february|march|april|may|june|july|august|september|october|november)\b", f.text, re.I)]
    for o in outcomes:
        if o.coverage is Coverage.COVERED:
            continue
        for f in dated:
            if not (_topic_words(o.text) & _topic_words(f.text)):
                continue
            found.append(
                Collision(
                    kind=CollisionKind.PLAN_AHEAD,
                    heading="There is a date attached to this one",
                    from_the_week=f.text,
                    week_quote=f.source_quote,
                    week_date=_pretty(f.created_at),
                    from_the_room=f"Not covered in the room: {o.text}",
                    room_quote=None,
                )
            )
            break
    return found


def dedupe(found: list[Collision]) -> list[Collision]:
    seen: set[tuple[str, str]] = set()
    out: list[Collision] = []
    for c in found:
        key = ("", c.week_quote)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out
