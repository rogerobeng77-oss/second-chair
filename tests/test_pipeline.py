"""Whole-appointment behaviour, plus the two claims the product rests on:
consent gates everything, and nothing branches on the speaker label."""

import dataclasses

import pytest

from second_chair import pipeline
from second_chair.bee import BeeClient, MockBeeTransport
from second_chair.consent import ConsentState
from second_chair.questions import Coverage
from second_chair.store import Store


class _Rewritten:
    """Transport that edits one field of the mocked stream, nothing else."""

    def __init__(self, inner, fn):
        self._inner, self._fn = inner, fn

    def get(self, path, **params):
        payload = self._inner.get(path, **params)
        if path == "/v1/conversations/stream":
            return {"events": [self._fn(dict(e)) for e in payload["events"]]}
        return payload

    def post(self, path, body):
        return self._inner.post(path, body)


@pytest.fixture
def run(client, session, week):
    return lambda **kw: pipeline.run(client=client, session=session, questions=week["questions"],
                                     facts=client.facts(), **kw)


class TestTheHappyPath:
    def test_consent_is_granted_and_the_record_is_built(self, run):
        r = run()
        assert r.consent.state is ConsentState.GRANTED
        assert not r.blocked
        assert r.meds and r.questions and r.recap is not None

    def test_the_four_outputs_all_exist(self, run):
        r = run()
        assert r.recap is not None
        assert r.meds
        assert r.questions
        assert r.relative is not None

    def test_the_questions_that_were_not_asked_become_todos(self, run):
        r = run()
        missed = [q for q in r.questions if q.coverage is not Coverage.COVERED]
        assert len(r.todos) == len(missed)
        assert all(t.startswith("Ask at the next appointment:") for t in r.todos)

    def test_the_week_collides_with_the_room(self, run):
        """The one thing a recorder on the table cannot do."""
        r = run()
        assert r.collisions
        assert any("ibuprofen" in c.from_the_week.lower() for c in r.collisions)

    def test_the_relative_summary_carries_no_transcript(self, run):
        r = run()
        text = r.relative.as_text()
        for u in r.retained:
            if len(u.text) > 60 and u.text not in [l.quote for l in r.relative.lines]:
                assert u.text not in text

    def test_the_relative_summary_says_it_is_not_the_record_twice(self, run):
        text = run().relative.as_text()
        assert text.count("not a medical record") == 2


class TestConsentGatesEverything:
    @pytest.mark.parametrize(
        "answer,state",
        [
            ("No, I'd rather you didn't record this.", ConsentState.REFUSED),
            ("Right, where were we.", ConsentState.UNANSWERED),
        ],
    )
    def test_no_consent_means_no_derived_output_at_all(self, transport, session, week, answer, state):
        def rewrite(e):
            if "lots of people do that now" in e["utterance"]["text"]:
                e["utterance"] = dict(e["utterance"], text=answer)
            return e

        client = BeeClient(_Rewritten(transport, rewrite))
        r = pipeline.run(client=client, session=session, questions=week["questions"], facts=[])
        assert r.consent.state is state
        assert r.blocked
        assert r.retained == []
        assert r.recap is None and r.meds == [] and r.questions == [] and r.relative is None

    def test_the_consent_record_survives_a_refusal(self, transport, session, week):
        def rewrite(e):
            if "lots of people do that now" in e["utterance"]["text"]:
                e["utterance"] = dict(e["utterance"], text="No, please don't record this.")
            return e

        client = BeeClient(_Rewritten(transport, rewrite))
        r = pipeline.run(client=client, session=session, questions=week["questions"], facts=[])
        assert r.consent.announcement_text
        assert r.consent.response_text.startswith("No")
        assert "discarded" in r.consent.basis

    def test_nothing_is_written_to_bee_when_consent_is_refused(self, transport, session, week):
        def rewrite(e):
            if "lots of people do that now" in e["utterance"]["text"]:
                e["utterance"] = dict(e["utterance"], text="No, turn it off please.")
            return e

        client = BeeClient(_Rewritten(transport, rewrite))
        r = pipeline.run(client=client, session=session, questions=week["questions"], facts=[])
        todos, facts = pipeline.push_to_bee(client, r)
        assert todos == [] and facts == []


class TestSpeakerBlindness:
    def test_replacing_every_speaker_label_changes_nothing(self, transport, session, week):
        """Speaker attribution is what the hardware is worst at. If any of this
        depended on it, this test would fail and the product would be a lie."""
        base = pipeline.run(
            client=BeeClient(transport), session=session, questions=week["questions"], facts=[]
        )

        def scramble(e):
            e["utterance"] = dict(e["utterance"], speaker="Speaker 7")
            return e

        other = pipeline.run(
            client=BeeClient(_Rewritten(MockBeeTransport(), scramble)),
            session=dataclasses.replace(session, consent=None),
            questions=week["questions"],
            facts=[],
        )
        assert [u.text for u in base.retained] == [u.text for u in other.retained]
        assert [m.as_dict() for m in base.meds] == [m.as_dict() for m in other.meds]
        assert base.consent.as_dict()["basis"] == other.consent.as_dict()["basis"]

    def test_no_output_names_a_speaker(self, run):
        r = run()
        blob = " ".join(
            [r.recap.text, r.relative.as_text()] + [m.medicine for m in r.meds] + r.todos
        ).lower()
        for phrase in ("the doctor said", "your doctor", "the clinician said", "she said", "he told"):
            assert phrase not in blob


class TestPersistence:
    def test_an_appointment_round_trips(self, run):
        store = Store()
        r = run()
        pipeline.persist(store, "a1", r)
        row = store.appointment("a1")
        assert row is not None
        assert row.consent["state"] == "granted"
        assert len(store.utterances("a1")) == len(r.retained)
        assert store.dropped("a1")

    def test_a_refused_appointment_persists_the_ledger_and_no_words(self, transport, session, week):
        def rewrite(e):
            if "lots of people do that now" in e["utterance"]["text"]:
                e["utterance"] = dict(e["utterance"], text="No, I'd rather you didn't.")
            return e

        r = pipeline.run(client=BeeClient(_Rewritten(transport, rewrite)), session=session,
                         questions=week["questions"], facts=[])
        store = Store()
        pipeline.persist(store, "a1", r)
        assert store.utterances("a1") == []
        assert store.appointment("a1").consent["state"] == "refused"
