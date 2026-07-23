def write_packet(deck, verdicts, path):
    if len(deck.slides) != len(verdicts):
        raise ValueError(f"{len(deck.slides)} slides but {len(verdicts)} verdicts")
    lines = []
    lines.append(f"# Review Packet: {deck.title}")
    lines.append("")
    for i, (slide, verdict) in enumerate(zip(deck.slides, verdicts)):
        lines.append(f"## Slide {i}: {slide.title}")
        lines.append("**Citations:**")
        for c in slide.sources:
            lines.append(f"- {c.source}, {c.section}, {c.pages}")
        if verdict.passed:
            lines.append("**Verdict:** PASS")
        else:
            lines.append("**Verdict:** NEEDS REVIEW")
            for p in verdict.problems:
                lines.append(f"- {p}")
        lines.append("")
    text = "\n".join(lines)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)