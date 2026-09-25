import json
import anthropic
import requests
from datetime import datetime
from src.settings import load_config

client = None
config = load_config().model_dump()

usage_log = []

def _generate_anthropic(prompt, system):
    global client
    if client is None:
        client = anthropic.Anthropic()
    reply = client.messages.create(
        model=config["model"],
        max_tokens=8000,
        messages=[{"role": "user", "content": prompt}],
        system=system
    )
    if reply.stop_reason == "max_tokens":
        raise ValueError("Reply truncated by max_tokens cap - raise the limit")
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

def generate(prompt, schema, system="You return only valid JSON.", agent="unknown",
             slide_index=None, attempt=None):
    if config["provider"] == "anthropic":
        text, tokens_in, tokens_out = _generate_anthropic(prompt, system)
    elif config["provider"] == "ollama":
        text, tokens_in, tokens_out = _generate_ollama(prompt, system)
    else:
        raise ValueError(f"Unknown provider: {config['provider']}")
    
    usage_log.append({
        "agent": agent,
        "slide_index": slide_index,
        "attempt": attempt,
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "time": datetime.now().isoformat(timespec="seconds"),
    })
    data = json.loads(text[text.find("{") : text.rfind("}") + 1])
    return schema.model_validate(data)
