"""Local review interface for Eir training runs."""

from pathlib import Path

import streamlit as st

from src.contracts import RequestSpec
from src.ui_jobs import RunConflict, RunManager


OUTPUT_ROOT = Path(__file__).resolve().parent / "output"
RETRIEVAL_STATUS = {
    "SOURCED": "Related material retrieved",
    "PARTLY SOURCED": "Related material and outside leads",
    "UNSOURCED": "No material retrieved",
}

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
            evidence = snapshot.sources[index] if index < len(snapshot.sources) else None
            if evidence is not None or entry.outside_sources:
                label = RETRIEVAL_STATUS.get(evidence.state, "Source details") if evidence else "Outside source leads"
                with st.expander(label):
                    if evidence is not None:
                        for source in evidence.retrieved:
                            st.text(f"{source.source} · {source.section} · p. {source.pages}")
                        if evidence.fabricated:
                            st.text("Unverified reference IDs · " + ", ".join(evidence.fabricated))
                        if evidence.unmatched:
                            st.text("Unmatched reference IDs · " + ", ".join(evidence.unmatched))
                    if entry.outside_sources:
                        st.text("Outside source leads to check")
                        for lead in entry.outside_sources:
                            st.text(lead)


def show_review(manager: RunManager, snapshot) -> None:
    if snapshot.status != "awaiting_approval":
        return
    st.subheader("Review decision")
    st.caption("Approval starts slide generation. Revision asks for a new outline. Discard closes this run.")
    with st.form(f"review-{snapshot.id}-{snapshot.revision}"):
        feedback = st.text_area(
            "Revision feedback",
            placeholder="Describe what should change in the outline",
            key=f"feedback-{snapshot.id}-{snapshot.revision}",
        )
        approve, revise, discard = st.columns(3)
        with approve:
            approved = st.form_submit_button("Approve outline", type="primary", use_container_width=True)
        with revise:
            revised = st.form_submit_button("Request revision", use_container_width=True)
        with discard:
            discarded = st.form_submit_button("Discard run", use_container_width=True)
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


def show_run(manager: RunManager, run_id: str) -> None:
    try:
        snapshot = manager.snapshot(run_id)
    except KeyError:
        st.session_state.pop("selected_run_id", None)
        st.warning("This run is no longer available.")
        return

    st.subheader("Current run")
    st.text(f"{snapshot.spec.topic} · {snapshot.spec.duration_minutes} min · {snapshot.spec.audience}")
    st.text(f"Status: {snapshot.status.replace('_', ' ').title()}")
    if snapshot.message:
        st.text(snapshot.message)
    if snapshot.status == "generating" and snapshot.total_slides:
        st.progress(
            min(snapshot.completed_slides / snapshot.total_slides, 1.0),
            text=f"Slides completed: {snapshot.completed_slides} of {snapshot.total_slides}",
        )
    if snapshot.status == "awaiting_approval":
        show_outline(snapshot)
        show_review(manager, snapshot)
    show_flags(snapshot.flags)
    if snapshot.artifacts is not None:
        show_artifacts(snapshot.artifacts, key_prefix=snapshot.id)
    if snapshot.status in {"failed", "discarded"}:
        st.info("This run is closed. You can start another request.")


def request_form(manager: RunManager, *, disabled: bool) -> None:
    st.subheader("New training request")
    with st.form("new-request", clear_on_submit=False):
        topic = st.text_input("Topic", placeholder="e.g. Blunt abdominal trauma assessment", disabled=disabled)
        audience = st.text_input("Audience", placeholder="e.g. Combat medics", disabled=disabled)
        duration = st.number_input(
            "Duration in minutes", min_value=2, value=30, step=1, disabled=disabled
        )
        submitted = st.form_submit_button("Create outline", type="primary", disabled=disabled)
    if not submitted:
        return
    if not topic.strip() or not audience.strip():
        st.warning("Enter a topic and audience.")
        return
    try:
        spec = RequestSpec(
            topic=topic.strip(), audience=audience.strip(), duration_minutes=int(duration)
        )
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
        chosen = st.selectbox(
            "Select a run",
            options=list(options),
            format_func=lambda run_id: f"{options[run_id].title} · {options[run_id].created_at}",
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
request_form(manager, disabled=active_id is not None)
if active_id is not None:
    st.session_state.selected_run_id = active_id

selected_id = st.session_state.get("selected_run_id")
if selected_id:
    # Only the run panel refreshes while a worker is active.
    if active_id == selected_id:
        @st.fragment(run_every="2s")
        def live_run_panel() -> None:
            show_run(manager, selected_id)
            if manager.active_id() != selected_id:
                st.rerun()

        live_run_panel()
    else:
        show_run(manager, selected_id)

history_panel(manager)
