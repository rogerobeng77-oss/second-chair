"""The summary for the relative who could not be there.

The person who most needs this is the daughter three hundred miles away who is the only
copy of the medication schedule. BRFSS 2022: 27.0% of older-adult caregivers are caring
for someone with a cognitive impairment, and 36.5% give twenty hours a week or more.

Sharing is where a consent-first product usually gives up, so this is narrow on purpose.
The announcement in the room asked whether the wearer could record so they could remember
it afterwards. It did not ask whether the whole transcript could be forwarded. So the
share is a derived view, not the record:

* Medication rows, next dates, and which questions were not covered. That is all.
* No transcript. Not a truncated transcript, not a "read more". The utterances never
  enter this document.
* Every claim carries its verbatim quote, because a carer acting on a paraphrase is the
  failure mode this product exists to prevent.
* It expires, and it says when.
* It says, at the top and at the bottom, that it is not the medical record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .meds import ChangeKind, HUMAN, MedChange
from .questions import Coverage, QuestionOutcome

NOT_THE_RECORD = (
    "This is a note made by a device in the room, not a medical record. The clinic's own "
    "summary is the one that counts. If the two disagree, the clinic is right."
)


@dataclass
class ShareLine:
    heading: str
    body: str
    quote: str


@dataclass
class RelativeSummary:
    for_name: str
    appointment_label: str
    appointment_date: str
    lines: list[ShareLine] = field(default_factory=list)
    not_covered: list[str] = field(default_factory=list)
    expires_at: str = ""
    disclaimer: str = NOT_THE_RECORD

    def as_dict(self) -> dict[str, object]:
        return {
            "for_name": self.for_name,
            "appointment_label": self.appointment_label,
            "appointment_date": self.appointment_date,
            "lines": [{"heading": l.heading, "body": l.body, "quote": l.quote} for l in self.lines],
            "not_covered": self.not_covered,
            "expires_at": self.expires_at,
            "disclaimer": self.disclaimer,
        }

    def as_text(self) -> str:
        """What actually gets pasted into a message. Plain text, because that is what
        lands on a phone at a nurses' station without a data plan."""
        out = [f"{self.appointment_label}, {self.appointment_date}", "", NOT_THE_RECORD, ""]
        for l in self.lines:
            out.append(f"{l.heading}: {l.body}")
            out.append(f'  Recorded in the room: "{l.quote}"')
            out.append("")
        if self.not_covered:
            out.append("Written down beforehand and not covered in the room:")
            out += [f"  - {q}" for q in self.not_covered]
            out.append("")
        out.append(f"This summary stops working on {self.expires_at}.")
        out.append(NOT_THE_RECORD)
        return "\n".join(out)


_ORDER = {
    ChangeKind.STOPPED: 0,
    ChangeKind.REDUCED: 1,
    ChangeKind.INCREASED: 2,
    ChangeKind.STARTED: 3,
    ChangeKind.UNCLEAR: 4,
    ChangeKind.UNCHANGED: 5,
}


def build(
    for_name: str,
    appointment_label: str,
    appointment_date: str,
    meds: list[MedChange],
    outcomes: list[QuestionOutcome],
    retention_days: int = 30,
    now: datetime | None = None,
) -> RelativeSummary:
    now = now or datetime.now(timezone.utc)
    lines = [_line(m) for m in sorted(meds, key=lambda m: (_ORDER[m.kind], m.utterance_index))]
    not_covered = [
        o.text for o in sorted(outcomes, key=lambda o: (not o.marked_important, o.id))
        if o.coverage is not Coverage.COVERED
    ]
    return RelativeSummary(
        for_name=for_name,
        appointment_label=appointment_label,
        appointment_date=appointment_date,
        lines=lines,
        not_covered=not_covered,
        expires_at=(now + timedelta(days=retention_days)).strftime("%d %B %Y"),
    )


def _line(m: MedChange) -> ShareLine:
    if m.kind is ChangeKind.UNCLEAR:
        body = f"{m.medicine}: what was meant was not said clearly enough to write down."
    elif m.kind is ChangeKind.UNCHANGED:
        body = f"{m.medicine}: not changed" + (f", {m.to_dose}" if m.to_dose else "")
    elif m.kind is ChangeKind.STOPPED:
        body = f"{m.medicine}: stopping" + (f", was {m.from_dose}" if m.from_dose else "")
    else:
        parts = [m.medicine]
        if m.from_dose and m.to_dose:
            parts.append(f"{m.from_dose} to {m.to_dose}")
        elif m.to_dose:
            parts.append(m.to_dose)
        if m.timing:
            parts.append(m.timing)
        body = parts[0] + ": " + ", ".join(parts[1:]) if len(parts) > 1 else parts[0]
    return ShareLine(heading=HUMAN[m.kind], body=body, quote=m.quote)
