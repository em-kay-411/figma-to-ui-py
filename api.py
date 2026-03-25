import json
import os
import re
import base64
import tempfile
import threading
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from session_store import SessionStore
from figma_to_angular_agent import (
    generate_angular_component,
    classify_refine_intent,
    build_component_suggestion_response,
    query_catalog_for_intent,
    load_ds_catalog,
    METRICS,
)

store = SessionStore()


def _start_cleanup():
    store.cleanup_expired()
    threading.Timer(300, _start_cleanup).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_cleanup()
    yield


app = FastAPI(title="Figma-to-Angular API", version="1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class CreateSessionRequest(BaseModel):
    design_system: str


# ── Conversation Router ───────────────────────────────────────────────────────

@dataclass
class ChatRouteDecision:
    action: str  # APPLY | CLARIFY | RESOLVE_UNRESOLVED | OUT_OF_SCOPE
    clarification_question: Optional[str] = None
    refusal_reason: Optional[str] = None
    intent: Optional[dict] = None


_OUT_OF_SCOPE_PATTERN = re.compile(
    r"\b(NgModule|app\.module|RouterModule|routing|HttpClient|backend service|"
    r"connect to api|rest endpoint|database|auth(?:entication|orization)?)\b",
    re.IGNORECASE,
)


def route_chat_message(prompt: str, session, catalog: Optional[dict]) -> ChatRouteDecision:
    """Route an incoming prompt to the appropriate action."""
    if session.pending_unresolved:
        return ChatRouteDecision(action="RESOLVE_UNRESOLVED")

    if _OUT_OF_SCOPE_PATTERN.search(prompt):
        return ChatRouteDecision(
            action="OUT_OF_SCOPE",
            refusal_reason=(
                "This tool generates Angular component UI only. "
                "Backend services, routing, NgModule configuration, and API integration "
                "are out of scope."
            ),
        )

    if session.current_artifact and catalog:
        try:
            intent = classify_refine_intent(prompt, session.current_artifact, catalog)
            if intent.category == "AMBIGUOUS" and intent.confidence < 0.5:
                return ChatRouteDecision(
                    action="CLARIFY",
                    clarification_question=(
                        "Could you clarify what you'd like to change? "
                        "(layout, colors, components, or logic)"
                    ),
                    intent=intent.as_dict(),
                )
            return ChatRouteDecision(action="APPLY", intent=intent.as_dict())
        except Exception:
            pass

    return ChatRouteDecision(action="APPLY")


# ── API endpoints ─────────────────────────────────────────────────────────────

@app.get("/design-systems")
def list_design_systems():
    return {"design_systems": store.list_design_systems()}


@app.post("/sessions", status_code=201)
def create_session(body: CreateSessionRequest):
    if body.design_system not in store.list_design_systems():
        raise HTTPException(400, f"Unknown design system: {body.design_system}")
    s = store.create(body.design_system)
    return {
        "session_id": s.session_id,
        "design_system": s.design_system,
        "created_at": s.created_at.isoformat(),
    }


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    s = _get_or_404(session_id)
    return _session_response(s)


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str):
    if not store.delete(session_id):
        raise HTTPException(404, "Session not found")


@app.post("/sessions/{session_id}/generate")
def generate(
    session_id: str,
    figma_json: Optional[UploadFile] = File(None),
    screenshot: Optional[UploadFile] = File(None),
    prompt: Optional[str] = Form(None),
    html_content: Optional[str] = Form(None),
    scss_content: Optional[str] = Form(None),
    ts_content: Optional[str] = Form(None),
    component_name: Optional[str] = Form(None),
    screenshot_base64: Optional[str] = Form(None),
):
    """Unified endpoint: generate from scratch or refine existing code.

    Accepts any combination of inputs — prompt, screenshot (file or base64),
    Figma JSON, and/or existing HTML/SCSS/TS file contents.  All inputs run
    through the full pipeline (IR → DS Map → CodeGen → Validate → Repair).
    """
    s = _get_or_404(session_id)
    has_files = any([html_content, scss_content, ts_content])
    has_screenshot = screenshot is not None or bool(screenshot_base64)
    if not figma_json and not has_screenshot and not prompt and not has_files:
        raise HTTPException(
            400,
            "Provide at least one of figma_json, screenshot, prompt, "
            "or existing file contents (html_content / scss_content / ts_content)",
        )

    catalog = load_ds_catalog(s.design_system)

    # ── Chat routing: check OUT_OF_SCOPE / CLARIFY before running pipeline ──
    if prompt:
        decision = route_chat_message(prompt, s, catalog)

        if decision.action == "OUT_OF_SCOPE":
            s.chat_history.append({"role": "user", "content": prompt})
            s.chat_history.append({"role": "assistant", "content": decision.refusal_reason})
            return {
                "session_id": s.session_id,
                "action": "OUT_OF_SCOPE",
                "message": decision.refusal_reason,
                "chat_history": s.chat_history,
            }

        if decision.action == "CLARIFY":
            s.chat_history.append({"role": "user", "content": prompt})
            s.chat_history.append({"role": "assistant", "content": decision.clarification_question})
            return {
                "session_id": s.session_id,
                "action": "CLARIFY",
                "message": decision.clarification_question,
                "chat_history": s.chat_history,
            }

    # ── Build inputs for the pipeline ──
    figma_data = None
    if figma_json:
        figma_data = json.loads(figma_json.file.read())

    existing_files = None
    if has_files:
        existing_files = {}
        if html_content:
            existing_files["html"] = html_content
        if ts_content:
            existing_files["typescript"] = ts_content
        if scss_content:
            existing_files["scss"] = scss_content

    effective_prompt = prompt
    if component_name:
        name_hint = f"The component must be named '{component_name}'."
        effective_prompt = f"{name_hint}\n{prompt}" if prompt else name_hint

    # Screenshot: accept either file upload or base64
    screenshot_path = None
    if screenshot:
        screenshot_path = _save_upload_to_tempfile(screenshot)
    elif screenshot_base64:
        screenshot_path = _decode_base64_to_tempfile(screenshot_base64)

    try:
        artifact, pipeline_meta = generate_angular_component(
            design_system=s.design_system,
            figma_json=figma_data,
            screenshot_path=screenshot_path,
            prompt=effective_prompt,
            existing_files=existing_files,
        )
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if screenshot_path and os.path.exists(screenshot_path):
            os.unlink(screenshot_path)

    # ── Update session state ──
    s.current_artifact = artifact
    s.phase1_research_context = pipeline_meta.get("phase1_research_context") or ""
    s.ds_coverage_history.append(pipeline_meta.get("ds_coverage", {}))

    if prompt:
        s.chat_history.append({"role": "user", "content": prompt})

    # Component suggestions for prompt-based requests
    if prompt and catalog:
        from figma_to_angular_agent import _extract_component_terms
        terms = _extract_component_terms(prompt, catalog)
        if terms:
            top_matches, _ = query_catalog_for_intent(prompt, catalog, terms)
            if top_matches:
                suggestion_text, _, _ = build_component_suggestion_response(catalog, top_matches)
                if suggestion_text:
                    s.chat_history.append({
                        "role": "assistant",
                        "content": suggestion_text,
                        "metadata": {"type": "component_suggestion"},
                    })

    s.chat_history.append(
        {"role": "assistant", "content": f"Generated {artifact.component_name}"}
    )

    # Surface unresolved nodes in chat
    if artifact.unresolved_nodes:
        ds_name = s.design_system
        node_list = "\n".join(
            f"- {n}" for n in artifact.unresolved_nodes[:5]
        )
        notice = {
            "role": "assistant",
            "content": (
                f"Generation complete. However, {len(artifact.unresolved_nodes)} element(s) "
                f"could not be confidently mapped to a {ds_name} component:\n{node_list}\n\n"
                "Describe what each should be and I will re-map them."
            ),
            "metadata": {
                "type": "unresolved_notice",
                "unresolved_nodes": artifact.unresolved_nodes,
            },
        }
        s.chat_history.append(notice)
        s.pending_unresolved = artifact.unresolved_nodes
    else:
        s.pending_unresolved = []

    return _artifact_response(s, artifact, pipeline_meta.get("ds_coverage"))


# ── Internal helpers ──────────────────────────────────────────────────────────

def _get_or_404(session_id: str):
    s = store.get(session_id)
    if not s:
        raise HTTPException(404, "Session not found")
    return s


def _save_upload_to_tempfile(upload: UploadFile) -> str:
    suffix = os.path.splitext(upload.filename or "img.png")[1] or ".png"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(upload.file.read())
        return tmp.name


def _decode_base64_to_tempfile(b64: str) -> str:
    data = base64.b64decode(b64)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".png") as tmp:
        tmp.write(data)
        return tmp.name


def _artifact_response(s, artifact, ds_coverage: Optional[dict] = None) -> dict:
    """Build the standard artifact response."""
    return {
        "session_id": s.session_id,
        "action": "APPLY",
        "component_name": artifact.component_name,
        "files": [f.model_dump() for f in artifact.files],
        "imports": artifact.imports,
        "ds_components_used": [m.model_dump() for m in artifact.ds_components_used],
        "unresolved_nodes": artifact.unresolved_nodes,
        "unresolved_count": len(artifact.unresolved_nodes),
        "ds_coverage": ds_coverage or {},
        "chat_history": s.chat_history,
    }


def _session_response(s) -> dict:
    return {
        "session_id": s.session_id,
        "design_system": s.design_system,
        "created_at": s.created_at.isoformat(),
        "last_active": s.last_active.isoformat(),
        "has_generated_code": s.current_artifact is not None,
        "chat_history": s.chat_history,
        "ds_coverage_history": s.ds_coverage_history,
        "current_files": (
            [f.model_dump() for f in s.current_artifact.files]
            if s.current_artifact
            else []
        ),
    }
