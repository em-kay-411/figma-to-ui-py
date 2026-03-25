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
    run_figma_to_angular,
    generate_from_prompt,
    generate_angular_component,
    refine_with_prompt,
    build_artifact_from_files,
    classify_refine_intent,
    build_component_suggestion_response,
    query_catalog_for_intent,
    compute_ds_coverage,
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


class RefineRequest(BaseModel):
    prompt: str
    screenshot_base64: Optional[str] = None
    # Optional: supply existing file contents to refine (bypasses /generate)
    html_content: Optional[str] = None
    scss_content: Optional[str] = None
    ts_content: Optional[str] = None
    figma_json: Optional[dict] = None
    component_name: Optional[str] = None


# ── Enhancement H: Conversation Router ────────────────────────────────────────

@dataclass
class ChatRouteDecision:
    action: str  # APPLY_REFINE | CLARIFY | SUGGEST_COMPONENTS | RESOLVE_UNRESOLVED | OUT_OF_SCOPE | COVERAGE_WARNING
    clarification_question: Optional[str] = None
    refusal_reason: Optional[str] = None
    intent: Optional[dict] = None


_OUT_OF_SCOPE_PATTERN = re.compile(
    r"\b(NgModule|app\.module|RouterModule|routing|HttpClient|backend service|"
    r"connect to api|rest endpoint|database|auth(?:entication|orization)?)\b",
    re.IGNORECASE,
)


def route_chat_message(prompt: str, session, catalog: Optional[dict]) -> ChatRouteDecision:
    """Route an incoming refine prompt to the appropriate action."""
    # Pending unresolved nodes awaiting clarification (Enhancement D)
    if session.pending_unresolved:
        return ChatRouteDecision(action="RESOLVE_UNRESOLVED")

    # Out-of-scope check
    if _OUT_OF_SCOPE_PATTERN.search(prompt):
        return ChatRouteDecision(
            action="OUT_OF_SCOPE",
            refusal_reason=(
                "This tool generates Angular component UI only. "
                "Backend services, routing, NgModule configuration, and API integration "
                "are out of scope."
            ),
        )

    # Intent classification
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
            return ChatRouteDecision(action="APPLY_REFINE", intent=intent.as_dict())
        except Exception:
            pass

    return ChatRouteDecision(action="APPLY_REFINE")


# ── API endpoints ──────────────────────────────────────────────────────────────

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
):
    s = _get_or_404(session_id)
    has_files = any([html_content, scss_content, ts_content])
    if not figma_json and not screenshot and not prompt and not has_files:
        raise HTTPException(
            400,
            "Provide at least one of figma_json, screenshot, prompt, "
            "or existing file contents (html_content / scss_content / ts_content)",
        )

    figma_data = None
    if figma_json:
        figma_data = json.loads(figma_json.file.read())

    # Build existing_files dict when user supplies file contents
    existing_files = None
    if has_files:
        existing_files = {}
        if html_content:
            existing_files["html"] = html_content
        if ts_content:
            existing_files["typescript"] = ts_content
        if scss_content:
            existing_files["scss"] = scss_content

    # Merge component_name into prompt so codegen picks it up
    effective_prompt = prompt
    if component_name:
        name_hint = f"The component must be named '{component_name}'."
        effective_prompt = f"{name_hint}\n{prompt}" if prompt else name_hint

    screenshot_path = _save_upload_to_tempfile(screenshot) if screenshot else None
    try:
        # Enhancement I: unified entry point for all generate paths
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

    s.current_artifact = artifact
    # Enhancement F: persist DS context
    s.phase1_research_context = pipeline_meta.get("phase1_research_context") or ""
    s.ds_coverage_history.append(pipeline_meta.get("ds_coverage", {}))

    if prompt:
        s.chat_history.append({"role": "user", "content": prompt})

    # Enhancement C: proactive component suggestions for prompt-based generates
    if prompt:
        catalog = load_ds_catalog(s.design_system)
        if catalog:
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

    # Enhancement D: surface unresolved nodes in chat
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

    return _artifact_response(s, artifact, pipeline_meta.get("ds_coverage"))


@app.post("/sessions/{session_id}/refine")
def refine(session_id: str, body: RefineRequest):
    s = _get_or_404(session_id)

    # If user supplies file contents, build an artifact from them
    has_external_files = any([body.html_content, body.scss_content, body.ts_content])
    if has_external_files:
        s.current_artifact = build_artifact_from_files(
            html_content=body.html_content,
            scss_content=body.scss_content,
            ts_content=body.ts_content,
            component_name=body.component_name,
        )

    if not s.current_artifact:
        raise HTTPException(
            400,
            "No code to refine — either call /generate first or supply "
            "html_content / scss_content / ts_content in the request",
        )

    catalog = load_ds_catalog(s.design_system)

    # Enhancement H: route the message
    decision = route_chat_message(body.prompt, s, catalog)

    if decision.action == "OUT_OF_SCOPE":
        s.chat_history.append({"role": "user", "content": body.prompt})
        s.chat_history.append({"role": "assistant", "content": decision.refusal_reason})
        return {
            "session_id": s.session_id,
            "action": "OUT_OF_SCOPE",
            "message": decision.refusal_reason,
            "chat_history": s.chat_history,
        }

    if decision.action == "CLARIFY":
        s.chat_history.append({"role": "user", "content": body.prompt})
        s.chat_history.append({"role": "assistant", "content": decision.clarification_question})
        return {
            "session_id": s.session_id,
            "action": "CLARIFY",
            "message": decision.clarification_question,
            "chat_history": s.chat_history,
        }

    # Reconstruct intent from routing decision if available
    intent = None
    if decision.intent and catalog and s.current_artifact:
        try:
            from figma_to_angular_agent import IntentClassification
            d = decision.intent
            intent = IntentClassification(
                category=d["category"],
                new_components_requested=d.get("new_components_requested", []),
                affected_selectors=d.get("affected_selectors", []),
                requires_catalog_lookup=d.get("requires_catalog_lookup", True),
                requires_doc_research=d.get("requires_doc_research", False),
                confidence=d.get("confidence", 0.5),
            )
        except Exception:
            pass

    screenshot_path = None
    if body.screenshot_base64:
        screenshot_path = _decode_base64_to_tempfile(body.screenshot_base64)
    try:
        artifact, refine_meta = refine_with_prompt(
            current_artifact=s.current_artifact,
            prompt=body.prompt,
            design_system=s.design_system,
            component_mappings=s.component_mappings,
            screenshot_path=screenshot_path,
            intent=intent,
            doc_research_cache=s.doc_research_cache,
            phase1_research_context=s.phase1_research_context,
            figma_json=body.figma_json,
        )
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if screenshot_path and os.path.exists(screenshot_path):
            os.unlink(screenshot_path)

    # Enhancement F: update session DS context
    s.current_artifact = artifact
    s.doc_research_cache = refine_meta.get("doc_research_cache", s.doc_research_cache)
    s.last_intent = refine_meta.get("intent")
    s.ds_coverage_history.append(refine_meta.get("ds_coverage", {}))

    s.chat_history.append({"role": "user", "content": body.prompt})

    # Enhancement C: include suggestion in chat if relevant
    suggestion = refine_meta.get("suggestion_text", "")
    if suggestion:
        s.chat_history.append({
            "role": "assistant",
            "content": suggestion,
            "metadata": {"type": "component_suggestion"},
        })

    s.chat_history.append(
        {"role": "assistant", "content": f"Refined {artifact.component_name}"}
    )

    # Enhancement D: surface new unresolved nodes post-refine
    if artifact.unresolved_nodes:
        s.pending_unresolved = artifact.unresolved_nodes
    else:
        s.pending_unresolved = []

    return _artifact_response(s, artifact, refine_meta.get("ds_coverage"))


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
    """Build the standard artifact response, including DS coverage and unresolved count."""
    return {
        "session_id": s.session_id,
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
