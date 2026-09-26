"""Retention, corrections, the refusal log and search."""

from datetime import datetime, timedelta, timezone

from second_chair.store import Store

CONSENT = {"state": "granted", "announcement_text": "announced", "response_text": "yes"}


def seed(store: Store, appt="a1", days=30):
    store.save_appointment(
        appointment_id=appt,
        label="Cardiology follow-up",
        held_at="22 September 2026",
        consent=CONSENT,
        utterances=[(0, "bring the bisoprolol down to two point five", "conv_1", 1), (1, "six weeks", "conv_1", 2)],
        dropped=[("corridor chatter", "a different Bee conversation")],
        retention_days=days,
    )


class TestRetention:
    def test_a_fresh_appointment_is_not_purged(self):
        s = Store()
        seed(s)
        assert s.purge_expired() == []
        assert len(s.utterances("a1")) == 2

    def test_an_expired_appointment_loses_its_words(self):
        s = Store()
        seed(s, days=1)
        later = datetime.now(timezone.utc) + timedelta(days=2)
        assert s.purge_expired(now=later) == ["a1"]
        assert s.utterances("a1") == []
        assert s.dropped("a1") == []

    def test_but_keeps_the_consent_record(self):
        """The ledger is the proof that the destruction happened."""
        s = Store()
        seed(s, days=1)
        s.purge_expired(now=datetime.now(timezone.utc) + timedelta(days=2))
        row = s.appointment("a1")
        assert row.consent["state"] == "granted"
        assert row.transcript_purged is True

    def test_purging_twice_is_a_no_op(self):
        s = Store()
        seed(s, days=1)
        later = datetime.now(timezone.utc) + timedelta(days=2)
        s.purge_expired(now=later)
        assert s.purge_expired(now=later) == []

    def test_days_left_counts_down(self):
        s = Store()
        seed(s, days=10)
        assert 8 <= s.appointment("a1").days_left <= 10

    def test_forget_removes_everything_including_the_ledger(self):
        s = Store()
        seed(s)
        s.add_refusal("a1", "should I stop it?", "no")
        s.forget("a1")
        assert s.appointment("a1") is None
        assert s.utterances("a1") == []
        assert s.refusals() == []


class TestCorrections:
    def test_a_correction_is_stored_against_its_row(self):
        s = Store()
        seed(s)
        s.add_correction("a1", "bisoprolol", "the printed summary says from Monday")
        assert s.corrections("a1")["bisoprolol"][0]["note"].startswith("the printed")

    def test_several_corrections_stack_in_order(self):
        s = Store()
        seed(s)
        s.add_correction("a1", "bisoprolol", "first")
        s.add_correction("a1", "bisoprolol", "second")
        assert [c["note"] for c in s.corrections("a1")["bisoprolol"]] == ["first", "second"]


class TestRefusalLog:
    def test_refusals_are_kept_newest_first(self):
        s = Store()
        s.add_refusal("a1", "should I skip a dose?", "refused")
        s.add_refusal("a1", "is this serious?", "refused")
        rows = s.refusals()
        assert rows[0]["question"] == "is this serious?"
        assert len(rows) == 2

    def test_the_log_outlives_the_transcript(self):
        s = Store()
        seed(s, days=1)
        s.add_refusal("a1", "should I stop?", "refused")
        s.purge_expired(now=datetime.now(timezone.utc) + timedelta(days=2))
        assert len(s.refusals()) == 1


class TestSearch:
    def test_it_finds_a_line_across_appointments(self):
        s = Store()
        seed(s)
        hits = s.search("bisoprolol")
        assert len(hits) == 1
        assert hits[0]["label"] == "Cardiology follow-up"

    def test_a_purged_transcript_is_no_longer_searchable(self):
        s = Store()
        seed(s, days=1)
        s.purge_expired(now=datetime.now(timezone.utc) + timedelta(days=2))
        assert s.search("bisoprolol") == []

    def test_a_one_character_query_is_refused(self):
        s = Store()
        seed(s)
        assert s.search("b") == []

    def test_a_wildcard_is_not_a_wildcard(self):
        """LIKE metacharacters must not turn a search box into a table dump."""
        s = Store()
        seed(s)
        assert s.search("%%") == []
