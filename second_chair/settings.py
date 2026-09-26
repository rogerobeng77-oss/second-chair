"""Settings that change what the product does, not how it looks.

Each one exists because it would change the answer to a question a real user has.

`jurisdiction`. The difference between an all-party and a one-party consent state is
whether an announcement with no recorded response is enough. In a one-party state the
wearer's own consent suffices and an unanswered announcement still permits a transcript;
in an all-party state it does not. Second Chair defaults to the strict reading for every
state, and you have to deliberately relax it.

`retention_days`. How long before the transcript is destroyed. The consent record
outlives it.

`share_enabled`. Whether the relative summary can be produced at all. A person who did
not want their consultation forwarded to a relative should be able to say so once.

`model_tier`. Off, fast, or judgement. Off is a supported configuration, not a broken
one. The medicine rows and the question list fall back to written rules, because those
produce structured facts tied to verbatim quotes. The recap does not fall back at all: it
says no recap was written and shows the record instead, because a paraphrase of a medical
appointment assembled by rule and presented as a summary is worse than no summary.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict

ALL_PARTY_STATES = {
    "CA", "DE", "FL", "IL", "MD", "MA", "MI", "MT", "NV", "NH", "OR", "PA", "WA",
}

JURISDICTION_NOTE = {
    "all": (
        "All-party state. An announcement with no recorded answer is not enough, so "
        "Second Chair keeps nothing until an answer is recorded."
    ),
    "one": (
        "One-party state. The wearer's own consent is sufficient in law. Second Chair "
        "still announces, still records the announcement, and still shows you that it did."
    ),
}


@dataclass
class Settings:
    jurisdiction: str = "CA"
    strict_all_party: bool = True
    retention_days: int = 30
    share_enabled: bool = True
    model_tier: str = "fast"  # off | fast | judgement
    relative_name: str = "your daughter"

    @property
    def is_all_party(self) -> bool:
        return self.strict_all_party or self.jurisdiction.upper() in ALL_PARTY_STATES

    @property
    def jurisdiction_note(self) -> str:
        return JURISDICTION_NOTE["all" if self.is_all_party else "one"]

    def as_dict(self) -> dict:
        d = asdict(self)
        d["is_all_party"] = self.is_all_party
        d["jurisdiction_note"] = self.jurisdiction_note
        return d

    def update(self, form: dict[str, str]) -> "Settings":
        return Settings(
            jurisdiction=(form.get("jurisdiction") or self.jurisdiction).upper()[:2],
            strict_all_party=form.get("strict_all_party") == "on",
            retention_days=_clamp(form.get("retention_days"), self.retention_days, 1, 365),
            share_enabled=form.get("share_enabled") == "on",
            model_tier=form.get("model_tier") if form.get("model_tier") in {"off", "fast", "judgement"} else self.model_tier,
            relative_name=(form.get("relative_name") or self.relative_name).strip()[:60],
        )


def _clamp(raw: str | None, current: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(str(raw))))
    except (TypeError, ValueError):
        return current
