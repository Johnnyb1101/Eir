import base64
import json
from unittest.mock import Mock

import pytest
import requests
from pydantic import BaseModel

from src import llm
from src.contracts import Deck


class Answer(BaseModel):
    found: bool


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-test-key")
    monkeypatch.setattr(llm, "config", {"provider": "openai", "model": "gpt-6.2-sol"})
    monkeypatch.setattr(llm, "usage_log", [])
    reply = Mock(status_code=200)
    reply.json.return_value = response()
    post = Mock(return_value=reply)
    monkeypatch.setattr(llm.requests, "post", post)
    return post, reply


def response(text='{"found":true}'):
    return {"status": "completed", "output": [
        {"type": "reasoning", "summary": []},
        {"type": "message", "content": [{"type": "output_text", "text": text}]},
    ], "usage": {"input_tokens": 12, "output_tokens": 4, "input_tokens_details": {"cached_tokens": 8}}}


def test_text_contract_and_request_hygiene(transport):
    post, reply = transport
    reply.json.return_value = response('```json\n{"found":true}\n```')
    assert llm.generate("private source", Answer, system="private system").found
    args, kwargs = post.call_args
    assert args == ("https://api.openai.com/v1/responses",)
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"] == (10, 180)
    assert kwargs["headers"] == {"Authorization": "Bearer secret-test-key"}
    payload = kwargs["json"]
    assert payload["model"] == "gpt-6.2-sol"
    assert payload["store"] is False
    assert payload["max_output_tokens"] == 8000
    assert "tools" not in payload and "text" not in payload
    assert payload["input"][0]["content"] == [{"type": "input_text", "text": "private source"}]
    event = llm.usage_log[-1]
    assert event["provider"] == "openai" and event["model"] == "gpt-6.2-sol"
    assert event["cached_tokens"] == 8 and event["input_tokens"] == 12
    assert "private" not in json.dumps(event) and "secret" not in json.dumps(event)


def test_strict_schema_handles_nested_refs_nullable_and_defaults(transport):
    post, _ = transport
    assert llm.generate("prompt", Answer, structured=True).found
    fmt = post.call_args.kwargs["json"]["text"]["format"]
    assert fmt["strict"] is True and fmt["type"] == "json_schema"
    assert post.call_args.kwargs["json"]["max_output_tokens"] == 16000
    schema = llm._openai_schema(Deck)
    citation = schema["$defs"]["Citation"]
    assert citation["additionalProperties"] is False
    assert set(citation["required"]) == set(citation["properties"])
    assert "default" not in citation["properties"]["kind"]
    assert {"type": "null"} in citation["properties"]["url"]["anyOf"]
    assert schema["$defs"]["Slide"]["properties"]["sources"]["items"]["$ref"] == "#/$defs/Citation"
    assert "default" in Deck.model_json_schema()["$defs"]["Citation"]["properties"]["kind"]


def test_visual_provider_is_independent(transport, monkeypatch):
    post, _ = transport
    monkeypatch.setattr(llm, "config", {"provider": "ollama", "model": "local",
                                       "visual": {"provider": "openai", "model": "gpt-6.2-sol"}})
    png = b"\x89PNG\r\n\x1a\nimage"
    result, metadata = llm.generate_visual(png, "PDF page extracted text", Answer)
    assert result.found and metadata["cached_tokens"] == 8
    payload = post.call_args.kwargs["json"]
    assert payload["model"] == "gpt-6.2-sol"
    assert payload["input"][0]["content"] == [
        {"type": "input_text", "text": "PDF page extracted text"},
        {"type": "input_image", "image_url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")},
    ]
    assert payload["text"]["format"]["strict"] is True


def test_missing_key_fails_before_network(transport, monkeypatch):
    post, _ = transport
    monkeypatch.delenv("OPENAI_API_KEY")
    with pytest.raises(llm.OpenAIProviderError, match="OPENAI_API_KEY"):
        llm.generate("prompt", Answer)
    post.assert_not_called()


@pytest.mark.parametrize("status, message", [(401, "authentication"), (403, "access denied"),
                                             (429, "quota"), (302, "request failed"), (500, "request failed")])
def test_http_errors_are_safe(transport, status, message):
    _, reply = transport
    reply.status_code = status
    reply.text = "private body secret-test-key"
    with pytest.raises(llm.OpenAIProviderError, match=message) as error:
        llm.generate("private source", Answer)
    assert "private" not in str(error.value) and "secret" not in str(error.value)
    reply.json.assert_not_called()
    assert llm.usage_log == []


@pytest.mark.parametrize("error", [requests.ConnectionError("secret private"), requests.Timeout("secret private")])
def test_transport_errors_are_safe(transport, error):
    post, _ = transport
    post.side_effect = error
    with pytest.raises(llm.OpenAIProviderError, match="connection") as caught:
        llm.generate("private", Answer)
    assert "secret" not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize("data, exception, message", [
    ({"status": "incomplete", "incomplete_details": {"reason": "private"}}, llm.IncompleteReply, "did not complete"),
    ({"status": "failed", "error": {"message": "private"}}, llm.OpenAIProviderError, "did not complete"),
    ({"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "private"}]}]}, llm.OpenAIProviderError, "refused"),
    ({"status": "completed", "output": []}, llm.OpenAIProviderError, "no usable text"),
    ([], llm.OpenAIProviderError, "invalid response"),
    (response('not JSON private'), llm.OpenAIProviderError, "requested format"),
    (response('{"found":"private"}'), llm.OpenAIProviderError, "requested format"),
])
def test_failed_responses_do_not_expose_content(transport, data, exception, message):
    _, reply = transport
    reply.json.return_value = data
    with pytest.raises(exception, match=message) as error:
        llm.generate("prompt", Answer, structured=True)
    assert "private" not in str(error.value)


def test_invalid_response_json_is_safe(transport):
    _, reply = transport
    reply.json.side_effect = ValueError("private response")
    with pytest.raises(llm.OpenAIProviderError, match="invalid response"):
        llm.generate("prompt", Answer)


def test_untrusted_usage_fields_are_not_logged(transport):
    _, reply = transport
    data = response()
    data["usage"] = {"input_tokens": "private", "output_tokens": {}, "input_tokens_details": {"cached_tokens": "secret"}}
    reply.json.return_value = data
    llm.generate("prompt", Answer)
    assert llm.usage_log[-1]["input_tokens"] is None
    assert llm.usage_log[-1]["cached_tokens"] is None
