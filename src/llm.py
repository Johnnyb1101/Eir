import json
import base64
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
    else:
        raise ValueError(f"Unknown visual provider: {provider}")

    result = schema.model_validate(json.loads(text))
    metadata = {
        "provider": provider,
        "model": model,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    record_usage({"agent": "visual", **metadata})
    return result, metadata

def generate(prompt, schema, system="You return only valid JSON.", agent="unknown",
             slide_index=None, attempt=None, *, structured=False):
    if config["provider"] == "anthropic":
        options = {"response_schema": schema} if structured else {}
        text, tokens_in, tokens_out = _generate_anthropic(prompt, system, **options)
    elif config["provider"] == "ollama":
        text, tokens_in, tokens_out = _generate_ollama(prompt, system)
    else:
        raise ValueError(f"Unknown provider: {config['provider']}")
    
    record_usage({
        "agent": agent,
        "slide_index": slide_index,
        "attempt": attempt,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "time": datetime.now().isoformat(timespec="seconds"),
    })
    exact_json = structured and config["provider"] == "anthropic"
    data = json.loads(text if exact_json else text[text.find("{") : text.rfind("}") + 1])
    return schema.model_validate(data)
