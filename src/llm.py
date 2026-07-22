import json
import anthropic
import yaml

client = anthropic.Anthropic()

with open("config.yaml") as f:
    config = yaml.safe_load(f)

usage_log = []

def generate(prompt, schema, system="You return only valid JSON.", agent="unknown"):
    reply = client.messages.create(
        model=config["model"],
        max_tokens=4000,
        messages=[{"role": "user", "content": prompt}],
        system=system
    )
    if reply.stop_reason == "max_tokens":
        raise ValueError("Reply truncated by max_tokens cap - raise the limit")
    usage_log.append({
        "agent": agent,
        "input_tokens": reply.usage.input_tokens,
        "output_tokens": reply.usage.output_tokens,
    })
    text = next(b.text for b in reply.content if b.type == "text")
    data = json.loads(text[text.find("{") : text.rfind("}") + 1])
    return schema.model_validate(data)