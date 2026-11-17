"""Unit tests for the Bedrock / Nova Lite provider. No live AWS calls."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from chat import llm


def test_bedrock_messages_splits_system():
    conv, system = llm.bedrock_messages(
        [
            {"role": "system", "content": "You are a helper."},
            {"role": "user", "content": "What is EQIP?"},
        ]
    )
    assert system == [{"text": "You are a helper."}]
    assert conv == [{"role": "user", "content": [{"text": "What is EQIP?"}]}]


def test_bedrock_creds_requires_both(monkeypatch):
    monkeypatch.delenv("AWS_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY", raising=False)
    assert llm._bedrock_creds() is False
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    assert llm._bedrock_creds() is False
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    assert llm._bedrock_creds() is True


def test_bedrock_chat_records_usage_and_returns_text(monkeypatch, tmp_path):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")
    monkeypatch.setattr(llm, "_usage_path", lambda: tmp_path / "usage.jsonl")
    llm._usage_totals.clear()
    llm._bedrock_endpoint.clear()

    fake_resp = {
        "output": {"message": {"content": [{"text": "pong"}]}},
        "usage": {"inputTokens": 11, "outputTokens": 2, "totalTokens": 13},
    }
    client = MagicMock()
    client.converse.return_value = fake_resp

    with patch.object(llm, "_bedrock_client", return_value=client):
        text = llm._bedrock_chat(
            "us.amazon.nova-lite-v1:0",
            [{"role": "user", "content": "Reply with the single word pong."}],
            role="probe",
            regions=["us-east-1"],
        )
    assert text == "pong"
    client.converse.assert_called_once()
    kwargs = client.converse.call_args.kwargs
    assert kwargs["modelId"] == "us.amazon.nova-lite-v1:0"
    assert kwargs["messages"][0]["role"] == "user"
    lines = (tmp_path / "usage.jsonl").read_text().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["provider"] == "bedrock"
    assert rec["input_tokens"] == 11
    assert rec["output_tokens"] == 2
    assert rec["role"] == "probe"
    summary = llm.usage_summary()
    assert summary["nova_lite"]["calls"] == 1
    assert summary["nova_lite"]["input_tokens"] == 11
    assert summary["nova_lite"]["usd_estimate"] == pytest.approx(
        llm.estimate_nova_cost(11, 2)
    )


def test_bedrock_throttling_surfaces_code(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIATEST")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret")

    class ClientError(Exception):
        def __init__(self):
            self.response = {"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}}

    import botocore.exceptions as be

    client = MagicMock()
    client.converse.side_effect = be.ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Too many requests"}},
        "Converse",
    )
    with patch.object(llm, "_bedrock_client", return_value=client):
        with pytest.raises(RuntimeError) as exc:
            llm._bedrock_chat(
                "us.amazon.nova-lite-v1:0",
                [{"role": "user", "content": "hi"}],
                role="generator",
                regions=["us-east-1"],
            )
    assert "ThrottlingException" in str(exc.value)


def test_sanitize_strips_aws_access_key():
    cleaned = llm._sanitize_err("boom AKIAIOSFODNN7EXAMPLE and sk-abcdefg1234567890")
    assert "AKIA" not in cleaned
    assert "sk-abcdefg" not in cleaned
    assert "[redacted]" in cleaned


def test_nova_cost_constants():
    # $0.06 / $0.24 per 1M
    assert llm.estimate_nova_cost(1_000_000, 1_000_000) == pytest.approx(0.30)
