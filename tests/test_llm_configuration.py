from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import requests

from src import llm
from src.contracts import SlideGrade


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    monkeypatch.setattr(llm, "client", None)
    monkeypatch.setattr(llm, "usage_log", [])


def test_ollama_never_initializes_anthropic(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(llm, "config", {"provider": "ollama", "model": "local-model"})
    anthropic_client = Mock(side_effect=AssertionError("Must not initialize Anthropic"))
    monkeypatch.setattr(llm.anthropic, "Anthropic", anthropic_client)
    response = Mock()
    response.json.return_value = {
        "message": {"content": '{"passed": true}'},
        "prompt_eval_count": 12, "eval_count": 4,
    }
    post = Mock(return_value=response)
    monkeypatch.setattr(llm.requests, "post", post)

    assert llm.generate("Grade", SlideGrade).passed
    anthropic_client.assert_not_called()
    response.raise_for_status.assert_called_once()
    assert post.call_args.kwargs["json"]["model"] == "local-model"
    assert llm.usage_log[0]["input_tokens"] == 12


def test_anthropic_initializes_only_on_request(monkeypatch):
    monkeypatch.setattr(llm, "config", {"provider": "anthropic", "model": "chosen-model"})
    client = Mock()
    client.messages.create.return_value = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"passed": true}')],
        usage=SimpleNamespace(input_tokens=10, output_tokens=3))
    factory = Mock(return_value=client)
    monkeypatch.setattr(llm.anthropic, "Anthropic", factory)
    factory.assert_not_called()
    assert llm.generate("Grade", SlideGrade).passed
    assert llm.generate("Grade again", SlideGrade).passed
    factory.assert_called_once()
    assert client.messages.create.call_args.kwargs["model"] == "chosen-model"


@pytest.mark.parametrize("error", [requests.ConnectionError("Unavailable"),
                                      requests.HTTPError("Model not found")])
def test_ollama_failure_propagates_without_fallback(monkeypatch, error):
    monkeypatch.setattr(llm, "config", {"provider": "ollama", "model": "local-model"})
    fallback = Mock(side_effect=AssertionError("Must not fall back"))
    monkeypatch.setattr(llm, "_generate_anthropic", fallback)
    response = Mock()
    response.raise_for_status.side_effect = error
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=response))
    with pytest.raises(type(error)):
        llm.generate("Grade", SlideGrade)
    response.json.assert_not_called()
    fallback.assert_not_called()
    assert llm.usage_log == []


def test_ollama_error_response_is_explicit(monkeypatch):
    monkeypatch.setattr(llm, "config", {"provider": "ollama", "model": "missing"})
    response = Mock()
    response.json.return_value = {"error": "model unavailable"}
    monkeypatch.setattr(llm.requests, "post", Mock(return_value=response))
    with pytest.raises(ValueError, match="Ollama request failed: model unavailable"):
        llm.generate("Grade", SlideGrade)
