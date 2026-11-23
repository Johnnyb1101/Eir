# Local training review UI

The Streamlit interface lets one operator request a training outline, review its timing and retrieval results, ask for a revision, approve generation, and download the resulting files. It runs on the local computer only. A subject matter expert still needs to verify the source material and deck before teaching from it.

## Start

From the repository root, use Eir's virtual environment to install the optional UI dependencies and start Streamlit.

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements-ui.txt
.\venv\Scripts\python.exe -m streamlit run app.py
```

The app binds to `127.0.0.1`. Open the local address printed by Streamlit. Close the process with Ctrl+C. The app reads the provider and model from `config.yaml` and the optional `config.local.yaml`. Set credentials in the environment before starting Streamlit. Restart the process when changing provider settings or credentials.

## Review a run

Enter a topic, audience, and duration in minutes. The default is 30 minutes and the minimum is 2 minutes. The outliner proposes entries with titles, time, and objectives. Retrieved references show document locations, while unverified IDs and outside source leads are called out separately. These locations do not establish that each claim is supported.

Use **Request revision** with feedback to produce another outline, **Approve outline** to start deck generation, or **Discard run** to close it and start over. A closed run leaves the request form available for another run. During generation, the page updates progress automatically. A completed run offers the PowerPoint deck, review packet, and audit log. The history panel also lists completed runs found on disk, including compatible older benchmark output.

If generation fails, the run panel shows where it stopped and `partial-usage.json` records the events collected so far. Each new run gets its own directory under `output/runs/`. Completed files stay available after restarting the app. Keep this local preview to one operator at a time.
