# Selected-page visual extraction

Use this when extraction warnings or manual inspection identify a chart, table,
infographic, or flowchart missing from ordinary text extraction. It does not
change ingestion, retrieval, or generated slides.

## Setup

Install the pinned dependencies with `python -m pip install -r requirements.txt`.
PDFium (`pypdfium2`) renders the page locally; Pillow encodes it as PNG. Both
packages are declared directly. No separate Poppler installation is needed.

Set `visual.provider` and `visual.model` in your `config.local.yaml`, independently
of your text-generation model. For example, replace the placeholder:

```yaml
visual:
  provider: ollama
  model: "YOUR_INSTALLED_VISION_MODEL"
```

For Anthropic, use `provider: anthropic`, a vision-capable model available to your
account, and `ANTHROPIC_API_KEY` in the environment. Calling the command sends the
rendered page to that service and incurs API usage. Ollama uses localhost; choose
a locally running model if the document must remain local. No model downloads or
automatic provider fallback occur. `visual: null` disables the command.

## Run from the project root

```powershell
python -m src.visual_extract "corpus/Airway_Management_in_Trauma_28_Jan_2026_ID39.pdf" --page 4
```

`--page` is the 1-based PDF page index, which may differ from printed page numbers.
You can select any page, including one the warning heuristic missed. Each call
interprets one page; visual extraction is never automatically run for all pages.

Results appear in a new directory under `output/visual_reviews/`:

- `page.png`: the rendered image sent to the model, capped at 2200 pixels on its
  longest edge and 4 MiB. The provider may downsample further.
- `interpretation.json`: unreviewed content, table rows and footnotes, flowchart
  nodes and labeled connections, chart descriptions, and uncertainties, plus
  source path, original PDF page index, source/image SHA-256 hashes, timestamp,
  provider/model and usage counts when available.

Every result is marked `unreviewed` by code. Unique directories preserve earlier
results. Provider or validation failures propagate and do not produce an
interpretation. A filesystem failure while saving may leave partial files;
successful completion is indicated by the printed result path. API usage may
already have been incurred if saving fails.

## Review before use

Compare the result with both `page.png` and the original PDF. Check numbers,
units, qualifications, table alignment, arrow direction and branch labels. The
schema validates structure, not medical accuracy or completeness. Dense pages,
small labels, color meaning and arrows can be misread. An empty uncertainties
list is not evidence of correctness. The original PDF remains authoritative.

This step supplies review material only. It does not provide an approval command
or import interpretations into ChromaDB. Human approval and controlled ingestion
are future work. Ordinary text extraction remains the primary path.
