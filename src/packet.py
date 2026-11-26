def write_packet(deck, verdicts, attempts, path, *, selected_source=None, visual_reviews=()):
    if len(deck.slides) != len(verdicts):
        raise ValueError(f"{len(deck.slides)} slides but {len(verdicts)} verdicts")
    if len(deck.slides) != len(attempts):
        raise ValueError(f"{len(deck.slides)} slides but {len(attempts)} attempt counts")
    enriched = any(c.kind == "supplemental" for slide in deck.slides for c in slide.sources)
    lines = []
    lines.append(f"# Review Packet: {deck.title}")
    lines.append("")
    if selected_source is not None:
        lines.extend(["## Selected source PDF", selected_source.filename,
                      f"Text extractor `{selected_source.extractor}`",
                      f"SHA-256 fingerprint `{selected_source.sha256}`", ""])
        for warning in selected_source.warnings:
            lines.append(f"- {warning}")
        if selected_source.warnings:
            lines.append("")
    if visual_reviews:
        lines.extend(["## Unreviewed visual checks", "Verify these findings against the original PDF pages.", ""])
        for review in visual_reviews:
            if selected_source is None or review.source_sha256 != selected_source.sha256:
                raise ValueError("Visual check must match the selected PDF fingerprint")
            lines.extend([f"### PDF page {review.page_number}", f"{review.provider} / {review.model}"])
            for item in review.missing or ("The agent reported no missing material.",):
                lines.append(f"- {item}")
            for item in review.uncertainties:
                lines.append(f"- Uncertainty · {item}")
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
            if not enriched:
                lines.append(f"- {c.source}, {c.section}, {c.pages}")
                continue
            label = "Supplemental source" if c.kind == "supplemental" else "Primary CPG"
            location = f"{c.source}, {c.section}"
            if c.pages:
                location += f", {c.pages}"
            if c.url:
                location += f" ([source link]({c.url}))"
            lines.append(f"- {label}: {location}")
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
