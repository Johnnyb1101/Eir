"""Local review interface for Eir training runs."""

from pathlib import Path

import streamlit as st

from src.contracts import RequestSpec
from src.source_pdf import list_source_pdfs
from src.ui_jobs import RunConflict, RunManager


OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
RETRIEVAL_STATUS = {
    "SOURCED": "Related material retrieved",
    "PARTLY SOURCED": "Related material and outside leads",
    "UNSOURCED": "No material retrieved",
}
SOURCE_LABELS = {"indexed": "Indexed documents", "selected_pdf": "Selected PDF"}

st.set_page_config(page_title="Eir | Training review", page_icon="📋", layout="wide")


@st.cache_resource
def run_manager() -> RunManager:
    return RunManager(OUTPUT_ROOT)


def show_error(error: Exception) -> None:
    st.error("The action could not be completed.")
    st.text(str(error))


def show_artifacts(artifacts, *, key_prefix: str) -> None:
    st.subheader("Download review files")
    for label, path, mime in (
        ("PowerPoint deck", artifacts.deck, "application/vnd.openxmlformats-officedocument.presentationml.presentation"),
        ("Review packet", artifacts.packet, "text/markdown"),
        ("Audit log", artifacts.log, "application/json"),
    ):
        try:
            data = path.read_bytes()
        except OSError:
            st.warning(f"{label} is unavailable on disk.")
            continue
        st.download_button(
            label,
            data=data,
            file_name=path.name,
            mime=mime,
            key=f"{key_prefix}-{label}",
        )


def show_flags(flags) -> None:
    if flags:
        st.subheader("Review flags")
        for flag in flags:
            st.text(f"Slide {flag.slide_index + 1} · {flag.note}")


def show_outline(snapshot) -> None:
    if snapshot.outline is None:
        return
    st.subheader("Proposed outline")
    st.caption("Review the timing, objectives, and source locations before approving. Retrieval identifies related material for review.")
    for index, entry in enumerate(snapshot.outline.entries):
        with st.container(border=True):
            st.text(f"{index + 1}. {entry.title} · {entry.time_minutes} min")
            st.text(entry.objective)
            if entry.required_points:
                st.text("Required points to cover")
                for point in entry.required_points:
                    st.text(f"• {point}")
            evidence = snapshot.sources[index] if index < len(snapshot.sources) else None
            if evidence is not None or entry.outside_sources:
                label = RETRIEVAL_STATUS.get(evidence.state, "Source details") if evidence else "Outside source leads"
                with st.expander(label):
                    if evidence is not None:
                        for source in evidence.retrieved:
                            st.text(f"{source.source} · {source.section} · p. {source.pages}")
                        for source in evidence.supplemental:
                            location = f"{source.section} · PDF page {source.pages}" if source.pages else source.section
                            st.text(f"Verified supplemental excerpt · {source.source} · {location}")
                            if source.excerpt:
                                st.text(source.excerpt)
                            if source.url:
                                st.link_button("Open supplemental reference", source.url)
                        for lead in evidence.supplemental_leads:
                            st.text(f"Unverified supplemental lead · {lead}")
                        if evidence.fabricated:
                            st.text("Unverified reference IDs · " + ", ".join(evidence.fabricated))
                        if evidence.unmatched:
                            st.text("Unmatched reference IDs · " + ", ".join(evidence.unmatched))
                    if entry.outside_sources:
                        st.text("Outside source leads to check")
                        for lead in entry.outside_sources:
                            st.text(lead)


def review_in_progress(snapshot) -> bool:
    return snapshot.reviewing_page is not None or snapshot.supplementing_slide is not None


def show_supplemental_lookup(manager: RunManager, snapshot, provider) -> None:
    if snapshot.status != "awaiting_approval" or snapshot.source_extractor is None or provider != "anthropic":
        return
    st.subheader("Supplemental references")
    st.caption("Anthropic uses the slide’s title, objective, and any public reference URL you enter. The CPG remains primary.")
    if snapshot.supplemental_message:
        st.text(snapshot.supplemental_message)
    busy = review_in_progress(snapshot)
    with st.form(f"supplemental-{snapshot.id}-{snapshot.revision}"):
        slide = st.selectbox(
            "Slide to supplement", range(1, snapshot.total_slides + 1),
            format_func=lambda number: f"{number}. {snapshot.outline.entries[number - 1].title}",
            disabled=busy,
        )
        reference_url = st.text_input("Reference URL (optional)", max_chars=500, disabled=busy)
        requested = st.form_submit_button("Find supplemental references", disabled=busy)
    if requested:
        try:
            manager.find_references(snapshot.id, snapshot.revision, slide, reference_url)
        except (RunConflict, KeyError, ValueError) as error:
            show_error(error)
            return
        st.rerun()


def show_review(manager: RunManager, snapshot) -> None:
    if snapshot.status != "awaiting_approval":
        return
    st.subheader("Review decision")
    st.caption("Approval starts slide generation. Revision asks for a new outline. Discard closes this run.")
    busy = review_in_progress(snapshot)
    with st.form(f"review-{snapshot.id}-{snapshot.revision}"):
        feedback = st.text_area(
            "Revision feedback",
            placeholder="Describe what should change in the outline",
            key=f"feedback-{snapshot.id}-{snapshot.revision}",
        )
        approve, revise, discard = st.columns(3)
        with approve:
            approved = st.form_submit_button("Approve outline", type="primary", use_container_width=True,
                                             disabled=busy)
        with revise:
            revised = st.form_submit_button("Request revision", use_container_width=True,
                                            disabled=busy)
        with discard:
            discarded = st.form_submit_button("Discard run", use_container_width=True,
                                             disabled=busy)
    try:
        if approved:
            manager.approve(snapshot.id, snapshot.revision)
        elif revised:
            if not feedback.strip():
                st.warning("Enter feedback before requesting a revision.")
                return
            manager.revise(snapshot.id, snapshot.revision, feedback.strip())
        elif discarded:
            manager.discard(snapshot.id, snapshot.revision)
        else:
            return
    except (RunConflict, KeyError) as error:
        show_error(error)
        return
    st.rerun()


def show_visual_review(manager: RunManager, snapshot, visual_config) -> None:
    if snapshot.source_extractor is None or snapshot.source_pages < 1:
        return
    st.subheader("Optional page check")
    st.caption("An AI checks one original PDF page at a time. Its notes are unreviewed and do not replace the extracted text.")
    checked_pages = {review.page_number for review in snapshot.visual_reviews}
    st.progress(
        min(len(checked_pages) / snapshot.source_pages, 1.0),
        text=f"Original PDF pages checked: {len(checked_pages)} of {snapshot.source_pages}",
    )
    if snapshot.reviewing_page is not None:
        st.info(f"Checking original PDF page {snapshot.reviewing_page}.")
    if snapshot.visual_message:
        st.text(snapshot.visual_message)
    for review in sorted(snapshot.visual_reviews, key=lambda item: item.page_number):
        with st.container(border=True):
            st.text(f"Original PDF page {review.page_number} · Unreviewed AI notes")
            for note in review.missing:
                st.text(f"Possible missing content: {note}")
            for note in review.uncertainties:
                st.text(f"Uncertainty: {note}")
            if not review.missing and not review.uncertainties:
                st.text("No missing content or uncertainty reported by the page check.")
    if snapshot.status != "awaiting_approval":
        return
    if visual_config is None:
        st.info("To enable page checks, set visual.provider and visual.model in config.local.yaml, then restart Streamlit.")
    else:
        st.caption(f"Page check provider: {visual_config['provider']} / {visual_config['model']} · Separate from outline and slide generation.")
        if visual_config["provider"] == "anthropic":
            st.caption("Anthropic receives the rendered page image and extracted page text.")
        elif visual_config["provider"] == "ollama":
            st.caption("Ollama processes the page image and extracted page text locally.")
    disabled = visual_config is None or review_in_progress(snapshot)
    with st.form(f"page-check-{snapshot.id}-{snapshot.revision}"):
        page_number = st.number_input(
            "Original PDF page number", min_value=1, max_value=snapshot.source_pages,
            value=1, step=1, disabled=disabled,
        )
        requested = st.form_submit_button(
            "Check this page", disabled=disabled,
        )
    if requested:
        try:
            manager.review_page(snapshot.id, snapshot.revision, int(page_number))
        except (RunConflict, ValueError, OSError) as error:
            show_error(error)
            return
        st.rerun()


def show_run(manager: RunManager, run_id: str, visual_config, provider) -> None:
    try:
        snapshot = manager.snapshot(run_id)
    except KeyError:
        st.session_state.pop("selected_run_id", None)
        st.warning("This run is no longer available.")
        return

    st.subheader("Current run")
    st.text(f"{snapshot.spec.topic} · {snapshot.spec.duration_minutes} min · {snapshot.spec.audience}")
    st.text(f"Status: {snapshot.status.replace('_', ' ').title()}")
    st.text(f"Source: {snapshot.source_name or 'Indexed documents'}")
    if snapshot.source_extractor is not None:
        st.text(f"Text extractor: {snapshot.source_extractor}")
    for warning in snapshot.source_warnings:
        st.warning(warning)
    if snapshot.message:
        st.text(snapshot.message)
    if snapshot.status == "generating" and snapshot.total_slides:
        st.progress(
            min(snapshot.completed_slides / snapshot.total_slides, 1.0),
            text=f"Slides completed: {snapshot.completed_slides} of {snapshot.total_slides}",
        )
    if snapshot.status == "awaiting_approval":
        show_outline(snapshot)
        show_supplemental_lookup(manager, snapshot, provider)
    show_visual_review(manager, snapshot, visual_config)
    if snapshot.status == "awaiting_approval":
        show_review(manager, snapshot)
    show_flags(snapshot.flags)
    if snapshot.artifacts is not None:
        show_artifacts(snapshot.artifacts, key_prefix=snapshot.id)
    if snapshot.status in {"failed", "discarded"}:
        st.info("This run is closed. You can start another request.")


def request_form(manager: RunManager, *, disabled: bool, source_folder: str) -> None:
    st.subheader("New training request")
    folder = Path(source_folder).expanduser()
    if not folder.is_absolute():
        folder = OUTPUT_ROOT.parent / folder
    source_error = None
    try:
        source_paths = list_source_pdfs(folder)
    except (OSError, ValueError) as error:
        source_paths = ()
        source_error = str(error)
    with st.form("new-request", clear_on_submit=False):
        topic = st.text_input("Topic", placeholder="e.g. Blunt abdominal trauma assessment", disabled=disabled)
        audience = st.text_input("Audience", placeholder="e.g. Combat medics", disabled=disabled)
        duration = st.number_input(
            "Duration in minutes", min_value=2, value=30, step=1, disabled=disabled
        )
        source_mode = st.radio(
            "Source", ("Indexed documents", "One selected PDF"),
            disabled=disabled, horizontal=True,
        )
        selected_source = st.selectbox(
            "Original PDF", source_paths, format_func=lambda path: path.name,
            index=None, disabled=disabled or not source_paths,
            help="Choose a PDF from the configured source folder for a direct-source run.",
        )
        source_extractor = st.selectbox(
            "Selected PDF text extractor", ("pypdf", "PDFium"),
            disabled=disabled,
            help="Used only with One selected PDF. PDFium is an opt-in text extraction pilot; the default is pypdf.",
        )
        submitted = st.form_submit_button("Create outline", type="primary", disabled=disabled)
    if source_error:
        st.warning(f"Source folder is unavailable: {source_error}. Indexed documents remain available.")
    elif not source_paths:
        st.info("No PDFs are available in the source folder. Indexed documents remain available.")
    if not submitted:
        return
    if not topic.strip() or not audience.strip():
        st.warning("Enter a topic and audience.")
        return
    if source_mode == "One selected PDF" and selected_source is None:
        st.warning("Choose an available PDF before creating a direct-source outline.")
        return
    try:
        spec = RequestSpec(
            topic=topic.strip(), audience=audience.strip(), duration_minutes=int(duration)
        )
        if source_mode == "One selected PDF":
            st.session_state.selected_run_id = manager.start(
                spec, source_path=selected_source, source_extractor=source_extractor.lower()
            )
        else:
            st.session_state.selected_run_id = manager.start(spec)
    except (RunConflict, ValueError, OSError) as error:
        show_error(error)
        return
    st.rerun()


def history_panel(manager: RunManager) -> None:
    with st.expander("Completed run history", expanded=False):
        try:
            history = manager.history()
        except OSError as error:
            show_error(error)
            return
        if not history:
            st.text("No completed runs yet.")
            return
        options = {run.id: run for run in history}

        def run_label(run_id):
            run = options[run_id]
            return " · ".join(filter(None, (run.title, SOURCE_LABELS.get(run.source_mode), run.created_at)))

        chosen = st.selectbox(
            "Select a run",
            options=list(options),
            format_func=run_label,
        )
        run = options[chosen]
        show_flags(run.flags)
        show_artifacts(run.artifacts, key_prefix=f"history-{chosen}")


st.title("Eir training review")
st.caption("Local, single-operator preview · Generated material requires subject matter review before teaching.")

try:
    from src.llm import config

    st.text(f"Configured model · {config['provider']} / {config['model']}")
except (OSError, ValueError) as error:
    show_error(error)
    st.stop()

manager = run_manager()
active_id = manager.active_id()
request_form(manager, disabled=active_id is not None, source_folder=config["source_folder"])
if active_id is not None:
    st.session_state.selected_run_id = active_id

selected_id = st.session_state.get("selected_run_id")
if selected_id:
    # Only the run panel refreshes while a worker is active.
    if active_id == selected_id:
        @st.fragment(run_every="2s")
        def live_run_panel() -> None:
            show_run(manager, selected_id, config.get("visual"), config["provider"])
            if manager.active_id() != selected_id:
                st.rerun()

        live_run_panel()
    else:
        show_run(manager, selected_id, config.get("visual"), config["provider"])

history_panel(manager)
