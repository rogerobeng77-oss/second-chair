"""The web app.

One route carries the demo: `/replay`. It steps through the consultation utterance by
utterance at the pace it happened, and the consent seal at the top of the page stays shut
until the announcement and the answer have both arrived. Flip the answer to a refusal and
watch the same run end with an empty record and an intact consent ledger.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import ask as ask_mod
from . import pipeline
from .bedrock import Bedrock, FAST_MODELS, JUDGEMENT_MODELS
from .bee import BeeClient, MockBeeTransport
from .consent import ANNOUNCEMENT, ConsentState, Session
from .settings import Settings
from .store import Store

HERE = Path(__file__).resolve().parent
FIXTURES = HERE.parent / "fixtures"

APPOINTMENT_ID = "appt_2026_09_22"

# The fixture's declared room. Rule 1: a session is a uuid and a window, both fixed
# before capture, and there is no way to widen either from the UI.
SESSION_UUID = "conv_7f21c0"
SESSION_START_MS = 1790067600000
SESSION_END_MS = 1790068440000


class App:
    """Holds the wiring so tests can build one without a server."""

    def __init__(self, db: str = ":memory:") -> None:
        self.store = Store(db)
        self.settings = Settings()
        self.transport = MockBeeTransport(FIXTURES)
        self.client = BeeClient(self.transport)
        self.week = json.loads((FIXTURES / "week.json").read_text())
        self.consent_override: str | None = None
        self.last: pipeline.Result | None = None
        # Set by /forget. Without it the next page load quietly re-ingests the
        # appointment from the Bee fixture and the delete button is a lie.
        self.erased = False
        self._clients: dict[str, Bedrock] = {}

    # --------------------------------------------------------------------- pipeline
    def bedrock(self, tier: str | None = None) -> Bedrock | None:
        """One client per tier, kept for the life of the process.

        The preference chain leads with model ids this account is not entitled to yet, so
        a fresh client pays a failed probe on its first call. Keeping the client means
        that is paid once rather than three times per page load.
        """
        tier = tier or self.settings.model_tier
        if tier == "off":
            return None
        if tier not in self._clients:
            self._clients[tier] = Bedrock(models=JUDGEMENT_MODELS if tier == "judgement" else FAST_MODELS)
        return self._clients[tier]

    def session(self) -> Session:
        return Session(
            conversation_uuid=SESSION_UUID,
            starts_at_ms=SESSION_START_MS,
            ends_at_ms=SESSION_END_MS,
            jurisdiction=self.settings.jurisdiction,
            label="Cardiology follow-up",
        )

    def run(self) -> pipeline.Result:
        client = self.client
        if self.consent_override:
            client = BeeClient(_OverriddenTransport(self.transport, self.consent_override))
        result = pipeline.run(
            client=client,
            session=self.session(),
            questions=self.week["questions"],
            facts=self.client.facts(),
            fast=self.bedrock(),
            judgement=self.bedrock("judgement") if self.settings.model_tier == "judgement" else self.bedrock(),
            relative_name=self.settings.relative_name,
        )
        pipeline.persist(self.store, APPOINTMENT_ID, result, self.settings.retention_days)
        if not result.blocked:
            pipeline.push_to_bee(self.client, result)
        self.last = result
        return result

    def current(self) -> pipeline.Result | None:
        if self.erased:
            return None
        return self.last or self.run()


class _OverriddenTransport:
    """Replaces the clinician's answer, so the refusal path can be demonstrated live.

    It rewrites one utterance in the mocked stream and nothing else, which means the
    refusal branch runs through exactly the same code as the granted branch.
    """

    def __init__(self, inner: MockBeeTransport, replacement: str) -> None:
        self._inner = inner
        self._replacement = replacement

    def get(self, path: str, **params: object):
        payload = self._inner.get(path, **params)
        if path == "/v1/conversations/stream":
            events = [dict(e) for e in payload["events"]]
            for i, e in enumerate(events):
                if e["conversation_uuid"] == SESSION_UUID:
                    # the utterance immediately after the announcement
                    if i + 1 < len(events) and "device" in events[i]["utterance"]["text"].lower():
                        nxt = dict(events[i + 1])
                        nxt["utterance"] = dict(nxt["utterance"], text=self._replacement)
                        events[i + 1] = nxt
                        break
            return {"events": events}
        return payload

    def post(self, path: str, body: dict):
        return self._inner.post(path, body)


def create_app(db: str = ":memory:") -> FastAPI:
    state = App(db)
    app = FastAPI(title="Second Chair", docs_url=None, redoc_url=None)
    app.state.sc = state
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))

    def page(request: Request, name: str, **ctx):
        base = {
            "settings": state.settings,
            "mock_note": state.transport.describe(),
            "nav": name,
            "announcement": ANNOUNCEMENT,
        }
        base.update(ctx)
        return templates.TemplateResponse(request, name, base)

    # ------------------------------------------------------------------- the record
    @app.get("/", response_class=HTMLResponse)
    def record(request: Request):
        result = state.current()
        if result is None:
            return page(request, "erased.html")
        derived = pipeline.derived_dict(result)
        row = state.store.appointment(APPOINTMENT_ID)
        return page(
            request,
            "record.html",
            result=result,
            d=derived,
            row=row,
            corrections=state.store.corrections(APPOINTMENT_ID),
            granted=result.consent.state is ConsentState.GRANTED,
        )

    def _rerun(answer: str) -> bool:
        """Re-ingest the appointment with this answer to the announcement.

        Returns whether consent came back granted, which is the one thing the
        replay page needs to know to say what the record now holds.
        """
        state.consent_override = answer.strip() or None
        state.erased = False
        result = state.run()
        return result.consent.state is ConsentState.GRANTED

    @app.post("/rerun")
    def rerun(answer: str = Form(default="")):
        _rerun(answer)
        return RedirectResponse("/", status_code=303)

    @app.post("/api/rerun")
    def rerun_api(answer: str = Form(default="")):
        """What the replay page presses, and the reason it exists.

        `/api/replay` is a read: it visualises what the gate would do and
        changes nothing. So a judge who followed the README — refuse consent on
        `/replay`, then open `/` — was shown CONSENT REFUSED and then, one
        click later, CONSENT RECORDED and the whole transcript. The two forms
        that posted to `/rerun` both sent `answer=""`, so no control anywhere in
        the UI could put the record into the refused state at all.

        Running the appointment on `/replay` now runs it for real, and the page
        says which record is on `/` as a result.
        """
        granted = _rerun(answer)
        return JSONResponse({"granted": granted})

    # --------------------------------------------------------------------- replay
    @app.get("/replay", response_class=HTMLResponse)
    def replay(request: Request):
        return page(request, "replay.html", session_uuid=SESSION_UUID)

    @app.get("/api/replay")
    def replay_data(answer: str = ""):
        """Every event Bee would have delivered, marked with what the gate did to it."""
        transport = _OverriddenTransport(state.transport, answer) if answer else state.transport
        events = transport.get("/v1/conversations/stream")["events"]
        session = state.session()
        out = []
        announced = False
        answered = False
        for e in events:
            in_room = e["conversation_uuid"] == SESSION_UUID
            in_window = session.starts_at_ms <= e["created_at_ms"] < session.ends_at_ms
            if not in_room:
                status, why = "dropped", "a different Bee conversation, so outside the declared room"
            elif not in_window:
                status, why = "dropped", "outside the declared session window"
            elif not announced:
                status, why = "announcement", "the announcement, recorded as required by RCW 9.73.030(3)"
                announced = True
            elif not answered:
                status, why = "response", "the answer, recorded verbatim"
                answered = True
            else:
                status, why = "kept", "inside the room, after consent"
            out.append(
                {
                    "text": e["utterance"]["text"],
                    "speaker": e["utterance"]["speaker"],
                    "at_ms": e["created_at_ms"],
                    "conversation_uuid": e["conversation_uuid"],
                    "status": status,
                    "why": why,
                }
            )
        return JSONResponse({"events": out, "announcement": ANNOUNCEMENT})

    # -------------------------------------------------------------------- consent
    @app.get("/consent", response_class=HTMLResponse)
    def consent_page(request: Request):
        result = state.current()
        if result is None:
            return page(request, "erased.html")
        return page(
            request,
            "consent.html",
            consent=result.consent,
            dropped=result.dropped,
            row=state.store.appointment(APPOINTMENT_ID),
        )

    # ---------------------------------------------------------------------- share
    @app.get("/share", response_class=HTMLResponse)
    def share(request: Request):
        result = state.current()
        if result is None:
            return page(request, "erased.html")
        return page(
            request,
            "share.html",
            summary=result.relative,
            enabled=state.settings.share_enabled,
            granted=result.consent.state is ConsentState.GRANTED,
        )

    @app.get("/share.txt", response_class=PlainTextResponse)
    def share_txt():
        result = state.current()
        if result is None or not state.settings.share_enabled or result.relative is None:
            return PlainTextResponse("Sharing is switched off for this appointment.", status_code=403)
        return PlainTextResponse(result.relative.as_text())

    # ------------------------------------------------------------------- ask / log
    @app.post("/ask", response_class=HTMLResponse)
    def ask(request: Request, question: str = Form(...)):
        # Derive first. Asking before the record has been opened used to search an empty
        # store and answer "nothing covers that", which is a wrong answer, not an empty one.
        state.current()
        utterances = state.store.utterances(APPOINTMENT_ID)
        a = ask_mod.answer(question, utterances, state.bedrock())
        if a.refused:
            state.store.add_refusal(APPOINTMENT_ID, question, a.text)
        return page(request, "ask.html", question=question, answer=a)

    @app.get("/refusals", response_class=HTMLResponse)
    def refusals(request: Request):
        return page(request, "refusals.html", rows=state.store.refusals())

    # ------------------------------------------------------------ history / search
    @app.get("/history", response_class=HTMLResponse)
    def history(request: Request, q: str = ""):
        state.current()  # no-op once erased
        return page(
            request,
            "history.html",
            rows=state.store.appointments(),
            hits=state.store.search(q) if q else [],
            q=q,
        )

    @app.post("/forget")
    def forget(appointment_id: str = Form(...)):
        state.store.forget(appointment_id)
        state.last = None
        state.erased = True
        return RedirectResponse("/history", status_code=303)

    @app.post("/purge")
    def purge():
        state.store.purge_expired()
        return RedirectResponse("/history", status_code=303)

    # ---------------------------------------------------------------- corrections
    @app.post("/correct")
    def correct(target: str = Form(...), note: str = Form(...)):
        if note.strip():
            state.store.add_correction(APPOINTMENT_ID, target, note.strip())
        return RedirectResponse("/#medicines", status_code=303)

    # ------------------------------------------------------------------- settings
    @app.get("/settings", response_class=HTMLResponse)
    def settings_page(request: Request):
        return page(request, "settings.html", saved=False)

    @app.post("/settings", response_class=HTMLResponse)
    async def settings_save(request: Request):
        form = dict(await request.form())  # type: ignore[arg-type]
        state.settings = state.settings.update({k: str(v) for k, v in form.items()})
        state.last = None  # the derived views depend on these, so they are rebuilt
        return page(request, "settings.html", saved=True)

    # ------------------------------------------------------------------- export
    @app.get("/export.txt", response_class=PlainTextResponse)
    def export_txt():
        result = state.current()
        if result is None:
            return PlainTextResponse(
                "This appointment was destroyed at the patient's request. Nothing remains, "
                "including the consent ledger."
            )
        return PlainTextResponse(build_export(state, result))

    return app


def build_export(state: App, result: pipeline.Result) -> str:
    """The sheet you take back to the clinic.

    It leads with the thing the clinic needs to see (what this is not) and ends with the
    list of what to check, because the whole point of the export is to get the clinic's
    record and this one compared while there is still time to fix a difference.
    """
    from .relative import NOT_THE_RECORD

    out = [
        f"SECOND CHAIR: {result.session.label}",
        "",
        NOT_THE_RECORD,
        "",
        "CONSENT",
        f"  {result.consent.state.value.upper()}",
        f'  Announced: "{result.consent.announcement_text}"',
        f'  Answer recorded: "{result.consent.response_text or "none"}"',
        f"  Basis: {result.consent.basis}",
        "",
    ]
    if result.blocked:
        out += ["Nothing was retained from this appointment.", ""]
        return "\n".join(out)
    out.append("WHAT WAS SAID ABOUT MEDICINES")
    corrections = state.store.corrections(APPOINTMENT_ID)
    for m in result.meds:
        out.append(f"  {m.medicine}: {m.kind.value}")
        out.append(f'    Words used: "{m.quote}"')
        for c in corrections.get(m.medicine.lower(), []):
            out.append(f"    CORRECTED BY THE PATIENT: {c['note']}")
    out += ["", "NOT COVERED IN THE ROOM"]
    for q in result.questions:
        if q.coverage.value != "covered":
            out.append(f"  - {q.text}")
    out += [
        "",
        "PLEASE CHECK THESE AGAINST YOUR OWN SUMMARY.",
        NOT_THE_RECORD,
    ]
    return "\n".join(out)


app = create_app()
