"""The model client: probing, deadlines, and living without it.

No AWS credentials are needed. `Bedrock` takes an injectable `invoke`, so every path
including the timeout is exercised with a fake.
"""

import json
import time

import pytest

from second_chair.bedrock import Bedrock, ModelUnavailable, parse_json_block
from conftest import failing_bedrock, stub_bedrock


def envelope(text: str) -> str:
    return json.dumps({"content": [{"type": "text", "text": text}]})


class TestProbing:
    def test_it_falls_through_to_the_model_that_answers(self):
        """Sonnet 5 and Opus 5 are listed on this account and refuse on invoke.
        Friction log item 3. This is why the client probes instead of hard-coding."""
        tried = []

        def invoke(model_id, body):
            tried.append(model_id)
            if model_id != "model-c":
                raise RuntimeError("AccessDeniedException: not available for this account")
            return envelope("ok")

        b = Bedrock(invoke=invoke, models=["model-a", "model-b", "model-c"])
        text, call = b.complete("sys", "user")
        assert text == "ok"
        assert call.model_id == "model-c"
        assert tried == ["model-a", "model-b", "model-c"]

    def test_the_working_model_is_cached(self):
        calls = []

        def invoke(model_id, body):
            calls.append(model_id)
            return envelope("ok")

        b = Bedrock(invoke=invoke, models=["model-a", "model-b"])
        b.complete("s", "u")
        b.complete("s", "u")
        assert calls == ["model-a", "model-a"]

    def test_a_cached_model_that_starts_failing_falls_back(self):
        state = {"n": 0}

        def invoke(model_id, body):
            state["n"] += 1
            if state["n"] == 1:
                return envelope("first")
            if model_id == "model-a":
                raise RuntimeError("gone")
            return envelope("second")

        b = Bedrock(invoke=invoke, models=["model-a", "model-b"])
        b.complete("s", "u")
        text, call = b.complete("s", "u")
        assert text == "second"
        assert call.model_id == "model-b"

    def test_every_model_failing_raises(self):
        b = Bedrock(invoke=failing_bedrock(), models=["a", "b"])
        with pytest.raises(ModelUnavailable):
            b.complete("s", "u")


class TestDeadline:
    def test_a_slow_model_is_abandoned(self):
        """botocore has no total-request timeout, so this guard is ours.
        Friction log item 5."""

        def slow(model_id, body):
            time.sleep(2)
            return envelope("too late")

        b = Bedrock(invoke=slow, models=["a"], deadline_seconds=0.15)
        started = time.monotonic()
        with pytest.raises(ModelUnavailable) as e:
            b.complete("s", "u")
        assert time.monotonic() - started < 1.0
        assert "did not answer within" in str(e.value)

    def test_a_slow_model_falls_back_rather_than_hanging(self):
        def slow(model_id, body):
            time.sleep(2)
            return envelope("too late")

        b = Bedrock(invoke=slow, models=["a"], deadline_seconds=0.15)
        text, call = b.complete_or("s", "u", fallback="the written version")
        assert text == "the written version"
        assert call.used_model is False


class TestProvenance:
    def test_a_real_call_names_the_model(self):
        b = Bedrock(invoke=stub_bedrock("hello"), models=["us.anthropic.claude-sonnet-4-6"])
        _, call = b.complete("s", "u")
        assert "us.anthropic.claude-sonnet-4-6" in call.provenance
        assert "Amazon Bedrock" in call.provenance

    def test_a_fallback_says_so(self):
        b = Bedrock(invoke=failing_bedrock(), models=["a"])
        _, call = b.complete_or("s", "u", fallback="x")
        assert "without the model" in call.provenance


class TestJsonParsing:
    def test_a_fenced_block_is_recovered(self):
        assert parse_json_block('```json\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_prose_around_the_json_is_ignored(self):
        assert parse_json_block('Here you go:\n[{"a": 1}]\nHope that helps.') == [{"a": 1}]

    def test_no_json_raises(self):
        with pytest.raises(ValueError):
            parse_json_block("I would rather not.")


class TestNoAccountIdShips:
    def test_the_module_names_no_aws_account(self):
        import inspect

        from second_chair import bedrock

        assert not __import__("re").search(r"\b\d{12}\b", inspect.getsource(bedrock))

    def test_the_preference_chain_leads_with_the_newest_id(self):
        from second_chair.bedrock import FAST_MODELS, JUDGEMENT_MODELS

        assert FAST_MODELS[0] == "us.anthropic.claude-sonnet-5"
        assert JUDGEMENT_MODELS[0] == "us.anthropic.claude-opus-5"
        # and the verified ones are behind it, so the app works today
        assert "us.anthropic.claude-sonnet-4-5-20250929-v1:0" in FAST_MODELS
