import base64
import io
import json
from unittest.mock import Mock

import anthropic
import pytest
import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from src import llm, supplemental
from src.contracts import OutlineEntry


URL = "https://www.resus.org.uk/guidance"
QUOTE = "Start chest compressions promptly and minimize interruptions."


def fetch(url=URL, text=QUOTE):
    return {"type": "web_fetch_tool_result", "tool_use_id": "srvtoolu_1",
            "content": {"type": "web_fetch_result", "url": url,
                        "retrieved_at": "2026-01-01T00:00:00Z",
                        "content": {"type": "document", "title": "Guideline",
                                    "source": {"type": "text", "media_type": "text/plain",
                                               "data": text}}}}


def answer(url=URL, excerpt=QUOTE):
    return {"type": "text", "text": json.dumps({"excerpts": [
        {"url": url, "excerpt": excerpt, "title": "Invented title", "pages": "999"}]})}


def reply(blocks, stop="end_turn"):
    return anthropic.types.Message.model_validate({
        "id": "msg_test", "type": "message", "role": "assistant", "model": "test",
        "content": blocks, "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 20}})


def test_real_sdk_raw_shape_and_computed_locator():
    raw = reply([fetch(text="Intro\n" + QUOTE), answer()])
    result = supplemental._verify([b.model_dump(exclude_none=True) for b in raw.content])
    assert result.errors == ()
    assert result.leads == ()
    chunk, = result.chunks
    assert chunk["text"] == QUOTE
    assert chunk["source"] == "Guideline"
    assert chunk["url"] == URL
    assert chunk["pages"] == ""
    assert chunk["section"] == f"Characters 7-{6 + len(QUOTE)} of fetched text"
    assert chunk["kind"] == "supplemental"


@pytest.mark.parametrize("blocks", [
    [answer()],
    [fetch(), answer(excerpt="A fabricated clinical claim that never appeared in the source.")],
    [fetch(), answer(url="https://jts.health.mil/other")],
    [fetch(url="https://evil.example/guidance"), answer(url="https://evil.example/guidance")],
    [fetch(), answer(url="https://www.resus.org.uk.evil.example/guidance")],
])
def test_unfetched_fabricated_and_misattributed_excerpts_are_not_evidence(blocks):
    assert supplemental._verify(blocks).chunks == ()


def test_search_snippets_are_only_leads():
    result = supplemental._verify([{"type": "web_search_tool_result", "content": [
        {"url": URL, "title": "Guideline", "text": QUOTE}]}, answer()])
    assert result.chunks == ()
    assert result.leads == (URL,)


def test_error_distinguished_from_no_hits():
    assert supplemental._verify([]).errors == ()
    result = supplemental._verify([{"type": "web_fetch_tool_result", "content": {
        "type": "web_fetch_tool_result_error", "error_code": "unavailable"}}])
    assert result.errors == ("Literature fetch tool failed.",)


def test_bounded_calls_usage_and_gap_only_payload(monkeypatch):
    client = Mock()
    client.with_options.return_value = client
    client.messages.create.side_effect = [reply([fetch()], "pause_turn"), reply([answer()])]
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "config", {"provider": "anthropic", "model": "test"})
    monkeypatch.setattr(llm, "usage_log", [])
    result = supplemental.find_supplemental_sources(OutlineEntry(
        title="Resuscitation", objective="Timing of compressions", time_minutes=1,
        chunk_ids=["PRIVATE-CPG-ID"], outside_sources=["PRIVATE-SOURCE-PATH"]))
    assert len(result.chunks) == 1
    assert client.messages.create.call_count == 2
    assert len(llm.usage_log) == 2
    args = client.messages.create.call_args.kwargs
    assert "PRIVATE" not in json.dumps(args)
    assert all(tool["max_uses"] == 3 for tool in args["tools"])
    client.with_options.assert_called_once_with(timeout=90.0, max_retries=0)


def test_continuation_limit(monkeypatch):
    client = Mock()
    client.with_options.return_value = client
    client.messages.create.return_value = reply([], "pause_turn")
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "config", {"provider": "anthropic", "model": "test"})
    entry = OutlineEntry(title="Gap", objective="Objective", time_minutes=1)
    result = supplemental.find_supplemental_sources(entry)
    assert client.messages.create.call_count == supplemental.MAX_RESPONSES
    assert "continuation limit" in result.errors[0]


def test_provider_failure_is_redacted(monkeypatch):
    client = Mock()
    client.with_options.return_value = client
    client.messages.create.side_effect = anthropic.APIConnectionError(
        message="PRIVATE request details", request=httpx.Request("POST", "https://example.com"))
    monkeypatch.setattr(llm, "client", client)
    monkeypatch.setattr(llm, "config", {"provider": "anthropic", "model": "test"})
    result = supplemental.find_supplemental_sources(OutlineEntry(
        title="Gap", objective="Objective", time_minutes=1))
    assert result.errors == ("Literature provider request failed.",)
    assert "PRIVATE" not in repr(result)


def test_pdf_excerpt_gets_verified_physical_page():
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
                             NameObject("/Subtype"): NameObject("/Type1"),
                             NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"):
        DictionaryObject({NameObject("/F1"): font})})
    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 12 Tf 50 700 Td ({QUOTE}) Tj ET".encode())
    page[NameObject("/Contents")] = stream
    output = io.BytesIO()
    writer.write(output)
    block = fetch()
    block["content"]["content"]["source"] = {
        "type": "base64", "media_type": "application/pdf",
        "data": base64.b64encode(output.getvalue()).decode()}
    result = supplemental._verify([block, answer()])
    assert result.chunks[0]["pages"] == "2"
    assert result.chunks[0]["section"].startswith("Characters ")


@pytest.mark.parametrize("url", ["http://www.resus.org.uk/guidance",
                                  "https://user:secret@www.resus.org.uk/guidance",
                                  "https://www.resus.org.uk:999/guidance"])
def test_unsafe_url_rejected(url):
    assert supplemental._canonical_url(url) is None
