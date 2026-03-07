import uuid
import re
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any

SESSION_TTL_MINUTES = 60


@dataclass
class Session:
    session_id: str
    design_system: str
    created_at: datetime
    last_active: datetime
    figma_json: Optional[Dict] = None
    figma_screenshots: Optional[Dict[str, str]] = None
    component_mappings: Optional[List] = None
    current_artifact: Optional[Any] = None  # GeneratedAngularArtifact
    ir_tree: Optional[List] = None
    ds_catalog_entries: Optional[List] = None
    chat_history: List[Dict] = field(default_factory=list)
    # DS context for multi-turn continuity (Enhancement F)
    doc_research_cache: Dict[str, str] = field(default_factory=dict)
    ds_coverage_history: List[dict] = field(default_factory=list)
    change_log: List[dict] = field(default_factory=list)
    pending_suggestion: Optional[dict] = None
    pending_unresolved: List[dict] = field(default_factory=list)
    phase1_research_context: Optional[str] = None
    last_intent: Optional[dict] = None


class SessionStore:
    def __init__(self):
        self._sessions: Dict[str, Session] = {}

    def create(self, design_system: str) -> Session:
        sid = str(uuid.uuid4())
        now = datetime.utcnow()
        s = Session(
            session_id=sid,
            design_system=design_system,
            created_at=now,
            last_active=now,
        )
        self._sessions[sid] = s
        return s

    def get(self, session_id: str) -> Optional[Session]:
        s = self._sessions.get(session_id)
        if s:
            s.last_active = datetime.utcnow()
        return s

    def delete(self, session_id: str) -> bool:
        return bool(self._sessions.pop(session_id, None))

    def cleanup_expired(self):
        cutoff = datetime.utcnow() - timedelta(minutes=SESSION_TTL_MINUTES)
        for sid in [k for k, v in self._sessions.items() if v.last_active < cutoff]:
            del self._sessions[sid]

    def list_design_systems(self) -> List[str]:
        """Return names of all *_catalog.json files under design_systems/."""
        ds_dir = os.path.join(os.path.dirname(__file__), "design_systems")
        return sorted(
            m.group(1)
            for f in os.listdir(ds_dir)
            if (m := re.match(r"^(.+)_catalog\.json$", f))
        )
