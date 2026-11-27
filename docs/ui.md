# Local training review UI

The Streamlit interface lets one operator request a training outline, review its timing and retrieval results, ask for a revision, approve generation, and download the resulting files. It runs on the local computer only. A subject matter expert still needs to verify the source material and deck before teaching from it.

## Start

From the repository root, use Eir's virtual environment to install the optional UI dependencies and start Streamlit.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-ui.txt
.\venv\Scripts\python.exe -m streamlit run app.py
```

The app binds to `127.0.0.1`. Open the local address printed by Streamlit. Close the process with Ctrl+C. The app reads the provider and model from `config.yaml` and the optional `config.local.yaml`. Set credentials in the environment before starting Streamlit. Restart the process when changing provider settings or credentials. To choose a different local or synced PDF folder, set `source_folder` in `config.local.yaml` (for example, `source_folder: "D:/Synced/Clinical Guidelines"`). Relative paths start at the repository root; the default is `corpus/`. Restart Streamlit after changing the folder.

## Review a run

Enter a topic, audience, and duration in minutes. The default is 30 minutes and the minimum is 2 minutes. **Indexed documents** is the default source. To use one original PDF, select **One selected PDF** and choose a file from the configured folder. The selected-PDF text extractor defaults to **pypdf**. Choose **PDFium** to try the pilot on that PDF. The current run shows the selected file, extractor, and extraction warnings. If the folder is empty or unavailable, indexed mode remains usable. Review image-only pages against the original. The outliner proposes entries with titles, time, and objectives. Retrieved references identify document locations to review. The outline also shows unverified IDs and outside source leads.

After a selected-PDF outline is ready, **Optional page check** can inspect a specific original PDF page. Enter its 1-based page number and select **Check this page**. The agent compares the rendered page and extracted text, then lists possible omissions and uncertainties for review. Compare its findings with the original page. Wait for the check to finish before approving, revising, or discarding the outline. The review packet records the extractor, PDF fingerprint, and unreviewed findings alongside the original-page citations.

Set `visual.provider` and `visual.model` in `config.local.yaml` to enable page checks. See `config.local.example.yaml` for examples. Configure the visual model independently of the model used for outline and slide generation. For Anthropic or OpenAI, Eir sends the selected page image and extracted text to that provider's API when you request a check. Ollama receives them at its local endpoint. Restart Streamlit after changing visual settings.

Use **Request revision** with feedback to produce another outline, **Approve outline** to start deck generation, or **Discard run** to close it and start over. A closed run leaves the request form available for another run. During generation, the page updates progress automatically. A completed run offers the PowerPoint deck, review packet, and audit log. The history panel also lists completed runs found on disk, including compatible older benchmark output.

Use **Full-deck re-review** on an eligible completed selected-PDF run, either in the current panel or in **Completed run history**. Eir uses the saved approved outline, verified excerpts, and the exact saved PowerPoint to review each teaching slide through the configured model. The pinned PDF stays primary and saved page-check notes stay in their separate section. A completed review publishes a new packet and audit log with a copy of the same PowerPoint. Keep `output/.review-inputs/` if you want to reopen saved inputs after restarting Eir.

If generation fails, the run panel shows where it stopped and `partial-usage.json` records the events collected so far. Each new run gets its own directory under `output/runs/`. Completed files stay available after restarting the app. Keep this local preview to one operator at a time.
