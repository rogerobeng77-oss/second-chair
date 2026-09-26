"""Amazon Bedrock, with a deadline and a way to live without it.

Region `us-east-1`, credentials from the environment. Nothing here names an account.

`anthropic.claude-sonnet-5` and `anthropic.claude-opus-5` are listed by both
`ListFoundationModels` and `ListInferenceProfiles` and return AccessDeniedException on
invoke, confirmed against this account. So `FAST_MODELS` and `JUDGEMENT_MODELS`
are preference chains rather than constants: the newest id sits first and the build
upgrades itself the day access lands, without a code change.

Every call has:
  * a wall-clock deadline enforced here, because botocore only gives per-socket timeouts
  * one attempt, no retries, because a judge will not wait for exponential backoff
  * a caller-supplied fallback, so every derived view still renders with the network off

The seam for tests is `invoke`: a callable taking (model_id, body_json) and returning the
raw response string. Tests pass a fake. No AWS credentials are needed to run the suite.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable, Sequence

log = logging.getLogger("second_chair.bedrock")

# Ordered by preference. Verified by invoking each one on this account on 2026-09-22.
# The `-5` names without a date suffix are listed by both AWS listing APIs and are not
# invocable, which is why this is a probe list and not a constant.
FAST_MODELS: Sequence[str] = (
    "us.anthropic.claude-sonnet-5",              # not yet entitled; first in line for when it is
    "us.anthropic.claude-sonnet-4-6",            # verified 2026-09-22
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",  # verified 2026-09-22, ~1.5s
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",   # verified 2026-09-22
)
JUDGEMENT_MODELS: Sequence[str] = (
    "us.anthropic.claude-opus-5",                # not yet entitled
    "us.anthropic.claude-opus-4-5-20251101-v1:0",    # verified 2026-09-22
    "us.anthropic.claude-sonnet-4-6",
)

REGION = os.environ.get("SECOND_CHAIR_REGION", "us-east-1")
DEADLINE_SECONDS = float(os.environ.get("SECOND_CHAIR_DEADLINE", "25"))


class ModelUnavailable(RuntimeError):
    """Bedrock could not be reached, refused us, or took too long."""


@dataclass
class ModelCall:
    """What happened, for the UI. Every derived view shows one of these."""

    model_id: str | None
    used_model: bool
    fallback_reason: str | None = None
    elapsed_ms: int = 0

    @property
    def provenance(self) -> str:
        if self.used_model:
            return f"Written by {self.model_id} on Amazon Bedrock, then checked against the transcript."
        return f"Written without the model. {self.fallback_reason}"


def _boto_invoke(model_id: str, body: str) -> str:
    import boto3  # imported lazily so the suite runs with no AWS packages configured
    from botocore.config import Config

    client = boto3.client(
        "bedrock-runtime",
        region_name=REGION,
        config=Config(connect_timeout=4, read_timeout=25, retries={"max_attempts": 1}),
    )
    resp = client.invoke_model(modelId=model_id, body=body)
    return resp["body"].read().decode("utf-8")


class Bedrock:
    def __init__(
        self,
        invoke: Callable[[str, str], str] = _boto_invoke,
        models: Sequence[str] = FAST_MODELS,
        deadline_seconds: float = DEADLINE_SECONDS,
    ) -> None:
        self._invoke = invoke
        self._models = list(models)
        self._deadline = deadline_seconds
        self._working: str | None = None

    def _run_with_deadline(self, model_id: str, body: str) -> str:
        """botocore has no total-request timeout, so we impose one.

        The worker thread is a daemon: if the call outlives the deadline we abandon it
        rather than block the response. A leaked socket is a better outcome than a demo
        that hangs.
        """
        box: dict[str, object] = {}

        def run() -> None:
            try:
                box["ok"] = self._invoke(model_id, body)
            except BaseException as exc:  # noqa: BLE001 - reported, not swallowed
                box["err"] = exc

        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(self._deadline)
        if t.is_alive():
            raise ModelUnavailable(f"{model_id} did not answer within {self._deadline:g}s")
        if "err" in box:
            raise ModelUnavailable(f"{model_id}: {box['err']}")
        return str(box["ok"])

    def complete(self, system: str, user: str, max_tokens: int = 1400, temperature: float = 0.0) -> tuple[str, ModelCall]:
        """Returns the model's text and a ModelCall describing how it went.

        Raises ModelUnavailable only when every candidate model failed. Callers are
        expected to catch it and fall back.
        """
        import time

        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": max_tokens,
                "temperature": temperature,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        )
        # The cached model first, then the rest of the list. Built once, up front: an
        # earlier version extended the list from inside the loop, which Python quietly
        # ignores, so a cached model that started failing took the whole call down
        # instead of falling back. tests/test_bedrock.py pins it.
        candidates = list(self._models)
        if self._working:
            candidates = [self._working] + [m for m in candidates if m != self._working]
        errors: list[str] = []
        for model_id in candidates:
            started = time.monotonic()
            try:
                raw = self._run_with_deadline(model_id, body)
            except ModelUnavailable as exc:
                errors.append(str(exc))
                if self._working == model_id:
                    self._working = None
                continue
            elapsed = int((time.monotonic() - started) * 1000)
            self._working = model_id
            text = _extract_text(raw)
            return text, ModelCall(model_id=model_id, used_model=True, elapsed_ms=elapsed)
        raise ModelUnavailable("; ".join(errors) or "no candidate models configured")

    def complete_or(self, system: str, user: str, fallback: str, **kw: object) -> tuple[str, ModelCall]:
        try:
            return self.complete(system, user, **kw)  # type: ignore[arg-type]
        except ModelUnavailable as exc:
            log.warning("bedrock unavailable, using the written fallback: %s", exc)
            return fallback, ModelCall(
                model_id=None,
                used_model=False,
                fallback_reason="Bedrock could not be reached, so this is assembled from the transcript by rule.",
            )


def _extract_text(raw: str) -> str:
    payload = json.loads(raw)
    parts = [b.get("text", "") for b in payload.get("content", []) if b.get("type") == "text"]
    return "".join(parts).strip()


def parse_json_block(text: str) -> object:
    """Models wrap JSON in prose and fences however often you ask them not to."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[-1]
        cleaned = cleaned.rsplit("```", 1)[0]
    start = min((i for i in (cleaned.find("["), cleaned.find("{")) if i != -1), default=-1)
    if start == -1:
        raise ValueError("no JSON found in model output")
    end = max(cleaned.rfind("]"), cleaned.rfind("}"))
    return json.loads(cleaned[start : end + 1])
