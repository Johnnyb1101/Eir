from datetime import datetime
from pathlib import Path

from src.llm import capture_usage_log, record_usage
from src.parser import parse_request
from src.pipeline import RunArtifacts, build_deck, export_draft, prepare_outline


def show_outline(prepared):
    print(f"\nProposed outline for '{prepared.outline.topic}':")
    for entry, evidence in zip(prepared.outline.entries, prepared.evidence, strict=True):
        chunk_ids = ", ".join(chunk.id for chunk in evidence.retrieved)
        print(f"  {entry.time_minutes} min - {entry.title}")
        print(f"        {entry.objective}")
        print(f"        [{evidence.state}] {chunk_ids or 'no chunks'}")
        for source in entry.outside_sources:
            print(f"        lead (unverified): {source}")
        if evidence.fabricated:
            print(f"        !! INVENTED ID: {', '.join(evidence.fabricated)}")
        if evidence.unmatched:
            print(f"        ?? CITED BUT NOT RETRIEVED: {', '.join(evidence.unmatched)}")


def show_retry(attempt, problems):
    if problems is None:
        print(f" Attempt {attempt} invalid output")
    else:
        print(f"  Attempt {attempt} failed: {problems}")


def main():
    log = []
    with capture_usage_log(log):
        request = input("What training do you need? ")
        spec = parse_request(request)
        print(f"Topic: {spec.topic} | {spec.duration_minutes} min | audience: {spec.audience}")
        note = None
        while True:
            prepared = prepare_outline(spec, note)
            show_outline(prepared)
            if input("\nApprove this outline? (y/n) ").lower() == "y":
                break
            note = input("What needs to change? ")
            record_usage({
                "agent": "human", "action": "outline_rejected", "note": note,
                "time": datetime.now().isoformat(timespec="seconds"),
            })
        draft = build_deck(prepared, on_retry=show_retry, refresh_sources=True)

    if draft.flags:
        print("\nFLAGGED FOR HUMAN REVIEW:")
        for note in draft.flags:
            print(f"  Slide {note.slide_index}: {note.note}")
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    artifacts = RunArtifacts(Path("output/deck.pptx"),
                             Path(f"output/review_packet_{stamp}.md"),
                             Path(f"logs/run_{stamp}.json"))
    export_draft(draft, artifacts, log, on_rendered=lambda: print(
        f"Rendered {len(draft.deck.slides)} slides to output/deck.pptx"
    ))
    print(f"Review packet written to {artifacts.packet.as_posix()}")


if __name__ == "__main__":
    main()
