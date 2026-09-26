"""The Bee `/v1/*` boundary.

MOCKED, and mocked deliberately low. Every Bee developer path (`bee-cli`, `bee proxy`,
the MCP server, the Skill) dead-ends at `bee login`, which needs the Bee iOS app with
Developer Mode unlocked. We have no device.

So `MockBeeTransport` replaces exactly one thing: the HTTP call. It answers the
documented `/v1/*` routes with the documented shapes, reading them out of
`fixtures/*.json`. `BeeClient` above it is the real client: it builds the paths, parses
the payloads and knows nothing about where the bytes came from. Pointing Second Chair at
a real Bee account is `BeeClient(HttpTransport("http://127.0.0.1:8787"))` and nothing
else changes.

`MockBeeTransport` answers the full documented set it might be asked for: `/v1/user`,
`/v1/conversations`, `/v1/conversations/stream`, `/v1/conversations/:id`, and `GET`+`POST`
on `/v1/facts` and `/v1/todos`. Second Chair itself calls three of those paths: the
stream, and `GET`+`POST` on facts and on todos, five calls and two of them writes. `GET
/v1/facts` is the one that matters most and is the least obvious: `collisions.py` reads
facts from the days either side of the appointment, so the mock has to serve a week and
not a room. A fixture covering only the consultation would quietly remove the one feature
a phone with a record button cannot copy. `/v1/user` and the conversation list and lookup
are answered because a faithful mock answers the shape it claims to mock, not because the
product asks for them.

Two things about the wire shape matter enough to repeat here:

* `utterance.speaker` is `Unknown`. That is what Bee's own `bee now` sample output shows
  and we treat it as permanent. Nothing downstream of this module is allowed to branch
  on it. See `SPEC.md`, "Speaker-blindness".
* Utterances are fragments, not sentences. "and the water tablet, we'll leave that for
  now" is a whole event. Anything that assumes clean sentences will break on real data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Protocol

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


# --------------------------------------------------------------------------- wire types


@dataclass(frozen=True)
class Utterance:
    """One `new-utterance` stream event, flattened.

    `speaker` is carried so the shape stays faithful. It is never read.
    """

    conversation_uuid: str
    created_at_ms: int
    text: str
    speaker: str = "Unknown"

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> "Utterance":
        return cls(
            conversation_uuid=event["conversation_uuid"],
            created_at_ms=int(event["created_at_ms"]),
            text=event["utterance"]["text"],
            speaker=event["utterance"].get("speaker", "Unknown"),
        )

    def to_event(self) -> dict[str, Any]:
        return {
            "conversation_uuid": self.conversation_uuid,
            "created_at_ms": self.created_at_ms,
            "utterance": {"speaker": self.speaker, "text": self.text},
        }


@dataclass(frozen=True)
class Conversation:
    id: str
    start_time: str
    end_time: str | None
    short_summary: str
    state: str


@dataclass(frozen=True)
class Fact:
    """A Bee fact. `source_quote` is ours: see friction item 7.

    Bee's `/v1/facts` has no provenance field, and Second Chair refuses to hold a fact it
    cannot quote, so we carry the quote alongside and would write it into `text` on a
    real account.
    """

    id: str
    text: str
    created_at: str
    visibility: str = "PRIVATE"
    source_conversation_uuid: str | None = None
    source_quote: str | None = None


@dataclass(frozen=True)
class Todo:
    id: str
    text: str
    completed: bool
    created_at: str


# ----------------------------------------------------------------------------- transport


class Transport(Protocol):
    def get(self, path: str, **params: Any) -> Any: ...
    def post(self, path: str, body: dict[str, Any]) -> Any: ...


class BeeApiError(RuntimeError):
    def __init__(self, status: int, path: str, detail: str = "") -> None:
        super().__init__(f"{status} {path} {detail}".strip())
        self.status = status
        self.path = path


class MockBeeTransport:
    """Answers the documented `/v1/*` routes from fixture files.

    OBVIOUSLY MOCKED. Every fixture it loads carries a `_mock` key saying so in prose,
    and `describe()` reproduces it for the UI so nobody can mistake the demo for live
    data.
    """

    def __init__(self, fixtures: Path = FIXTURES) -> None:
        self._consultation = json.loads((fixtures / "consultation.json").read_text())
        self._week = json.loads((fixtures / "week.json").read_text())
        self._todos: list[dict[str, Any]] = []
        self._written_facts: list[dict[str, Any]] = []
        self._next_id = 1

    def describe(self) -> str:
        return self._consultation["_mock"]

    def get(self, path: str, **params: Any) -> Any:
        if path == "/v1/user":
            return {"id": "usr_mock", "name": "Ada Mensah", "_mock": True}
        if path == "/v1/conversations":
            return {"conversations": self._consultation["conversations"]}
        if path == "/v1/conversations/stream":
            uuid = params.get("conversation_uuid")
            events = self._consultation["events"]
            if uuid:
                events = [e for e in events if e["conversation_uuid"] == uuid]
            return {"events": events}
        if path.startswith("/v1/conversations/"):
            uuid = path.rsplit("/", 1)[-1]
            for c in self._consultation["conversations"]:
                if c["id"] == uuid:
                    return c
            raise BeeApiError(404, path, "conversation not found")
        if path == "/v1/facts":
            return {"facts": self._week["facts"] + self._written_facts}
        if path == "/v1/todos":
            return {"todos": self._todos}
        raise BeeApiError(404, path, "no mock route")

    def post(self, path: str, body: dict[str, Any]) -> Any:
        if path == "/v1/todos":
            todo = {
                "id": f"todo_mock_{self._next_id}",
                "text": body["text"],
                "completed": False,
                "created_at": "2026-09-22T09:16:00Z",
            }
            self._next_id += 1
            self._todos.append(todo)
            return todo
        if path == "/v1/facts":
            fact = {
                "id": f"fact_mock_{self._next_id}",
                "text": body["text"],
                "created_at": "2026-09-22T09:16:00Z",
                "visibility": body.get("visibility", "PRIVATE"),
            }
            self._next_id += 1
            self._written_facts.append(fact)
            return fact
        raise BeeApiError(404, path, "no mock route")


# -------------------------------------------------------------------------------- client


class BeeClient:
    """The real client. Not mocked. Only its transport is."""

    def __init__(self, transport: Transport) -> None:
        self._t = transport

    # reads
    def user(self) -> dict[str, Any]:
        return self._t.get("/v1/user")

    def conversations(self) -> list[Conversation]:
        payload = self._t.get("/v1/conversations")
        return [
            Conversation(
                id=c["id"],
                start_time=c["start_time"],
                end_time=c.get("end_time"),
                short_summary=c.get("short_summary", ""),
                state=c.get("state", "COMPLETED"),
            )
            for c in payload["conversations"]
        ]

    def stream(self, conversation_uuid: str | None = None) -> Iterator[Utterance]:
        payload = self._t.get("/v1/conversations/stream", conversation_uuid=conversation_uuid)
        for event in payload["events"]:
            yield Utterance.from_event(event)

    def facts(self) -> list[Fact]:
        payload = self._t.get("/v1/facts")
        return [
            Fact(
                id=f["id"],
                text=f["text"],
                created_at=f["created_at"],
                visibility=f.get("visibility", "PRIVATE"),
                source_conversation_uuid=f.get("source_conversation_uuid"),
                source_quote=f.get("source_quote"),
            )
            for f in payload["facts"]
        ]

    # writes
    def create_todo(self, text: str) -> Todo:
        t = self._t.post("/v1/todos", {"text": text})
        return Todo(id=t["id"], text=t["text"], completed=t["completed"], created_at=t["created_at"])

    def create_fact(self, text: str, visibility: str = "PRIVATE") -> Fact:
        f = self._t.post("/v1/facts", {"text": text, "visibility": visibility})
        return Fact(id=f["id"], text=f["text"], created_at=f["created_at"], visibility=f["visibility"])

    def todos(self) -> list[Todo]:
        payload = self._t.get("/v1/todos")
        return [
            Todo(id=t["id"], text=t["text"], completed=t["completed"], created_at=t["created_at"])
            for t in payload["todos"]
        ]
