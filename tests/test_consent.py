"""The consent gate. If any of these fail the product should not ship."""

import pytest

from conftest import SESSION_UUID, START, utt
from second_chair.consent import (
    ANNOUNCEMENT,
    ConsentState,
    Session,
    classify_response,
    establish,
    match_announcement,
    scope_filter,
)


class TestAnnouncementMatching:
    def test_the_canonical_announcement_matches(self):
        assert match_announcement(ANNOUNCEMENT)

    @pytest.mark.parametrize(
        "text",
        [
            "I'm wearing a device that records what's said, is that alright?",
            "Just so you know, this pendant is recording. Do you mind?",
            "I have a recorder on, it writes down what's said. Is that ok?",
        ],
    )
    def test_it_tolerates_the_wearer_saying_it_their_own_way(self, text):
        assert match_announcement(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Before we start, how have you been?",
            "I'm wearing my hearing aid today.",
            "It's recording now.",  # states it, never asks
            "Is that alright with you?",  # asks, never says what
        ],
    )
    def test_half_an_announcement_is_not_an_announcement(self, text):
        assert not match_announcement(text)


class TestResponseClassification:
    @pytest.mark.parametrize(
        "text",
        ["Yes that's fine.", "Of course, go ahead.", "Sure, no problem.", "That's alright."],
    )
    def test_assent(self, text):
        assert classify_response(text) is ConsentState.GRANTED

    @pytest.mark.parametrize(
        "text",
        [
            "No, I'd rather you didn't.",
            "I'd rather not, please turn it off.",
            "I'm not comfortable with that.",
            "Can you not record this one.",
        ],
    )
    def test_refusal(self, text):
        assert classify_response(text) is ConsentState.REFUSED

    def test_a_sentence_containing_both_is_a_refusal(self):
        assert classify_response("Yes, well, no, I'd rather you didn't.") is ConsentState.REFUSED

    @pytest.mark.parametrize("text", ["Hm.", "Right, where were we.", "So how's the knee."])
    def test_a_non_answer_is_not_consent(self, text):
        assert classify_response(text) is ConsentState.UNANSWERED


class TestScope:
    def test_a_session_cannot_be_open_ended(self):
        with pytest.raises(ValueError):
            Session(conversation_uuid="c", starts_at_ms=100, ends_at_ms=100)

    def test_another_conversation_is_out_of_the_room(self, session):
        kept, dropped = scope_filter(session, [utt("corridor", uuid="conv_other")])
        assert kept == []
        assert "different Bee conversation" in dropped[0].reason

    def test_outside_the_window_is_dropped(self, session):
        kept, dropped = scope_filter(session, [utt("too early", ms=START - 1)])
        assert kept == []
        assert "session window" in dropped[0].reason

    def test_the_fixture_drops_the_waiting_room_and_the_corridor(self, session, stream):
        kept, dropped = scope_filter(session, stream)
        assert len(dropped) == 5
        assert all(u.conversation_uuid == SESSION_UUID for u in kept)
        assert all(session.in_window(u) for u in kept)

    def test_both_scoping_rules_are_exercised_by_the_fixture(self, session, stream):
        """One line is dropped for being in another room, another for being after the
        session closed, and they are different rules with different reasons."""
        _, dropped = scope_filter(session, stream)
        reasons = {d.reason for d in dropped}
        assert any("different Bee conversation" in r for r in reasons)
        assert any("session window" in r for r in reasons)
        late = [d for d in dropped if d.utterance.conversation_uuid == SESSION_UUID]
        assert len(late) == 1
        assert "husband" in late[0].utterance.text
        assert "session window" in late[0].reason


class TestTheGate:
    def test_granted_retains_everything_after_the_answer(self, session, stream):
        in_scope, _ = scope_filter(session, stream)
        record, retained = establish(session, in_scope)
        assert record.state is ConsentState.GRANTED
        assert record.may_retain_transcript
        assert retained
        # the announcement and the answer are not part of the clinical transcript
        assert record.announcement_text not in [u.text for u in retained]
        assert record.response_text not in [u.text for u in retained]

    def test_the_announcement_itself_is_recorded(self, session, stream):
        """RCW 9.73.030(3): 'said announcement shall also be recorded'."""
        in_scope, _ = scope_filter(session, stream)
        record, _ = establish(session, in_scope)
        assert record.announcement_text.strip()
        assert record.announced_at_ms >= session.starts_at_ms
        assert record.as_dict()["announced_at"].endswith("Z")

    def test_a_refusal_retains_nothing_but_the_record(self, session, stream):
        in_scope, _ = scope_filter(session, stream)
        idx = next(i for i, u in enumerate(in_scope) if "device" in u.text)
        in_scope[idx + 1] = utt("No, I'd rather you didn't record this.", ms=in_scope[idx + 1].created_at_ms)
        record, retained = establish(session, in_scope)
        assert record.state is ConsentState.REFUSED
        assert retained == []
        assert not record.may_retain_transcript
        assert record.response_text.startswith("No")

    def test_an_unanswered_announcement_retains_nothing(self, session, stream):
        in_scope, _ = scope_filter(session, stream)
        idx = next(i for i, u in enumerate(in_scope) if "device" in u.text)
        in_scope[idx + 1] = utt("Right, where were we.", ms=in_scope[idx + 1].created_at_ms)
        record, retained = establish(session, in_scope)
        assert record.state is ConsentState.UNANSWERED
        assert retained == []

    def test_no_announcement_at_all_retains_nothing(self, session, stream):
        in_scope, _ = scope_filter(session, stream)
        stripped = [u for u in in_scope if not u.text.lower().startswith("before we start")]
        record, retained = establish(session, stripped)
        assert record.state is ConsentState.PENDING
        assert retained == []
        assert record.announcement_text == ""

    def test_the_record_never_claims_who_answered(self, session, stream):
        in_scope, _ = scope_filter(session, stream)
        record, _ = establish(session, in_scope)
        d = record.as_dict()
        assert d["attribution"] == "not recorded: Bee labels every speaker Unknown"
        blob = " ".join(str(v) for v in d.values()).lower()
        for phrase in ("the doctor said", "the clinician said", "she said", "he said"):
            assert phrase not in blob

    def test_the_basis_cites_the_statutes(self, session, stream):
        in_scope, _ = scope_filter(session, stream)
        record, _ = establish(session, in_scope)
        assert "632(b)" in record.basis
        assert "9.73.030(3)" in record.basis


class TestAmbiguity:
    def test_idioms_containing_no_that_mean_yes(self):
        for text in ["Sure, no problem.", "Not at all, go ahead.", "No objection here, carry on."]:
            assert classify_response(text) is ConsentState.GRANTED

    def test_a_genuinely_ambiguous_answer_falls_to_refusal(self):
        """Deliberate. The cost of being wrong the other way is a lawsuit."""
        assert classify_response("No, that's fine.") is ConsentState.REFUSED
