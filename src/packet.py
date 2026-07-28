def write_packet(deck, verdicts, attempts, path):
    if len(deck.slides) != len(verdicts):
        raise ValueError(f"{len(deck.slides)} slides but {len(verdicts)} verdicts")
    if len(deck.slides) != len(attempts):
        raise ValueError(f"{len(deck.slides)} slides but {len(attempts)} attempt counts")
    lines = []
    lines.append(f"# Review Packet: {deck.title}")
    lines.append("")
    for i, (slide, verdict) in enumerate(zip(deck.slides, verdicts)):
        lines.append(f"## Slide {i}: {slide.title}")
        lines.append("**Content:**")
        for b in slide.bullets:
            lines.append(f"- {b}")
        lines.append("")
        lines.append("**Speaker notes:**")
        lines.append(slide.speaker_notes)
        lines.append("")
        lines.append("**Citations:**")
        for c in slide.sources:
            lines.append(f"- {c.source}, {c.section}, {c.pages}")
        if verdict.passed:
            lines.append(f"**Verdict:** PASS (attempts: {attempts[i]})")
        else:
            lines.append(f"**Verdict:** NEEDS REVIEW (attempts: {attempts[i]})")
            for p in verdict.problems:
                lines.append(f"- {p}")
        lines.append("")
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)