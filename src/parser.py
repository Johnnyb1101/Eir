from src.contracts import RequestSpec
from src.llm import generate
def parse_request(text):
    prompt = f"""convert this request into JSON with exactly these keys:
topic (string), duration_minutes (integer),
output_format (must be exactly one of: "pptx", "docx", "pdf" - use "pptx" if not specified), audience (string).
Reply with ONLY the JSON - no other text.

Request: {text}"""
    return generate(prompt, RequestSpec, agent="parser")