"""SQLite. Text only, never audio, and it forgets on a clock.

Two design positions live in this file.

**Text, not audio.** No audio file is ever written, because audio is a voiceprint and a
voiceprint is a biometric identifier in Illinois and Texas. Bee produces text; we keep
text. There is no code path in Second Chair that persists a waveform.

**It is not the record of truth, and it is built so it cannot drift into being one.**
45 CFR 164.508(a)(1) means a wearable in a consulting room creates protected health
information on a consumer device outside the covered entity's control. We cannot make
that problem disappear, so we make the artefact small and short-lived instead:

* every appointment has an expiry, set when it is created, and `purge_expired` drops the
  transcript while keeping the consent record, because the consent record is the proof
  that nothing improper was kept;
* any row can be superseded by a correction saying the clinic's own summary differs, and
  the correction is what exports;
* the export carries the disclaimer at the top and the bottom.

The refusal log is also here. Every time Second Chair declines to answer something, the
question and the refusal are written down and shown in the UI. A product that refuses
things ought to be auditable about what it refused.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS appointment (
  id TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  held_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  consent_json TEXT NOT NULL,
  derived_json TEXT NOT NULL DEFAULT '{}',
  transcript_purged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS utterance (
  appointment_id TEXT NOT NULL,
  idx INTEGER NOT NULL,
  text TEXT NOT NULL,
  conversation_uuid TEXT NOT NULL,
  created_at_ms INTEGER NOT NULL,
  PRIMARY KEY (appointment_id, idx)
);
CREATE TABLE IF NOT EXISTS dropped (
  appointment_id TEXT NOT NULL,
  text TEXT NOT NULL,
  reason TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS correction (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  appointment_id TEXT NOT NULL,
  target TEXT NOT NULL,
  note TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refusal (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  appointment_id TEXT,
  question TEXT NOT NULL,
  answer TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_utt_text ON utterance(appointment_id);
"""


@dataclass
class AppointmentRow:
    id: str
    label: str
    held_at: str
    created_at: str
    expires_at: str
    consent: dict
    derived: dict
    transcript_purged: bool

    @property
    def days_left(self) -> int:
        try:
            exp = datetime.fromisoformat(self.expires_at)
        except ValueError:
            return 0
        return max(0, (exp - datetime.now(timezone.utc)).days)


class Store:
    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with closing(self._conn.cursor()) as cur:
            cur.executescript(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------ appointments
    def save_appointment(
        self,
        appointment_id: str,
        label: str,
        held_at: str,
        consent: dict,
        utterances: list[tuple[int, str, str, int]],
        dropped: list[tuple[str, str]],
        retention_days: int = 30,
    ) -> None:
        now = datetime.now(timezone.utc)
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "INSERT OR REPLACE INTO appointment"
                " (id,label,held_at,created_at,expires_at,consent_json,derived_json,transcript_purged)"
                " VALUES (?,?,?,?,?,?,COALESCE((SELECT derived_json FROM appointment WHERE id=?),'{}'),0)",
                (
                    appointment_id, label, held_at, now.isoformat(),
                    (now + timedelta(days=retention_days)).isoformat(),
                    json.dumps(consent), appointment_id,
                ),
            )
            cur.execute("DELETE FROM utterance WHERE appointment_id=?", (appointment_id,))
            cur.executemany(
                "INSERT INTO utterance (appointment_id,idx,text,conversation_uuid,created_at_ms) VALUES (?,?,?,?,?)",
                [(appointment_id, i, t, c, ms) for i, t, c, ms in utterances],
            )
            cur.execute("DELETE FROM dropped WHERE appointment_id=?", (appointment_id,))
            cur.executemany(
                "INSERT INTO dropped (appointment_id,text,reason) VALUES (?,?,?)",
                [(appointment_id, t, r) for t, r in dropped],
            )
        self._conn.commit()

    def save_derived(self, appointment_id: str, derived: dict) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "UPDATE appointment SET derived_json=? WHERE id=?",
                (json.dumps(derived, default=str), appointment_id),
            )
        self._conn.commit()

    def appointments(self) -> list[AppointmentRow]:
        with closing(self._conn.cursor()) as cur:
            rows = cur.execute("SELECT * FROM appointment ORDER BY held_at DESC").fetchall()
        return [self._row(r) for r in rows]

    def appointment(self, appointment_id: str) -> AppointmentRow | None:
        with closing(self._conn.cursor()) as cur:
            r = cur.execute("SELECT * FROM appointment WHERE id=?", (appointment_id,)).fetchone()
        return self._row(r) if r else None

    @staticmethod
    def _row(r: sqlite3.Row) -> AppointmentRow:
        return AppointmentRow(
            id=r["id"], label=r["label"], held_at=r["held_at"], created_at=r["created_at"],
            expires_at=r["expires_at"], consent=json.loads(r["consent_json"]),
            derived=json.loads(r["derived_json"] or "{}"),
            transcript_purged=bool(r["transcript_purged"]),
        )

    def utterances(self, appointment_id: str) -> list[tuple[int, str]]:
        with closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT idx,text FROM utterance WHERE appointment_id=? ORDER BY idx", (appointment_id,)
            ).fetchall()
        return [(r["idx"], r["text"]) for r in rows]

    def dropped(self, appointment_id: str) -> list[tuple[str, str]]:
        with closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT text,reason FROM dropped WHERE appointment_id=?", (appointment_id,)
            ).fetchall()
        return [(r["text"], r["reason"]) for r in rows]

    # ---------------------------------------------------------------------- retention
    def purge_expired(self, now: datetime | None = None) -> list[str]:
        """Drop transcripts past their expiry. Keep the consent record and the derived view.

        The consent record outlives the transcript on purpose. If anyone ever asks what
        this device was doing in a consulting room in September, the answer should still
        exist after the words themselves are gone.
        """
        now = now or datetime.now(timezone.utc)
        purged: list[str] = []
        with closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT id,expires_at FROM appointment WHERE transcript_purged=0"
            ).fetchall()
            for r in rows:
                try:
                    exp = datetime.fromisoformat(r["expires_at"])
                except ValueError:
                    continue
                if exp <= now:
                    cur.execute("DELETE FROM utterance WHERE appointment_id=?", (r["id"],))
                    cur.execute("DELETE FROM dropped WHERE appointment_id=?", (r["id"],))
                    cur.execute("UPDATE appointment SET transcript_purged=1 WHERE id=?", (r["id"],))
                    purged.append(r["id"])
        self._conn.commit()
        return purged

    def forget(self, appointment_id: str) -> None:
        """The delete button. Everything goes, including the consent record."""
        with closing(self._conn.cursor()) as cur:
            for table in ("utterance", "dropped", "correction", "refusal"):
                cur.execute(f"DELETE FROM {table} WHERE appointment_id=?", (appointment_id,))
            cur.execute("DELETE FROM appointment WHERE id=?", (appointment_id,))
        self._conn.commit()

    # -------------------------------------------------------------------- corrections
    def add_correction(self, appointment_id: str, target: str, note: str) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO correction (appointment_id,target,note,created_at) VALUES (?,?,?,?)",
                (appointment_id, target, note, datetime.now(timezone.utc).isoformat()),
            )
        self._conn.commit()

    def corrections(self, appointment_id: str) -> dict[str, list[dict]]:
        with closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT target,note,created_at FROM correction WHERE appointment_id=? ORDER BY id",
                (appointment_id,),
            ).fetchall()
        out: dict[str, list[dict]] = {}
        for r in rows:
            out.setdefault(r["target"], []).append({"note": r["note"], "created_at": r["created_at"]})
        return out

    # ----------------------------------------------------------------------- refusals
    def add_refusal(self, appointment_id: str | None, question: str, answer: str) -> None:
        with closing(self._conn.cursor()) as cur:
            cur.execute(
                "INSERT INTO refusal (appointment_id,question,answer,created_at) VALUES (?,?,?,?)",
                (appointment_id, question, answer, datetime.now(timezone.utc).isoformat()),
            )
        self._conn.commit()

    def refusals(self, limit: int = 50) -> list[dict]:
        with closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT appointment_id,question,answer,created_at FROM refusal ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------------- search
    def search(self, needle: str) -> list[dict]:
        """Across every appointment still holding a transcript."""
        needle = needle.strip()
        if len(needle) < 2:
            return []
        # LIKE treats % and _ as wildcards, so "%%" would otherwise return the whole
        # table. Escape them and declare the escape character.
        escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if not escaped.replace("\\", "").strip():
            return []
        with closing(self._conn.cursor()) as cur:
            rows = cur.execute(
                "SELECT u.appointment_id, u.idx, u.text, a.label, a.held_at"
                " FROM utterance u JOIN appointment a ON a.id = u.appointment_id"
                " WHERE u.text LIKE ? ESCAPE '\\' ORDER BY a.held_at DESC, u.idx LIMIT 60",
                (f"%{escaped}%",),
            ).fetchall()
        return [dict(r) for r in rows]
