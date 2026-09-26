"""The replay page and the record page have to be describing the same room.

The README sends a judge to `/replay`, tells them to refuse consent, and then
sends them to `/`. Following it exactly, `/replay` showed **CONSENT REFUSED —
"Everything captured in this room was discarded"** and `/` then showed
**CONSENT RECORDED**, the granted answer quoted, and the whole transcript.

`GET /api/replay` builds its view from an `_OverriddenTransport` and never
touches `state`: it is a read-only visualisation. The only thing that changed
the stored record was `POST /rerun`, and the only two forms that posted to it
both sent `answer=""` — back to granted. No control anywhere in the UI could
put the record into the refused state; reaching it at all meant posting by
hand.
"""

import re

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from second_chair.web import create_app

ROOT = Path(__file__).resolve().parent.parent
REFUSAL = "No, I'd rather you didn't record this."
GRANTED_QUOTE = "lots of people do that now"


@pytest.fixture
def client():
    app = create_app()
    app.state.sc.settings.model_tier = "off"
    return TestClient(app)


def test_running_the_refused_branch_leaves_the_record_refused(client):
    response = client.post("/api/rerun", data={"answer": REFUSAL})

    assert response.status_code == 200
    assert response.json() == {"granted": False}

    record = client.get("/").text
    assert "Consent refused" in record
    assert "Nothing was kept from this appointment." in record
    assert GRANTED_QUOTE not in record


def test_running_it_again_with_the_recorded_answer_brings_the_record_back(client):
    client.post("/api/rerun", data={"answer": REFUSAL})

    assert client.post("/api/rerun", data={"answer": ""}).json() == {"granted": True}

    record = client.get("/").text
    assert "Consent recorded" in record
    assert GRANTED_QUOTE in record


def test_an_unanswered_announcement_is_not_consent_either(client):
    assert client.post(
        "/api/rerun", data={"answer": "Hm. Right, where were we."}
    ).json() == {"granted": False}


def test_the_read_only_replay_view_still_changes_nothing_by_itself(client):
    """`/api/replay` is a visualisation and stays one. The fix is that the page
    presses `/api/rerun` as well, not that the read started writing."""
    before = client.get("/").text

    client.get("/api/replay", params={"answer": REFUSAL})

    assert client.get("/").text == before


def test_every_answer_the_replay_dropdown_offers_can_be_applied(client):
    page = (ROOT / "second_chair" / "templates" / "replay.html").read_text()
    options = re.findall(r'<option value="([^"]*)"', page)

    assert len(options) == 3
    for answer in options:
        assert client.post("/api/rerun", data={"answer": answer}).status_code == 200


def test_the_replay_page_presses_it_before_it_offers_a_link_to_the_record():
    page = (ROOT / "second_chair" / "templates" / "replay.html").read_text()
    script = page[page.index("async function run("):]

    assert "'/api/rerun'" in script
    assert "await applied;" in script
    assert script.index("await applied;") < script.index('<a href="/">')


def test_the_stream_does_not_wait_on_the_rebuild_to_start():
    """The granted branch re-ingests through Bedrock. Awaiting it before the
    first utterance would leave the page blank for several seconds."""
    page = (ROOT / "second_chair" / "templates" / "replay.html").read_text()
    script = page[page.index("async function run("):]
    started = script.index("const applied = fetch('/api/rerun'")

    assert "await fetch('/api/rerun'" not in script
    assert started < script.index("await fetch('/api/replay")


def test_the_empty_stream_says_what_it_is_waiting_for():
    """It is the first screen the README names, and it opened as a 400px grey
    box with nothing written in it."""
    page = (ROOT / "second_chair" / "templates" / "replay.html").read_text()
    stage = page[page.index('id="stage"'):]

    assert "Run the appointment" in stage[: stage.index("</div>")]


def test_the_readme_no_longer_sends_a_judge_from_one_state_into_the_other():
    readme = (ROOT / "README.md").read_text()

    assert "Running it there runs it for real" in readme
    assert "run it\nonce more" in readme
