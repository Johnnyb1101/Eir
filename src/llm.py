import json
import anthropic

client = anthropic.Anthropic()

def generate(prompt, schema):
    reply = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}],
    )
    text = reply.content[0].text
    data = json.loads(text[text.find("{") : text.rfind("}") + 1])
    return schema.model_validate(data)