import base64
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests
from pydantic import BaseModel, ValidationError

from src import llm


PNG = b"\x89PNG\r\n\x1a\nimage data"


class Answer(BaseModel):
    found: bool


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setattr(llm, "client", None)
    monkeypatch.setattr(llm, "usage_log", [])


def anthropic_reply(text='{"found": true}', stop_reason="end_turn"):
    return SimpleNamespace(
        stop_reason=stop_reason,
        content=[SimpleNamespace(type="text", text=text)],
        usage=SimpleNamespace(input_tokens=13, output_tokens=5),
    )


def ollama_response(data):
    response = Mock()
    response.json.return_value = data
    return response


def test_disabled_rejects_before_request(monkeypatch):
    monkeypatch.setattr(llm, "config", {"provider": "anthropic", "model": "text", "visual": None})
    factory = Mock(side_effect=AssertionError("request must not start"))
    post = Mock(side_effect=AssertionError("request must not start"))
    monkeypatch.setattr(llm.anthropic, "Anthropic", factory)
    monkeypatch.setattr(llm.requests, "post", post)

    with pytest.raises(ValueError, match="not configured"):
        llm.generate_visual(PNG, "inspect", Answer)
    factory.assert_not_called()
    post.assert_not_called()


@pytest.mark.parametrize("image, message", [
    (b"not PNG", "PNG bytes"),
    ("not bytes", "PNG bytes"),
    (b"\x89PNG\r\n\x1a\n" + b"x" * (4 * 1024 * 1024), "4 MiB"),
], ids=["invalid-signature", "non-bytes", "too-large"])
def test_image_validation_precedes_request(monkeypatch, image, message):
    monkeypatch.setattr(llm, "config", {"visual": {"provider": "ollama", "model": "vision"}})
    post = Mock(side_effect=AssertionError("request must not start"))
    monkeypatch.setattr(llm.requests, "post", post)
    with pytest.raises(ValueError, match=message):
        llm.generate_visual(image, "inspect", Answer)
    post.assert_not_called()


def test_anthropic_uses_independent_visual_model_and_logs_metadata(monkeypatch):
    monkeypatch.setattr(llm, "config", {"provider": "ollama", "model": "text-model",
                                         "visual": {"provider": "anthropic", "model": "vision-model"}})
    client = Mock()
    client.messages.create.return_value = anthropic_reply()
    factory = Mock(return_value=client)
    monkeypatch.setattr(llm.anthropic, "Anthropic", factory)
    post = Mock(side_effect=AssertionError("must not use Ollama"))
    monkeypatch.setattr(llm.requests, "post", post)

    result, metadata = llm.generate_visual(PNG, "inspect", Answer, system="JSON only")

    assert result.found is True
    factory.assert_called_once()
    kwargs = client.messages.create.call_args.kwargs
    assert kwargs["model"] == "vision-model"
    assert kwargs["max_tokens"] == 8000
    assert kwargs["system"] == "JSON only"
    content = kwargs["messages"][0]["content"]
    assert content[0]["source"]["media_type"] == "image/png"
    assert base64.b64decode(content[0]["source"]["data"]) == PNG
    assert content[1] == {"type": "text", "text": "inspect"}
    assert metadata["provider"] == "anthropic"
    assert metadata["model"] == "vision-model"
    assert metadata["input_tokens"] == 13
    assert metadata["output_tokens"] == 5
    assert "timestamp" in metadata
    assert llm.usage_log == [{"agent": "visual", **metadata}]
    assert "image" not in json.dumps(llm.usage_log)
    post.assert_not_called()


@pytest.mark.parametrize("reply, error", [
    (anthropic_reply(stop_reason="max_tokens"), "truncated"),
    (anthropic_reply(text=""), "no text"),
])
def test_anthropic_rejects_incomplete_reply(monkeypatch, reply, error):
    monkeypatch.setattr(llm, "config", {"visual": {"provider": "anthropic", "model": "vision"}})
    client = Mock()
    client.messages.create.return_value = reply
    monkeypatch.setattr(llm.anthropic, "Anthropic", Mock(return_value=client))
    with pytest.raises(ValueError, match=error):
        llm.generate_visual(PNG, "inspect", Answer)
    assert llm.usage_log == []


def test_ollama_checks_capability_and_sends_schema(monkeypatch):
    monkeypatch.setattr(llm, "config", {"provider": "anthropic", "model": "text-model",
                                         "visual": {"provider": "ollama", "model": "vision-model"}})
    monkeypatch.setattr(llm.anthropic, "Anthropic", Mock(side_effect=AssertionError("no fallback")))
    show = ollama_response({"capabilities": ["completion", "vision"]})
    chat = ollama_response({"message": {"content": '{"found": true}'},
                            "prompt_eval_count": 8, "eval_count": 3})
    post = Mock(side_effect=[show, chat])
    monkeypatch.setattr(llm.requests, "post", post)

    result, metadata = llm.generate_visual(PNG, "inspect", Answer)

    assert result.found is True
    assert post.call_args_list[0].args[0].endswith("/api/show")
    assert post.call_args_list[0].kwargs["json"] == {"model": "vision-model"}
    assert post.call_args_list[1].args[0].endswith("/api/chat")
    payload = post.call_args_list[1].kwargs["json"]
    assert payload["model"] == "vision-model"
    assert payload["stream"] is False
    assert payload["format"] == Answer.model_json_schema()
    assert base64.b64decode(payload["messages"][1]["images"][0]) == PNG
    assert metadata["input_tokens"] == 8
    assert metadata["output_tokens"] == 3
    show.raise_for_status.assert_called_once()
    chat.raise_for_status.assert_called_once()


def test_nonvision_model_rejected_before_chat(monkeypatch):
    monkeypatch.setattr(llm, "config", {"visual": {"provider": "ollama", "model": "plain"}})
    post = Mock(return_value=ollama_response({"capabilities": ["completion"]}))
    monkeypatch.setattr(llm.requests, "post", post)
    with pytest.raises(ValueError, match="does not support vision"):
        llm.generate_visual(PNG, "inspect", Answer)
    post.assert_called_once()


@pytest.mark.parametrize("phase, response, error", [
    ("show", {"error": "model missing"}, "model inspection failed: model missing"),
    ("chat", {"error": "backend failed"}, "visual request failed: backend failed"),
    ("chat", {"done_reason": "length", "message": {"content": '{"found": true}'}}, "truncated"),
    ("chat", {"message": {}}, "no text"),
    ("chat", {"message": None}, "no text"),
])
def test_ollama_errors_are_explicit_and_do_not_fallback(monkeypatch, phase, response, error):
    monkeypatch.setattr(llm, "config", {"visual": {"provider": "ollama", "model": "vision"}})
    monkeypatch.setattr(llm.anthropic, "Anthropic", Mock(side_effect=AssertionError("no fallback")))
    responses = ([ollama_response(response)] if phase == "show" else
                 [ollama_response({"capabilities": ["vision"]}), ollama_response(response)])
    monkeypatch.setattr(llm.requests, "post", Mock(side_effect=responses))
    with pytest.raises(ValueError, match=error):
        llm.generate_visual(PNG, "inspect", Answer)
    assert llm.usage_log == []


@pytest.mark.parametrize("phase", ["show", "chat"])
def test_ollama_http_errors_propagate(monkeypatch, phase):
    monkeypatch.setattr(llm, "config", {"visual": {"provider": "ollama", "model": "vision"}})
    failed = ollama_response({})
    failed.raise_for_status.side_effect = requests.HTTPError("unavailable")
    responses = ([failed] if phase == "show" else
                 [ollama_response({"capabilities": ["vision"]}), failed])
    monkeypatch.setattr(llm.requests, "post", Mock(side_effect=responses))
    with pytest.raises(requests.HTTPError, match="unavailable"):
        llm.generate_visual(PNG, "inspect", Answer)
    failed.json.assert_not_called()
    assert llm.usage_log == []


@pytest.mark.parametrize("text, error", [
    ("prefix {\"found\": true}", json.JSONDecodeError),
    ('{"found": "maybe"}', ValidationError),
])
def test_visual_requires_exact_valid_json_and_schema(monkeypatch, text, error):
    monkeypatch.setattr(llm, "config", {"visual": {"provider": "anthropic", "model": "vision"}})
    client = Mock()
    client.messages.create.return_value = anthropic_reply(text=text)
    monkeypatch.setattr(llm.anthropic, "Anthropic", Mock(return_value=client))
    with pytest.raises(error):
        llm.generate_visual(PNG, "inspect", Answer)
    assert llm.usage_log == []
