import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from second_chair.bee import BeeClient, MockBeeTransport, Utterance  # noqa: E402
from second_chair.consent import Session  # noqa: E402

FIXTURES = ROOT / "fixtures"

SESSION_UUID = "conv_7f21c0"
START = 1790067600000
END = 1790068440000


@pytest.fixture
def transport():
    return MockBeeTransport(FIXTURES)


@pytest.fixture
def client(transport):
    return BeeClient(transport)


@pytest.fixture
def session():
    return Session(conversation_uuid=SESSION_UUID, starts_at_ms=START, ends_at_ms=END, label="Cardiology follow-up")


@pytest.fixture
def week():
    return json.loads((FIXTURES / "week.json").read_text())


@pytest.fixture
def stream(client):
    return list(client.stream())


def utt(text: str, ms: int = START + 1000, uuid: str = SESSION_UUID) -> Utterance:
    return Utterance(conversation_uuid=uuid, created_at_ms=ms, text=text)


def stub_bedrock(payload: str):
    """An `invoke` that returns whatever text you give it, in Bedrock's envelope."""

    def invoke(model_id: str, body: str) -> str:
        return json.dumps({"content": [{"type": "text", "text": payload}]})

    return invoke


def failing_bedrock(exc: Exception | None = None):
    def invoke(model_id: str, body: str) -> str:
        raise exc or ConnectionError("no network")

    return invoke
