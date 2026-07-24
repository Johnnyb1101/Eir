import json
import anthropic
import yaml
from datetime import datetime

client = anthropic.Anthropic()

with open("config.yaml") as f:
    config = yaml.safe_load(f)

usage_log = []

def _generate_anthropic(prompt, system):
    reply = client.messages.create(
        model=config["model"],
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
        system=system
    )
    if reply.stop_reason == "max_tokens":
        raise ValueError("Reply truncated by max_tokens cap - raise the limit")
    text = next(b.text for b in reply.content if b.type == "text")
    return text, reply.usage.input_tokens, reply.usage.output_tokens

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