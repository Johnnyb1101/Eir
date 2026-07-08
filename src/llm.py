import json
import anthropic
import yaml

client = anthropic.Anthropic()

with open("config.yaml") as f:
    config = yaml.safe_load(f)

def generate(prompt, schema):
    reply = client.messages.create(
        model=config["model"],
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = reply.content[0].text
    data = json.loads(text[text.find("{") : text.rfind("}") + 1])
    return schema.model_validate(data)