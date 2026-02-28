import json
import os
import base64
import tempfile
import threading
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from session_store import SessionStore
from figma_to_angular_agent import (
    run_figma_to_angular,
    generate_from_prompt,
    refine_with_prompt,
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
):
    s = _get_or_404(session_id)
    if not figma_json and not screenshot and not prompt:
        raise HTTPException(
            400, "Provide at least one of figma_json, screenshot, or prompt"
        )

    screenshot_path = _save_upload_to_tempfile(screenshot) if screenshot else None
    try:
        if figma_json:
            figma_data = json.loads(figma_json.file.read())
            figma_screenshots = {"main": screenshot_path} if screenshot_path else None
            artifact = run_figma_to_angular(
                figma_json=figma_data,
                design_system=s.design_system,
                figma_screenshots=figma_screenshots,
            )
        else:
            artifact = generate_from_prompt(
                design_system=s.design_system,
                prompt=prompt,
                screenshot_path=screenshot_path,
            )
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if screenshot_path and os.path.exists(screenshot_path):
            os.unlink(screenshot_path)

    s.current_artifact = artifact
    if prompt:
        s.chat_history.append({"role": "user", "content": prompt})
    s.chat_history.append(
        {"role": "assistant", "content": f"Generated {artifact.component_name}"}
    )
    return _artifact_response(s, artifact)


@app.post("/sessions/{session_id}/refine")
def refine(session_id: str, body: RefineRequest):
    s = _get_or_404(session_id)
    if not s.current_artifact:
        raise HTTPException(
            400, "No generated code in session — call /generate first"
        )

    screenshot_path = None
    if body.screenshot_base64:
        screenshot_path = _decode_base64_to_tempfile(body.screenshot_base64)
    try:
        artifact = refine_with_prompt(
            current_artifact=s.current_artifact,
            prompt=body.prompt,
            design_system=s.design_system,
            component_mappings=s.component_mappings,
            screenshot_path=screenshot_path,
        )
    except Exception as e:
        raise HTTPException(500, str(e))
    finally:
        if screenshot_path and os.path.exists(screenshot_path):
            os.unlink(screenshot_path)

    s.current_artifact = artifact
    s.chat_history.append({"role": "user", "content": body.prompt})
    s.chat_history.append(
        {"role": "assistant", "content": f"Refined {artifact.component_name}"}
    )
    return _artifact_response(s, artifact)


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


def _artifact_response(s, artifact) -> dict:
    return {
        "session_id": s.session_id,
        "component_name": artifact.component_name,
        "files": [f.model_dump() for f in artifact.files],
        "imports": artifact.imports,
        "ds_components_used": [m.model_dump() for m in artifact.ds_components_used],
        "unresolved_nodes": artifact.unresolved_nodes,
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
        "current_files": (
            [f.model_dump() for f in s.current_artifact.files]
            if s.current_artifact
            else []
        ),
    }
