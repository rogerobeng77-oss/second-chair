"""The consent gate. This module is the product.

A consulting room has no statutory escape. Cal. Penal Code 632(c) lets a public gathering
out of the definition of a confidential communication; a room whose door is shut so it
cannot be overheard is the case the section was written about, and 637.2 prices a
violation at $5,000 with no actual damages required.

The one route left is 632(b), which excludes from "person" anyone "known by all parties to
a confidential communication to be overhearing or recording the communication", with
RCW 9.73.030(3) supplying the procedure: announce "in any reasonably effective manner",
provided "said announcement shall also be recorded". Make it, record it, and there is no
offence. Miss it, and nothing else helps.

So this module refuses to let a transcript exist until it holds a recorded announcement AND
a recorded response, and destroys everything but the refusal record if the answer was no.

The hard part is that there is nothing to construct. Second Chair has no start button,
because the person it is for is the person who forgets to press one. Capture is already
running when the appointment begins, so `establish()` has to FIND the announcement inside
a stream, read the next utterance as the reply, and rule on it. Four states, and three of
them keep nothing:

    PENDING     nothing announced. No transcript may exist.
    GRANTED     announced, recorded, answered affirmatively.
    REFUSED     answered in the negative. Everything is destroyed.
    UNANSWERED  announced, no response recorded. Everything is destroyed.

Ambiguity falls to REFUSED. The cost of being wrong that way is one lost recording. The
cost of being wrong the other way is a bystander's consent and $5,000. `_REFUSAL` is
therefore tested before `_ASSENT`, and "no, that's fine" comes out REFUSED deliberately.

Scoping is by the session window plus `conversation_uuid`, which is weaker than the rule
wants. The honest implementation would be voice attribution and neither we nor Bee can do
it: every speaker label is `Unknown`. The waiting room and the corridor arrive under
different uuids and are dropped unread. That limitation is printed on `/consent`, not
buried in a docstring.

What this module never records is who answered. See `ConsentRecord` below.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Iterable

from .bee import Utterance

# The wearer says this, out loud, before anything else. It is deliberately not a legal
# formula: it has to be a sentence a frightened 71-year-old will actually say in a
# consulting room. It names the device, names the recording, and asks.
ANNOUNCEMENT = (
    "Before we start, I'm wearing a device that writes down what's said in this room so "
    "I can remember it afterwards. It's recording now. Is that alright with you?"
)

# Matched against the utterance that follows the announcement. Order matters: refusals
# are tested first, because "no, that's fine" must not read as consent and "I'd rather
# you didn't" must not read as consent because it contains "you".
# Each alternative carries its own word boundaries. A single trailing \b on the group is
# wrong and silently breaks the most important case: "No," ends in a comma, so \b after
# it never matches and a plain refusal reads as no answer at all. That bug shipped for
# about ten minutes and the test that caught it is in tests/test_consent.py.
_REFUSAL = re.compile(
    r"(\bno\b\s*[,.!]|^\s*no\b|\bnot happy\b|\brather you did ?n[o']t\b|\bi'd rather not\b|"
    r"\bplease don'?t\b|\bturn it off\b|\bswitch it off\b|\bstop recording\b|"
    r"\bnot comfortable\b|\bi'm not\b|\bcan you not\b)",
    re.I,
)
_ASSENT = re.compile(
    r"\b(yes|yeah|yep|that's fine|that is fine|of course|go ahead|no problem|"
    r"fine by me|sure|absolutely|carry on|that's alright|no objection)\b",
    re.I,
)


class ConsentState(str, Enum):
    PENDING = "pending"          # nothing announced yet. No transcript may exist.
    GRANTED = "granted"          # announced, recorded, and answered affirmatively.
    REFUSED = "refused"          # answered in the negative. Everything is destroyed.
    UNANSWERED = "unanswered"    # announced, but no response was recorded.


@dataclass(frozen=True)
class ConsentRecord:
    """The first artefact. Written before any transcript, and kept if all else is wiped.

    `announcement_text` and `response_text` are both verbatim. That is the RCW
    9.73.030(3) requirement: the announcement itself is part of the recording.

    Note what this record does NOT say. It does not say who answered. Bee labels every
    speaker `Unknown` and we will not invent an attribution to make the record look
    better. The statutory test is whether the announcement was made in the room and
    heard, which the recording of the announcement and the recording of a response to it
    evidence. It is not whose mouth the answer came from.
    """

    state: ConsentState
    announcement_text: str
    announced_at_ms: int
    response_text: str | None
    responded_at_ms: int | None
    conversation_uuid: str
    jurisdiction: str
    institution_policy: str
    basis: str

    @property
    def may_retain_transcript(self) -> bool:
        return self.state is ConsentState.GRANTED

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "announcement_text": self.announcement_text,
            "announced_at": _iso(self.announced_at_ms),
            "response_text": self.response_text,
            "responded_at": _iso(self.responded_at_ms) if self.responded_at_ms else None,
            "conversation_uuid": self.conversation_uuid,
            "jurisdiction": self.jurisdiction,
            "institution_policy": self.institution_policy,
            "basis": self.basis,
            "attribution": "not recorded: Bee labels every speaker Unknown",
        }


def _iso(ms: int | None) -> str:
    if ms is None:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


# Idioms that contain "no" and mean yes. Without these, "sure, no problem" reads as a
# refusal. The list is short on purpose; anything not on it and still ambiguous falls to
# refusal, which costs the user a recording and costs nobody a lawsuit.
_ASSENT_IDIOM = re.compile(r"\b(no problem|no objection|no bother|no worries|no issue|not at all)\b", re.I)


def classify_response(text: str) -> ConsentState:
    """Refusal beats assent. A sentence containing both is a refusal.

    Deliberate consequence: "no, that's fine" comes out REFUSED. In answer to "is that
    alright with you?" those words are genuinely ambiguous, and the safe reading of an
    ambiguous answer in an all-party state is that consent was not given. The verbatim
    words are kept in the ledger either way, so a person can see exactly what the app
    heard and why it stopped.
    """
    probe = _ASSENT_IDIOM.sub(" ", text)
    if _REFUSAL.search(probe):
        return ConsentState.REFUSED
    if _ASSENT.search(text):
        return ConsentState.GRANTED
    return ConsentState.UNANSWERED


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", text.lower()).strip()


def match_announcement(text: str, expected: str = ANNOUNCEMENT) -> bool:
    """Did this utterance carry the announcement?

    Matched on content, not on speaker, and tolerant of transcription drift: we require
    the three load-bearing ideas (a device, that it is recording, and a question) rather
    than the exact string, because real capture will not return the exact string.
    """
    t = _normalise(text)
    has_device = any(w in t for w in ("device", "wearing", "recorder", "pendant"))
    has_recording = any(w in t for w in ("recording", "records", "writes down", "writing down"))
    has_question = "?" in text or any(
        w in t for w in ("is that alright", "is that ok", "is that okay", "do you mind", "may i")
    )
    return has_device and has_recording and has_question


@dataclass
class Session:
    """A room-scoped capture window. Rule 1.

    A session cannot exist without an end. `ends_at_ms` is required at construction, not
    set later, so there is no code path that opens an unbounded capture.
    """

    conversation_uuid: str
    starts_at_ms: int
    ends_at_ms: int
    jurisdiction: str = "CA"
    institution_policy: str = (
        "Patient-held recording is permitted by the clinic's own visitor policy. "
        "Second Chair sits inside that policy and does not invent one."
    )
    label: str = "Appointment"
    consent: ConsentRecord | None = field(default=None)

    def __post_init__(self) -> None:
        if self.ends_at_ms <= self.starts_at_ms:
            raise ValueError("a session must end after it starts; there is no open-ended capture")

    def in_window(self, u: Utterance) -> bool:
        return self.starts_at_ms <= u.created_at_ms < self.ends_at_ms

    def in_scope(self, u: Utterance) -> bool:
        """Rules 1 and 3 together."""
        return u.conversation_uuid == self.conversation_uuid and self.in_window(u)


@dataclass(frozen=True)
class Dropped:
    utterance: Utterance
    reason: str


def scope_filter(session: Session, stream: Iterable[Utterance]) -> tuple[list[Utterance], list[Dropped]]:
    """Apply rules 1 and 3. Returns what was kept and, for every drop, why.

    The dropped list is surfaced in the UI. A product that quietly discards audio is
    asking to be trusted; one that shows you the discard pile has earned a little.
    """
    kept: list[Utterance] = []
    dropped: list[Dropped] = []
    for u in stream:
        if u.conversation_uuid != session.conversation_uuid:
            dropped.append(Dropped(u, "outside the declared room: a different Bee conversation"))
        elif not session.in_window(u):
            dropped.append(Dropped(u, "outside the declared session window"))
        else:
            kept.append(u)
    return kept, dropped


class ConsentError(RuntimeError):
    """Raised when something tries to read a transcript that consent does not cover."""


def establish(session: Session, in_scope: list[Utterance]) -> tuple[ConsentRecord, list[Utterance]]:
    """Find the announcement, read the next utterance as the response, and rule on it.

    Returns the consent record and the utterances that may be retained. On anything but
    GRANTED that second list is empty, and the caller has nothing to write.
    """
    announcement_idx = next((i for i, u in enumerate(in_scope) if match_announcement(u.text)), None)

    if announcement_idx is None:
        record = ConsentRecord(
            state=ConsentState.PENDING,
            announcement_text="",
            announced_at_ms=session.starts_at_ms,
            response_text=None,
            responded_at_ms=None,
            conversation_uuid=session.conversation_uuid,
            jurisdiction=session.jurisdiction,
            institution_policy=session.institution_policy,
            basis="No announcement was recorded. Nothing was retained.",
        )
        session.consent = record
        return record, []

    announcement = in_scope[announcement_idx]
    response = in_scope[announcement_idx + 1] if announcement_idx + 1 < len(in_scope) else None
    state = classify_response(response.text) if response else ConsentState.UNANSWERED

    basis = {
        ConsentState.GRANTED: (
            "Announced out loud and recorded, and a response in the affirmative was recorded. "
            "Cal. Penal Code 632(b) excludes from 'person' anyone known by all parties to be "
            "recording. RCW 9.73.030(3) is satisfied by announcing in a reasonably effective "
            "manner and recording the announcement itself."
        ),
        ConsentState.REFUSED: (
            "A response in the negative was recorded. Every utterance in this session was "
            "discarded. This record is all that remains, and it is kept as proof that "
            "nothing was retained."
        ),
        ConsentState.UNANSWERED: (
            "The announcement was recorded but no response was. Without a recorded response "
            "there is no evidence the announcement was heard, so nothing is retained."
        ),
        ConsentState.PENDING: "No announcement was recorded.",
    }[state]

    record = ConsentRecord(
        state=state,
        announcement_text=announcement.text,
        announced_at_ms=announcement.created_at_ms,
        response_text=response.text if response else None,
        responded_at_ms=response.created_at_ms if response else None,
        conversation_uuid=session.conversation_uuid,
        jurisdiction=session.jurisdiction,
        institution_policy=session.institution_policy,
        basis=basis,
    )
    session.consent = record

    if state is not ConsentState.GRANTED:
        return record, []
    return record, in_scope[announcement_idx + 2 :]
