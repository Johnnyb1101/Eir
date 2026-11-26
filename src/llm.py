import json
import base64
import os
import anthropic
import requests
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from src.settings import load_config

client = None
config = load_config().model_dump()

usage_log = []
_active_usage_log = ContextVar("active_usage_log", default=None)


@contextmanager
def capture_usage_log(log):
    token = _active_usage_log.set(log)
    try:
        yield
    finally:
        _active_usage_log.reset(token)


def record_usage(event):
    log = _active_usage_log.get()
    (usage_log if log is None else log).append(event)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_VISUAL_BYTES = 4 * 1024 * 1024


class IncompleteReply(ValueError):
    pass


class OpenAIProviderError(ValueError):
    """An actionable provider error that never includes request or response content."""


def _openai_schema(schema):
    def strict(node):
        if isinstance(node, list):
            return [strict(value) for value in node]
        if not isinstance(node, dict):
            return node
        result = {key: strict(value) for key, value in node.items() if key != "default"}
        if result.get("type") == "object" or "properties" in result:
            result["additionalProperties"] = False
            result["required"] = list(result.get("properties", {}))
        return result

    return strict(schema.model_json_schema())


def _generate_openai(prompt, system, *, model, response_schema=None, encoded_image=None):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise OpenAIProviderError("Set OPENAI_API_KEY in the process environment to use OpenAI.")
    content = [{"type": "input_text", "text": prompt}]
    if encoded_image is not None:
        content.append({"type": "input_image", "image_url": f"data:image/png;base64,{encoded_image}"})
    payload = {
        "model": model,
        "instructions": system,
        "input": [{"role": "user", "content": content}],
        "store": False,
        "max_output_tokens": 16000 if response_schema is not None else 8000,
    }
    if response_schema is not None:
        payload["text"] = {"format": {
            "type": "json_schema", "name": "eir_response", "strict": True,
            "schema": _openai_schema(response_schema),
        }}
    try:
        reply = requests.post(
            "https://api.openai.com/v1/responses",
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload, timeout=(10, 180), allow_redirects=False,
        )
    except requests.exceptions.RequestException:
        raise OpenAIProviderError("OpenAI connection failed or timed out. Check connectivity and retry.") from None
    if reply.status_code != 200:
        messages = {
            401: "OpenAI authentication failed. Check OPENAI_API_KEY.",
            403: "OpenAI access denied. Check project and model permissions.",
            429: "OpenAI rate or quota limit reached. Check billing or retry later.",
        }
        raise OpenAIProviderError(messages.get(
            reply.status_code, "OpenAI request failed. Check model configuration and service availability."
        ))
    try:
        data = reply.json()
    except ValueError:
        raise OpenAIProviderError("OpenAI returned an invalid response. Retry the request.") from None
    if not isinstance(data, dict):
        raise OpenAIProviderError("OpenAI returned an invalid response. Retry the request.")
    if data.get("status") == "incomplete":
        raise IncompleteReply("OpenAI reply did not complete within the output limit.")
    if data.get("status") != "completed":
        raise OpenAIProviderError("OpenAI did not complete the request. Retry or check service availability.")
    output = data.get("output")
    if not isinstance(output, list):
        raise OpenAIProviderError("OpenAI returned no usable text content.")
    parts = []
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        blocks = item.get("content")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "refusal":
                raise OpenAIProviderError("OpenAI refused the request. Review the supplied content.")
            if block.get("type") == "output_text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
    text = "".join(parts)
    if not text.strip():
        raise OpenAIProviderError("OpenAI returned no usable text content.")
    usage = data.get("usage")
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("input_tokens_details")
    details = details if isinstance(details, dict) else {}
    def token_count(value):
        return value if type(value) is int and value >= 0 else None
    return (text, token_count(usage.get("input_tokens")),
            token_count(usage.get("output_tokens")), token_count(details.get("cached_tokens")))


def _validate_openai_text(text, schema, *, exact):
    try:
        data = json.loads(text if exact else text[text.find("{"):text.rfind("}") + 1])
        return schema.model_validate(data)
    except ValueError:
        raise OpenAIProviderError("OpenAI returned content that does not match the requested format.") from None


def _generate_anthropic(prompt, system, *, response_schema=None):
    global client
    if client is None:
        client = anthropic.Anthropic()
    options = {"max_tokens": 8000}
    if response_schema is not None:
        output = {"format": {"type": "json_schema", "schema": anthropic.transform_schema(response_schema)}}
        # Sonnet 5 counts thinking against the output limit and defaults to high effort.
        if config["model"] == "claude-sonnet-5":
            output["effort"] = "medium"
        options = {"max_tokens": 16000, "output_config": output}
    reply = client.messages.create(model=config["model"], messages=[{"role": "user", "content": prompt}],
                                   system=system, **options)
    if reply.stop_reason == "max_tokens":
        raise IncompleteReply("Reply truncated by max_tokens cap - raise the limit")
    if response_schema is not None and reply.stop_reason != "end_turn":
        raise IncompleteReply("Structured reply did not complete")
    if response_schema is not None:
        text = "".join(block.text for block in reply.content if block.type == "text")
        return text, reply.usage.input_tokens, reply.usage.output_tokens
    text = next(b.text for b in reply.content if b.type == "text")
    return text, reply.usage.input_tokens, reply.usage.output_tokens

def _generate_ollama(prompt, system):
    reply = requests.post(
        "http://localhost:11434/api/chat",
        json={
            "model": config["model"],
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "stream": False,
        },
        timeout=600
    )
    reply.raise_for_status()
    data = reply.json()
    if "error" in data:
        raise ValueError(f"Ollama request failed: {data['error']}")
    return data["message"]["content"], data["prompt_eval_count"], data["eval_count"]


def generate_visual(image_bytes, prompt, schema, system="You return only valid JSON."):
    visual = config.get("visual")
    if visual is None:
        raise ValueError("Visual processing is not configured")
    if not isinstance(image_bytes, bytes) or not image_bytes.startswith(_PNG_SIGNATURE):
        raise ValueError("Visual input must be PNG bytes")
    if len(image_bytes) > _MAX_VISUAL_BYTES:
        raise ValueError("Visual input exceeds the 4 MiB limit")

    provider = visual["provider"]
    model = visual["model"]
    encoded_image = base64.b64encode(image_bytes).decode("ascii")
    cached_tokens = None
    if provider == "anthropic":
        global client
        if client is None:
            client = anthropic.Anthropic()
        reply = client.messages.create(
            model=model,
            max_tokens=8000,
            system=system,
            output_config={"format": {"type": "json_schema", "schema": anthropic.transform_schema(schema)}},
            messages=[{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": encoded_image}},
                {"type": "text", "text": prompt},
            ]}],
        )
        if reply.stop_reason == "max_tokens":
            raise ValueError("Visual reply truncated by max_tokens cap")
        if reply.stop_reason == "refusal":
            raise ValueError("Anthropic refused the visual request")
        text = "".join(block.text for block in reply.content if block.type == "text")
        if not text.strip():
            raise ValueError("Visual reply has no text content")
        usage = getattr(reply, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None)
        tokens_out = getattr(usage, "output_tokens", None)
    elif provider == "ollama":
        show = requests.post("http://localhost:11434/api/show", json={"model": model}, timeout=600)
        show.raise_for_status()
        show_data = show.json()
        if "error" in show_data:
            raise ValueError(f"Ollama model inspection failed: {show_data['error']}")
        if "vision" not in (show_data.get("capabilities") or []):
            raise ValueError(f"Ollama model {model!r} does not support vision")
        reply = requests.post(
            "http://localhost:11434/api/chat",
            json={
                "model": model,
                "messages": [{"role": "system", "content": system},
                             {"role": "user", "content": prompt, "images": [encoded_image]}],
                "format": schema.model_json_schema(),
                "stream": False,
            },
            timeout=600,
        )
        reply.raise_for_status()
        data = reply.json()
        if "error" in data:
            raise ValueError(f"Ollama visual request failed: {data['error']}")
        if data.get("done_reason") == "length":
            raise ValueError("Ollama visual reply truncated")
        message = data.get("message")
        text = message.get("content") if isinstance(message, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Ollama visual reply has no text content")
        tokens_in = data.get("prompt_eval_count")
        tokens_out = data.get("eval_count")
    elif provider == "openai":
        text, tokens_in, tokens_out, cached_tokens = _generate_openai(
            prompt, system, model=model, response_schema=schema, encoded_image=encoded_image,
        )
    else:
        raise ValueError(f"Unknown visual provider: {provider}")

    result = (_validate_openai_text(text, schema, exact=True) if provider == "openai"
              else schema.model_validate(json.loads(text)))
    metadata = {
        "provider": provider,
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    if provider == "openai":
        metadata["cached_tokens"] = cached_tokens
    record_usage({"agent": "visual", **metadata})
    return result, metadata

def generate(prompt, schema, system="You return only valid JSON.", agent="unknown",
             slide_index=None, attempt=None, *, structured=False):
    openai_metadata = {}
    if config["provider"] == "anthropic":
        options = {"response_schema": schema} if structured else {}
        text, tokens_in, tokens_out = _generate_anthropic(prompt, system, **options)
    elif config["provider"] == "ollama":
        text, tokens_in, tokens_out = _generate_ollama(prompt, system)
    elif config["provider"] == "openai":
        text, tokens_in, tokens_out, cached_tokens = _generate_openai(
            prompt, system, model=config["model"], response_schema=schema if structured else None,
        )
        openai_metadata = {"provider": "openai", "model": config["model"], "cached_tokens": cached_tokens}
    else:
        raise ValueError(f"Unknown provider: {config['provider']}")
    
    record_usage({
        "agent": agent,
        "slide_index": slide_index,
        "attempt": attempt,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "time": datetime.now().isoformat(timespec="seconds"),
        **openai_metadata,
    })
    if config["provider"] == "openai":
        return _validate_openai_text(text, schema, exact=structured)
    exact_json = structured and config["provider"] == "anthropic"
    data = json.loads(text if exact_json else text[text.find("{") : text.rfind("}") + 1])
    return schema.model_validate(data)
