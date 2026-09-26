"""The routes, driven with Bedrock switched off so the suite needs no network."""

import json

import pytest
from fastapi.testclient import TestClient

from second_chair.web import APPOINTMENT_ID, create_app


@pytest.fixture
def client():
    app = create_app()
    app.state.sc.settings.model_tier = "off"
    return TestClient(app)


class TestPages:
    @pytest.mark.parametrize(
        "path",
        ["/", "/replay", "/consent", "/share", "/history", "/refusals", "/settings",
         "/export.txt", "/share.txt", "/api/replay"],
    )
    def test_every_page_renders(self, client, path):
        assert client.get(path).status_code == 200

    def test_the_record_names_the_medicines(self, client):
        body = client.get("/").text
        for name in ("bisoprolol", "apixaban", "amlodipine", "atorvastatin"):
            assert name in body

    def test_the_seal_shows_the_recorded_answer(self, client):
        assert "lots of people do that now" in client.get("/").text

    def test_every_page_says_the_data_is_mocked(self, client):
        for path in ("/", "/consent", "/share", "/history"):
            assert "Mocked data" in client.get(path).text

    def test_every_page_says_it_is_not_a_medical_record(self, client):
        for path in ("/", "/consent", "/share"):
            assert "not a medical record" in client.get(path).text

    def test_the_consent_page_cites_both_statutes(self, client):
        body = client.get("/consent").text
        assert "632(b)" in body
        assert "9.73.030(3)" in body

    def test_the_consent_page_admits_the_hipaa_problem(self, client):
        assert "164.508(a)(1)" in client.get("/consent").text

    def test_the_consent_page_admits_rule_three_is_weaker_than_it_sounds(self, client):
        assert "cannot do voice recognition" in client.get("/consent").text


class TestReplay:
    def test_the_stream_is_labelled_with_what_the_gate_did(self, client):
        data = client.get("/api/replay").json()
        kinds = {e["status"] for e in data["events"]}
        assert {"dropped", "announcement", "response", "kept"} <= kinds

    def test_the_waiting_room_is_dropped_before_the_announcement(self, client):
        events = client.get("/api/replay").json()["events"]
        first_kept = next(i for i, e in enumerate(events) if e["status"] == "announcement")
        assert all(e["status"] == "dropped" for e in events[:first_kept])

    def test_every_speaker_is_unknown(self, client):
        """Bee's real label. If the fixture ever says otherwise, the demo is a lie."""
        assert {e["speaker"] for e in client.get("/api/replay").json()["events"]} == {"Unknown"}

    def test_a_refusal_can_be_replayed(self, client):
        data = client.get("/api/replay", params={"answer": "No, I'd rather you didn't."}).json()
        response = next(e for e in data["events"] if e["status"] == "response")
        assert response["text"].startswith("No")


class TestRefusalFlow:
    def test_an_advice_question_is_refused_and_logged(self, client):
        r = client.post("/ask", data={"question": "Should I stop taking the amlodipine?"})
        assert "will not answer that" in r.text
        assert "Should I stop taking the amlodipine?" in client.get("/refusals").text

    def test_a_record_question_is_answered_with_quotes(self, client):
        r = client.post("/ask", data={"question": "What was said about the blood test?"})
        assert "will not answer that" not in r.text
        assert "six weeks" in r.text


class TestCorrections:
    def test_a_correction_appears_on_the_record_and_in_the_export(self, client):
        client.post("/correct", data={"target": "bisoprolol", "note": "the printed summary says Monday"})
        assert "the printed summary says Monday" in client.get("/").text
        assert "CORRECTED BY THE PATIENT" in client.get("/export.txt").text


class TestSettings:
    def test_switching_the_model_off_still_renders_everything(self, client):
        client.post("/settings", data={"model_tier": "off", "jurisdiction": "CA",
                                       "retention_days": "30", "share_enabled": "on",
                                       "relative_name": "your daughter"})
        body = client.get("/").text
        assert "Written without the model" in body
        assert "bisoprolol" in body

    def test_turning_sharing_off_closes_the_share_page(self, client):
        client.post("/settings", data={"model_tier": "off", "jurisdiction": "CA",
                                       "retention_days": "30", "relative_name": "d"})
        assert "Sharing is switched off" in client.get("/share").text
        assert client.get("/share.txt").status_code == 403

    def test_retention_is_clamped(self, client):
        client.post("/settings", data={"model_tier": "off", "jurisdiction": "CA",
                                       "retention_days": "9999", "relative_name": "d"})
        assert client.app.state.sc.settings.retention_days == 365


class TestHistory:
    def test_search_finds_a_line(self, client):
        client.get("/")
        assert "bisoprolol" in client.get("/history", params={"q": "bisoprolol"}).text

    def test_search_with_no_hits_says_so(self, client):
        client.get("/")
        assert "No line in any held transcript" in client.get("/history", params={"q": "helicopter"}).text

    def test_forget_removes_the_appointment(self, client):
        client.get("/")
        client.post("/forget", data={"appointment_id": APPOINTMENT_ID})
        assert client.app.state.sc.store.appointment(APPOINTMENT_ID) is None


class TestRefusedConsentEndToEnd:
    def test_the_record_page_shows_nothing_was_kept(self, client):
        client.post("/rerun", data={"answer": "No, I'd rather you didn't record this."})
        body = client.get("/").text
        assert "Nothing was kept from this appointment" in body
        assert "bisoprolol" not in body

    def test_the_export_holds_the_ledger_and_no_transcript(self, client):
        client.post("/rerun", data={"answer": "No, please don't record this."})
        text = client.get("/export.txt").text
        assert "REFUSED" in text
        assert "Nothing was retained" in text
        assert "bisoprolol" not in text

    def test_the_share_page_refuses(self, client):
        client.post("/rerun", data={"answer": "No, turn it off."})
        assert "Nothing to share" in client.get("/share").text


class TestNoModelIsHonest:
    """With Bedrock unreachable the app must not present a rule-built paraphrase as a
    recap. A half-written summary in a clinical setting is worse than saying no."""

    def test_the_page_says_no_recap_was_written(self, client):
        body = client.get("/").text
        assert "No recap was written" in body
        assert "will not assemble a summary" in body

    def test_the_lines_shown_are_verbatim_transcript(self, client):
        from second_chair.web import APPOINTMENT_ID

        client.get("/")
        sc = client.app.state.sc
        said = {t for _, t in sc.store.utterances(APPOINTMENT_ID)}
        for sentence in sc.last.recap.sentences:
            inner = sentence.split('"')[1] if '"' in sentence else sentence
            assert inner in said

    def test_the_recap_is_not_labelled_as_a_recap(self, client):
        assert "The record, in the words that were used" in client.get("/").text


class TestBeeFactsAreGuardedLikeEverythingElse:
    """A Bee fact is another model's prose and it lands on the patient's page. It was the
    one channel into this app with nothing on it."""

    def _fact(self, **over):
        from second_chair.bee import Fact

        base = dict(
            id="f1",
            text="Ada has been taking ibuprofen most days for her knee.",
            created_at="2026-09-20T17:31:00Z",
            source_quote="I've been taking the ibuprofen most days",
        )
        base.update(over)
        return Fact(**base)

    def test_a_fact_that_gives_advice_never_reaches_the_page(self):
        from second_chair.collisions import find

        bad = self._fact(text="Stop the ibuprofen before the operation.")
        assert find([bad], [], [], []) == []

    def test_a_fact_that_names_a_speaker_never_reaches_the_page(self):
        from second_chair.collisions import find

        bad = self._fact(text="Her doctor said the ibuprofen was the problem.")
        assert find([bad], [], [], []) == []

    def test_a_fact_with_no_source_quote_is_dropped_rather_than_quoted(self):
        """Bee's own `/v1/facts` has no provenance field, so the fallback would have put
        a generated sentence on screen inside quotation marks."""
        from second_chair.collisions import find

        assert find([self._fact(source_quote=None)], [], [], []) == []

    def test_an_ordinary_fact_still_gets_through(self):
        from second_chair.collisions import find
        from second_chair.questions import Coverage, QuestionOutcome

        q = QuestionOutcome("q4", "Is it alright to keep taking the ibuprofen for my knee?",
                            True, Coverage.NOT_COVERED, None, None)
        assert find([self._fact()], [q], [], [])
