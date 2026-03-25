import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
} from 'react';

const API = 'http://localhost:8000';

// ─── Style & Font Injection ───────────────────────────────────────────────────

function useStyleInjection() {
  useEffect(() => {
    // Google Fonts
    const fontsLink = document.createElement('link');
    fontsLink.rel = 'stylesheet';
    fontsLink.href =
      'https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap';
    document.head.appendChild(fontsLink);

    // JSZip
    const jszip = document.createElement('script');
    jszip.src =
      'https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js';
    document.head.appendChild(jszip);

    // Styles
    const style = document.createElement('style');
    style.textContent = `
      :root {
        --bg-primary: #0a0a0a;
        --bg-secondary: #111111;
        --bg-tertiary: #1a1a1a;
        --bg-hover: #222222;
        --border: #2a2a2a;
        --border-active: #3a3a3a;
        --text-primary: #e8e8e8;
        --text-secondary: #888888;
        --text-dim: #444444;
        --accent: #00d4ff;
        --accent-dim: #00d4ff18;
        --warning: #f59e0b;
        --error: #ef4444;
        --success: #22c55e;
        --info: #60a5fa;
        --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
        --font-ui: 'IBM Plex Sans', system-ui, sans-serif;
      }

      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

      body { background: var(--bg-primary); color: var(--text-primary); font-family: var(--font-ui); overflow: hidden; }

      ::-webkit-scrollbar { width: 4px; height: 4px; }
      ::-webkit-scrollbar-track { background: transparent; }
      ::-webkit-scrollbar-thumb { background: var(--border-active); border-radius: 2px; }

      .ide-root {
        width: 100vw; height: 100vh; display: flex; flex-direction: column;
        background: var(--bg-primary);
      }

      /* ── Header ── */
      .ide-header {
        height: 40px; min-height: 40px; display: flex; align-items: center;
        justify-content: space-between; padding: 0 12px;
        background: var(--bg-secondary); border-bottom: 1px solid var(--border);
        font-size: 13px; gap: 12px; user-select: none;
      }
      .ide-header-left { display: flex; align-items: center; gap: 8px; }
      .ide-header-brand { font-family: var(--font-mono); font-weight: 600; color: var(--accent); font-size: 14px; }
      .ide-header-sep { width: 1px; height: 16px; background: var(--border); }
      .ide-header-subtitle { color: var(--text-dim); font-size: 12px; }
      .ide-header-center { display: flex; align-items: center; gap: 10px; }
      .ide-header-center label { color: var(--text-secondary); font-size: 12px; }
      .ide-header-center select {
        background: var(--bg-tertiary); color: var(--text-primary); border: 1px solid var(--border);
        border-radius: 2px; padding: 2px 8px; font-size: 12px; font-family: var(--font-mono);
        outline: none; cursor: pointer;
      }
      .ide-header-center select:focus { border-color: var(--accent); }
      .session-dot {
        width: 6px; height: 6px; border-radius: 50%; display: inline-block;
      }
      .session-dot.active { background: var(--success); }
      .session-dot.inactive { background: var(--error); }
      .session-id { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); }
      .ide-header-right { display: flex; align-items: center; gap: 8px; }
      .ide-header-right .last-active { font-size: 11px; color: var(--text-dim); }

      /* ── Buttons ── */
      .btn {
        font-family: var(--font-ui); font-size: 12px; font-weight: 500;
        border: 1px solid var(--border); border-radius: 2px;
        padding: 4px 12px; cursor: pointer; transition: background 0.15s, border-color 0.15s;
        white-space: nowrap;
      }
      .btn-ghost { background: transparent; color: var(--text-secondary); }
      .btn-ghost:hover { background: var(--bg-hover); color: var(--text-primary); }
      .btn-accent {
        background: var(--accent-dim); color: var(--accent); border-color: var(--accent);
      }
      .btn-accent:hover { background: #00d4ff30; }
      .btn-accent:disabled {
        opacity: 0.4; cursor: not-allowed;
      }
      .btn-accent:disabled:hover { background: var(--accent-dim); }
      .btn-warning { background: transparent; color: var(--warning); border-color: var(--warning); }
      .btn-warning:hover { background: #f59e0b18; }
      .btn-sm { padding: 2px 8px; font-size: 11px; }

      /* ── Body ── */
      .ide-body { display: flex; flex: 1; overflow: hidden; }

      /* ── Vertical Divider ── */
      .v-divider {
        width: 4px; cursor: col-resize; background: var(--border);
        transition: background 0.15s; flex-shrink: 0;
      }
      .v-divider:hover, .v-divider.active { background: var(--accent); }

      /* ── Left Panel ── */
      .left-panel {
        display: flex; flex-direction: column; overflow: hidden;
        background: var(--bg-primary);
      }

      /* Input section */
      .input-section {
        display: flex; flex-direction: column; padding: 12px;
        gap: 8px; overflow-y: auto; flex-shrink: 0;
      }
      .prompt-textarea {
        width: 100%; resize: none; background: var(--bg-tertiary);
        border: 1px solid var(--border); border-radius: 2px;
        color: var(--text-primary); font-family: var(--font-mono);
        font-size: 13px; padding: 8px 10px; outline: none;
        line-height: 1.5;
      }
      .prompt-textarea::placeholder { color: var(--text-dim); }
      .prompt-textarea:focus { border-color: var(--accent); }

      .drop-zone {
        height: 80px; border: 1px dashed var(--border); border-radius: 2px;
        display: flex; align-items: center; justify-content: center;
        font-size: 12px; color: var(--text-dim); cursor: pointer;
        transition: border-color 0.15s, background 0.15s;
        overflow: hidden; position: relative;
      }
      .drop-zone:hover { border-color: var(--text-secondary); background: var(--bg-tertiary); }
      .drop-zone.has-file { border-style: solid; border-color: var(--accent); background: var(--accent-dim); }
      .drop-zone.drag-over { border-color: var(--accent); background: var(--accent-dim); }
      .drop-zone-thumb {
        max-height: 60px; max-width: 100%; object-fit: contain;
      }
      .drop-zone-filename {
        font-family: var(--font-mono); font-size: 11px; color: var(--accent);
        text-overflow: ellipsis; overflow: hidden; white-space: nowrap;
        max-width: 90%; padding: 0 8px;
      }
      .drop-zone-clear {
        position: absolute; top: 4px; right: 4px;
        background: var(--bg-primary); border: 1px solid var(--border);
        color: var(--text-secondary); width: 18px; height: 18px;
        display: flex; align-items: center; justify-content: center;
        font-size: 11px; cursor: pointer; border-radius: 2px; line-height: 1;
      }
      .drop-zone-clear:hover { color: var(--error); border-color: var(--error); }

      .action-row { display: flex; gap: 6px; }

      .hint-text { font-size: 11px; color: var(--text-dim); text-align: center; }

      /* ── Horizontal Divider ── */
      .h-divider {
        height: 4px; cursor: row-resize; background: var(--border);
        transition: background 0.15s; flex-shrink: 0;
      }
      .h-divider:hover, .h-divider.active { background: var(--accent); }

      /* ── Chat Section ── */
      .chat-section {
        flex: 1; overflow-y: auto; display: flex; flex-direction: column;
        padding: 12px; gap: 8px; position: relative;
      }
      .chat-empty {
        flex: 1; display: flex; align-items: center; justify-content: center;
        color: var(--text-dim); font-size: 13px; text-align: center;
        padding: 20px; line-height: 1.6;
      }

      .chat-msg {
        max-width: 88%; padding: 8px 12px; border-radius: 2px;
        font-size: 13px; line-height: 1.5; word-break: break-word;
        white-space: pre-wrap;
      }
      .chat-msg.user {
        align-self: flex-end; background: var(--bg-tertiary);
        border: 1px solid var(--border); color: var(--text-primary);
      }
      .chat-msg.assistant {
        align-self: flex-start; background: var(--bg-secondary);
        border: 1px solid var(--border); color: var(--text-primary);
      }
      .chat-msg.suggestion {
        border-left: 2px solid var(--accent); background: var(--accent-dim);
      }
      .chat-msg.suggestion .chat-tag {
        font-size: 10px; font-weight: 600; text-transform: uppercase;
        color: var(--accent); margin-bottom: 4px; letter-spacing: 0.5px;
      }
      .chat-msg.unresolved {
        border-left: 2px solid var(--warning); background: #f59e0b10;
      }
      .chat-msg.unresolved .chat-tag {
        font-size: 10px; font-weight: 600; text-transform: uppercase;
        color: var(--warning); margin-bottom: 4px; letter-spacing: 0.5px;
      }
      .chat-msg.out-of-scope {
        border-left: 2px solid var(--warning);
      }
      .chat-msg.out-of-scope .chat-icon { color: var(--warning); margin-right: 6px; }
      .chat-msg.clarify {
        border-left: 2px solid var(--info);
      }
      .chat-msg.clarify .chat-icon { color: var(--info); margin-right: 6px; }

      .ds-coverage-bar {
        align-self: flex-start; width: 88%; height: 4px;
        background: var(--bg-tertiary); border-radius: 2px; overflow: hidden;
        position: relative; margin-top: -4px;
      }
      .ds-coverage-fill {
        height: 100%; background: var(--accent); transition: width 0.3s;
      }
      .ds-coverage-label {
        font-size: 10px; color: var(--text-dim); align-self: flex-start;
        margin-top: -2px; font-family: var(--font-mono);
      }

      .generating-pulse {
        align-self: flex-start; display: flex; align-items: center; gap: 6px;
        font-size: 12px; color: var(--text-dim); padding: 8px 0;
      }
      .generating-pulse .dot {
        width: 4px; height: 4px; border-radius: 50%; background: var(--accent);
        animation: pulse 1.2s ease-in-out infinite;
      }
      .generating-pulse .dot:nth-child(2) { animation-delay: 0.2s; }
      .generating-pulse .dot:nth-child(3) { animation-delay: 0.4s; }
      @keyframes pulse {
        0%, 100% { opacity: 0.2; }
        50% { opacity: 1; }
      }

      /* ── Error Banner ── */
      .error-banner {
        background: #ef444418; border: 1px solid var(--error); border-radius: 2px;
        padding: 8px 12px; font-size: 12px; color: var(--error);
        display: flex; align-items: center; justify-content: space-between;
        gap: 8px;
      }
      .error-banner button {
        background: none; border: none; color: var(--error); cursor: pointer;
        font-size: 14px; line-height: 1; padding: 0 2px;
      }

      /* ── Right Panel ── */
      .right-panel {
        flex: 1; display: flex; flex-direction: column; overflow: hidden;
        background: var(--bg-primary);
      }

      .code-topbar {
        height: 36px; min-height: 36px; display: flex; align-items: center;
        justify-content: space-between; padding: 0 12px;
        background: var(--bg-secondary); border-bottom: 1px solid var(--border);
        user-select: none;
      }
      .code-topbar-left { display: flex; align-items: center; gap: 8px; }
      .code-topbar-name {
        font-family: var(--font-mono); font-size: 13px; font-weight: 500;
        color: var(--accent);
      }
      .code-topbar-right { display: flex; align-items: center; gap: 6px; }

      .file-tabs {
        display: flex; height: 32px; min-height: 32px;
        background: var(--bg-secondary); border-bottom: 1px solid var(--border);
        user-select: none;
      }
      .file-tab {
        display: flex; align-items: center; gap: 6px;
        padding: 0 16px; font-size: 12px; font-family: var(--font-mono);
        color: var(--text-secondary); cursor: pointer;
        border-bottom: 2px solid transparent;
        transition: color 0.15s, border-color 0.15s;
      }
      .file-tab:hover { color: var(--text-primary); background: var(--bg-hover); }
      .file-tab.active {
        color: var(--text-primary); border-bottom-color: var(--accent);
      }
      .file-tab-dot {
        width: 6px; height: 6px; border-radius: 50%;
      }
      .file-tab-dot.html { background: var(--info); }
      .file-tab-dot.scss { background: #ec4899; }
      .file-tab-dot.ts { background: #22d3ee; }
      .file-tab-dot.modified { background: var(--warning) !important; }

      /* ── Code Editor ── */
      .code-editor-wrap {
        flex: 1; position: relative; overflow: hidden;
      }
      .code-gutter {
        position: absolute; top: 0; left: 0; width: 48px; height: 100%;
        overflow: hidden; padding: 10px 8px 10px 0;
        text-align: right; font-family: var(--font-mono); font-size: 13px;
        line-height: 1.5; color: var(--text-dim); background: var(--bg-secondary);
        border-right: 1px solid var(--border); user-select: none;
        pointer-events: none; white-space: pre;
      }
      .code-textarea {
        width: 100%; height: 100%; resize: none;
        background: var(--bg-primary); color: var(--text-primary);
        font-family: var(--font-mono); font-size: 13px; line-height: 1.5;
        border: none; outline: none;
        padding: 10px 12px 10px 56px;
        tab-size: 2;
      }
      .code-textarea::placeholder { color: var(--text-dim); }

      .code-statusbar {
        height: 28px; min-height: 28px; display: flex; align-items: center;
        justify-content: space-between; padding: 0 12px;
        background: var(--bg-secondary); border-top: 1px solid var(--border);
        font-size: 11px; color: var(--text-dim); font-family: var(--font-mono);
        user-select: none;
      }
      .code-statusbar-left { display: flex; gap: 12px; }
      .code-statusbar-right { display: flex; gap: 6px; }

      /* ── Empty State ── */
      .code-empty {
        flex: 1; display: flex; flex-direction: column;
        align-items: center; justify-content: center; gap: 16px;
        color: var(--text-dim); font-size: 14px;
      }
      .code-empty svg { opacity: 0.15; }

      /* ── Import button in file tabs ── */
      .file-tab-actions { display: flex; align-items: center; gap: 4px; margin-left: auto; }
      .btn-import {
        font-family: var(--font-mono); font-size: 10px; font-weight: 500;
        background: transparent; color: var(--text-dim); border: 1px solid var(--border);
        border-radius: 2px; padding: 1px 6px; cursor: pointer;
        transition: color 0.15s, border-color 0.15s;
      }
      .btn-import:hover { color: var(--accent); border-color: var(--accent); }

      .code-placeholder {
        position: absolute; top: 10px; left: 56px;
        color: var(--text-dim); font-family: var(--font-mono); font-size: 13px;
        pointer-events: none; user-select: none;
      }
    `;
    document.head.appendChild(style);

    return () => {
      document.head.removeChild(fontsLink);
      document.head.removeChild(jszip);
      document.head.removeChild(style);
    };
  }, []);
}

// ─── Angular Shield SVG ───────────────────────────────────────────────────────

function AngularShield({ size = 48, color = 'currentColor' }) {
  return (
    <svg width={size} height={size} viewBox="0 0 256 272" fill="none">
      <path
        d="M.1 45.522L125.908.697l129.196 44.028-20.919 166.45-108.277 59.966-106.583-59.169L.1 45.522z"
        fill={color}
        opacity={0.85}
      />
      <path
        d="M255.104 44.725L125.908.697v270.444l108.277-59.866 20.919-166.55z"
        fill={color}
        opacity={0.65}
      />
      <path
        d="M126.107 32.274L47.714 206.693l29.285-.498 15.739-39.828h70.681l17.075 40.326 27.628.498-82.015-174.917zm.2 55.882l26.912 55.442h-49.903l22.991-55.442z"
        fill="#fff"
      />
    </svg>
  );
}

// ─── Main IDE Component ───────────────────────────────────────────────────────

export default function IDE() {
  useStyleInjection();

  // ── Design system & session ──
  const [designSystems, setDesignSystems] = useState([]);
  const [selectedDS, setSelectedDS] = useState('');
  const [session, setSession] = useState(null); // { session_id, design_system, created_at, last_active }
  const [isGenerating, setIsGenerating] = useState(false);

  // ── Inputs ──
  const [promptText, setPromptText] = useState('');
  const [screenshotFile, setScreenshotFile] = useState(null);
  const [screenshotBase64, setScreenshotBase64] = useState('');
  const [figmaFile, setFigmaFile] = useState(null);

  // ── Code output ──
  const [files, setFiles] = useState({ html: '', scss: '', ts: '' });
  const [lastGenerated, setLastGenerated] = useState({ html: '', scss: '', ts: '' });
  const [fileModified, setFileModified] = useState({ html: false, scss: false, ts: false });
  const [filePaths, setFilePaths] = useState({ html: '', scss: '', ts: '' });
  const [activeFileTab, setActiveFileTab] = useState('html');
  const [componentName, setComponentName] = useState('');

  // ── Chat ──
  const [chatHistory, setChatHistory] = useState([]);
  const [dsCoverageHistory, setDsCoverageHistory] = useState([]);
  const [lastRefineAction, setLastRefineAction] = useState(null);

  // ── Copy/download states ──
  const [copyAllState, setCopyAllState] = useState('idle');
  const [fileCopyState, setFileCopyState] = useState({ html: 'idle', scss: 'idle', ts: 'idle' });

  // ── Error ──
  const [error, setError] = useState(null);

  // ── Panel sizing ──
  const [leftPanelWidth, setLeftPanelWidth] = useState(38);
  const [inputSectionHeight, setInputSectionHeight] = useState(340);
  const vDragRef = useRef(false);
  const hDragRef = useRef(false);
  const leftPanelRef = useRef(null);

  // ── Refs ──
  const chatEndRef = useRef(null);
  const screenshotInputRef = useRef(null);
  const figmaInputRef = useRef(null);
  const gutterRef = useRef(null);
  const codeTextareaRef = useRef(null);

  // ── Import file refs (per tab) ──
  const importHtmlRef = useRef(null);
  const importScssRef = useRef(null);
  const importTsRef = useRef(null);
  const importRefs = { html: importHtmlRef, scss: importScssRef, ts: importTsRef };

  // Drag-over state for drop zones
  const [screenshotDragOver, setScreenshotDragOver] = useState(false);
  const [figmaDragOver, setFigmaDragOver] = useState(false);

  // ── API Helper ──
  const apiFetch = useCallback(async (path, options = {}) => {
    try {
      const res = await fetch(`${API}${path}`, options);
      if (!res.ok) {
        let detail = `HTTP ${res.status}`;
        try {
          const err = await res.json();
          if (err.detail) detail = err.detail;
        } catch {}
        throw new Error(detail);
      }
      if (res.status === 204) return null;
      return await res.json();
    } catch (e) {
      setError(e.message || 'Request failed');
      throw e;
    }
  }, []);

  // ── Error auto-dismiss ──
  useEffect(() => {
    if (!error) return;
    const t = setTimeout(() => setError(null), 6000);
    return () => clearTimeout(t);
  }, [error]);

  // ── Mount: fetch design systems ──
  useEffect(() => {
    (async () => {
      try {
        const data = await apiFetch('/design-systems');
        if (data?.design_systems?.length) {
          setDesignSystems(data.design_systems);
          setSelectedDS(data.design_systems[0]);
        }
      } catch {}
    })();
  }, [apiFetch]);

  // ── Auto-create session on DS selection ──
  useEffect(() => {
    if (!selectedDS) return;
    let cancelled = false;
    (async () => {
      // Delete existing session
      if (session?.session_id) {
        try { await apiFetch(`/sessions/${session.session_id}`, { method: 'DELETE' }); } catch {}
      }
      try {
        const s = await apiFetch('/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ design_system: selectedDS }),
        });
        if (!cancelled) setSession(s);
      } catch {}
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDS]);

  // ── Chat auto-scroll ──
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatHistory, isGenerating]);

  // ── Gutter scroll sync ──
  const handleCodeScroll = useCallback(() => {
    if (gutterRef.current && codeTextareaRef.current) {
      gutterRef.current.scrollTop = codeTextareaRef.current.scrollTop;
    }
  }, []);

  // ── File type mapping (API "typescript" → state "ts") ──
  const mapFileType = (ft) => (ft === 'typescript' ? 'ts' : ft);

  // ── Extract files from API response ──
  const extractFiles = useCallback((response) => {
    const newFiles = { html: '', scss: '', ts: '' };
    const newPaths = { html: '', scss: '', ts: '' };
    if (response.files) {
      for (const f of response.files) {
        const key = mapFileType(f.file_type);
        if (key in newFiles) {
          newFiles[key] = f.content || '';
          newPaths[key] = f.path || '';
        }
      }
    }
    setFiles(newFiles);
    setLastGenerated({ ...newFiles });
    setFilePaths(newPaths);
    setFileModified({ html: false, scss: false, ts: false });
    if (response.component_name) setComponentName(response.component_name);
    if (response.chat_history) setChatHistory(response.chat_history);
    if (response.ds_coverage) {
      setDsCoverageHistory((prev) => [...prev, response.ds_coverage]);
    }
  }, []);

  // ── Screenshot → base64 ──
  const readFileAsBase64 = (file) =>
    new Promise((resolve) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result.split(',')[1]);
      reader.readAsDataURL(file);
    });

  const handleScreenshotSelect = useCallback(async (file) => {
    if (!file) return;
    setScreenshotFile(file);
    const b64 = await readFileAsBase64(file);
    setScreenshotBase64(b64);
  }, []);

  // ── Generate (unified — handles both fresh generation and refinement) ──
  const handleGenerate = useCallback(async () => {
    if (!session?.session_id) return;
    const hasCodeFiles = files.html || files.scss || files.ts;
    if (!promptText && !screenshotFile && !figmaFile && !hasCodeFiles) return;
    setIsGenerating(true);
    setError(null);
    try {
      const fd = new FormData();
      if (promptText) fd.append('prompt', promptText);
      if (screenshotFile) fd.append('screenshot', screenshotFile);
      if (figmaFile) fd.append('figma_json', figmaFile);
      if (screenshotBase64) fd.append('screenshot_base64', screenshotBase64);
      // Send code editor contents through the full pipeline
      if (files.html) fd.append('html_content', files.html);
      if (files.scss) fd.append('scss_content', files.scss);
      if (files.ts) fd.append('ts_content', files.ts);
      if (componentName) fd.append('component_name', componentName);
      const data = await apiFetch(`/sessions/${session.session_id}/generate`, {
        method: 'POST',
        body: fd,
      });
      setLastRefineAction(data.action || null);
      if (data.action === 'APPLY') {
        extractFiles(data);
      } else {
        // OUT_OF_SCOPE or CLARIFY — only update chat
        if (data.chat_history) setChatHistory(data.chat_history);
      }
      setSession((s) => ({ ...s, last_active: new Date().toISOString() }));
      if (promptText) setPromptText('');
    } catch {} finally {
      setIsGenerating(false);
    }
  }, [session, promptText, screenshotFile, screenshotBase64, figmaFile, files, componentName, apiFetch, extractFiles]);

  // ── New / End Session ──
  const handleNewSession = useCallback(async () => {
    if (!selectedDS) return;
    if (session?.session_id) {
      try { await apiFetch(`/sessions/${session.session_id}`, { method: 'DELETE' }); } catch {}
    }
    try {
      const s = await apiFetch('/sessions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ design_system: selectedDS }),
      });
      setSession(s);
      setFiles({ html: '', scss: '', ts: '' });
      setLastGenerated({ html: '', scss: '', ts: '' });
      setFileModified({ html: false, scss: false, ts: false });
      setFilePaths({ html: '', scss: '', ts: '' });
      setComponentName('');
      setChatHistory([]);
      setDsCoverageHistory([]);
      setLastRefineAction(null);
      setPromptText('');
      setScreenshotFile(null);
      setScreenshotBase64('');
      setFigmaFile(null);
      setError(null);
    } catch {}
  }, [selectedDS, session, apiFetch]);

  const handleEndSession = useCallback(async () => {
    if (session?.session_id) {
      try { await apiFetch(`/sessions/${session.session_id}`, { method: 'DELETE' }); } catch {}
    }
    setSession(null);
    setFiles({ html: '', scss: '', ts: '' });
    setLastGenerated({ html: '', scss: '', ts: '' });
    setFileModified({ html: false, scss: false, ts: false });
    setFilePaths({ html: '', scss: '', ts: '' });
    setComponentName('');
    setChatHistory([]);
    setDsCoverageHistory([]);
    setLastRefineAction(null);
    setPromptText('');
    setScreenshotFile(null);
    setScreenshotBase64('');
    setFigmaFile(null);
    setError(null);
  }, [session, apiFetch]);

  // ── Copy / Download ──
  const handleCopyAll = useCallback(async () => {
    const sep = (path) => `// ${'─'.repeat(3)} ${path} ${'─'.repeat(60 - path.length)}\n`;
    const text = ['html', 'scss', 'ts']
      .filter((k) => files[k])
      .map((k) => sep(filePaths[k] || `component.${k}`) + files[k])
      .join('\n\n');
    await navigator.clipboard.writeText(text);
    setCopyAllState('copied');
    setTimeout(() => setCopyAllState('idle'), 800);
  }, [files, filePaths]);

  const handleCopyFile = useCallback(async (tab) => {
    await navigator.clipboard.writeText(files[tab]);
    setFileCopyState((s) => ({ ...s, [tab]: 'copied' }));
    setTimeout(() => setFileCopyState((s) => ({ ...s, [tab]: 'idle' })), 800);
  }, [files]);

  const handleDownloadAll = useCallback(async () => {
    if (!window.JSZip) { setError('JSZip not loaded yet'); return; }
    const zip = new window.JSZip();
    for (const k of ['html', 'scss', 'ts']) {
      if (files[k]) {
        zip.file(filePaths[k] || `component.${k}`, files[k]);
      }
    }
    const blob = await zip.generateAsync({ type: 'blob' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${componentName || 'component'}.zip`;
    a.click();
    URL.revokeObjectURL(url);
  }, [files, filePaths, componentName]);

  const handleResetFile = useCallback((tab) => {
    setFiles((f) => ({ ...f, [tab]: lastGenerated[tab] }));
    setFileModified((m) => ({ ...m, [tab]: false }));
  }, [lastGenerated]);

  // ── Keyboard shortcuts ──
  const handlePromptKeyDown = useCallback((e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      handleGenerate();
    }
  }, [handleGenerate]);

  // ── Resizable panels ──
  useEffect(() => {
    const onMouseMove = (e) => {
      if (vDragRef.current) {
        const pct = (e.clientX / window.innerWidth) * 100;
        setLeftPanelWidth(Math.min(55, Math.max(25, pct)));
      }
      if (hDragRef.current && leftPanelRef.current) {
        const rect = leftPanelRef.current.getBoundingClientRect();
        const y = e.clientY - rect.top;
        const maxH = rect.height * 0.6;
        setInputSectionHeight(Math.min(maxH, Math.max(220, y)));
      }
    };
    const onMouseUp = () => {
      vDragRef.current = false;
      hDragRef.current = false;
      document.body.style.cursor = '';
      document.body.style.userSelect = '';
    };
    document.addEventListener('mousemove', onMouseMove);
    document.addEventListener('mouseup', onMouseUp);
    return () => {
      document.removeEventListener('mousemove', onMouseMove);
      document.removeEventListener('mouseup', onMouseUp);
    };
  }, []);

  const startVDrag = useCallback(() => {
    vDragRef.current = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, []);

  const startHDrag = useCallback(() => {
    hDragRef.current = true;
    document.body.style.cursor = 'row-resize';
    document.body.style.userSelect = 'none';
  }, []);

  // ── Code editing ──
  const handleCodeChange = useCallback((tab, value) => {
    setFiles((f) => ({ ...f, [tab]: value }));
    setFileModified((m) => ({ ...m, [tab]: true }));
  }, []);

  // ── Drop handlers ──
  const onScreenshotDrop = useCallback((e) => {
    e.preventDefault();
    setScreenshotDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith('image/')) handleScreenshotSelect(file);
  }, [handleScreenshotSelect]);

  const onFigmaDrop = useCallback((e) => {
    e.preventDefault();
    setFigmaDragOver(false);
    const file = e.dataTransfer.files?.[0];
    if (file && (file.type === 'application/json' || file.name.endsWith('.json'))) {
      setFigmaFile(file);
    }
  }, []);

  // ── Import file into editor ──
  const handleImportFile = useCallback((tab) => {
    const ref = importRefs[tab];
    if (ref?.current) ref.current.click();
  }, []);

  const onImportFileSelected = useCallback((tab, file) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
      const content = reader.result;
      setFiles((f) => ({ ...f, [tab]: content }));
      setFileModified((m) => ({ ...m, [tab]: true }));
      // Derive file path from imported filename
      setFilePaths((p) => ({ ...p, [tab]: file.name }));
    };
    reader.readAsText(file);
  }, []);

  // ── Helpers ──
  const hasFiles = files.html || files.scss || files.ts;
  const hasInput = promptText || screenshotFile || figmaFile || hasFiles;
  const lineCount = (text) => (text ? text.split('\n').length : 0);
  const charCount = (text) => (text ? text.length : 0);

  const activeCode = files[activeFileTab];
  const activeLines = lineCount(activeCode);

  // ── Chat message renderer ──
  const renderChatMsg = (msg, i) => {
    const meta = msg.metadata?.type;
    if (msg.role === 'user') {
      return (
        <div key={i} className="chat-msg user">{msg.content}</div>
      );
    }
    if (meta === 'component_suggestion') {
      return (
        <div key={i} className="chat-msg assistant suggestion">
          <div className="chat-tag">Component Suggestion</div>
          {msg.content}
        </div>
      );
    }
    if (meta === 'unresolved_notice') {
      return (
        <div key={i} className="chat-msg assistant unresolved">
          <div className="chat-tag">Unresolved Nodes</div>
          {msg.content}
        </div>
      );
    }
    // Check for action-based messages (last assistant message after refine)
    if (msg.role === 'assistant' && lastRefineAction === 'OUT_OF_SCOPE' && i === chatHistory.length - 1) {
      return (
        <div key={i} className="chat-msg assistant out-of-scope">
          <span className="chat-icon">&#9888;</span>{msg.content}
        </div>
      );
    }
    if (msg.role === 'assistant' && lastRefineAction === 'CLARIFY' && i === chatHistory.length - 1) {
      return (
        <div key={i} className="chat-msg assistant clarify">
          <span className="chat-icon">&#8505;</span>{msg.content}
        </div>
      );
    }
    return (
      <div key={i} className="chat-msg assistant">{msg.content}</div>
    );
  };

  // ── Gutter numbers ──
  const gutterNumbers = Array.from({ length: activeLines || 1 }, (_, i) => i + 1).join('\n');

  // ── Coverage for last entry ──
  const lastCoverage = dsCoverageHistory.length > 0
    ? dsCoverageHistory[dsCoverageHistory.length - 1]
    : null;

  // ── Format last_active timestamp ──
  const formatTime = (iso) => {
    if (!iso) return '';
    try {
      return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch { return ''; }
  };

  // ── File tab labels ──
  const tabLabels = { html: '.html', scss: '.scss', ts: '.ts' };

  // ── Render ──
  return (
    <div className="ide-root">
      {/* ── Header ── */}
      <div className="ide-header">
        <div className="ide-header-left">
          <span style={{ color: 'var(--accent)', fontSize: 16, fontFamily: 'var(--font-mono)' }}>&lt;/&gt;</span>
          <span className="ide-header-brand">ngForge</span>
          <span className="ide-header-sep" />
          <span className="ide-header-subtitle">Angular Component Studio</span>
        </div>

        <div className="ide-header-center">
          <label>Design System:</label>
          <select
            value={selectedDS}
            onChange={(e) => setSelectedDS(e.target.value)}
          >
            {designSystems.map((ds) => (
              <option key={ds} value={ds}>{ds}</option>
            ))}
          </select>
          <span className={`session-dot ${session ? 'active' : 'inactive'}`} />
          <span className="session-id">
            {session?.session_id ? session.session_id.slice(0, 8) : '--------'}
          </span>
        </div>

        <div className="ide-header-right">
          {session?.last_active && (
            <span className="last-active">{formatTime(session.last_active)}</span>
          )}
          <button className="btn btn-ghost btn-sm" onClick={handleNewSession}>
            New Session
          </button>
          <button className="btn btn-ghost btn-sm" onClick={handleEndSession}>
            End Session
          </button>
        </div>
      </div>

      {/* ── Body ── */}
      <div className="ide-body">
        {/* ── Left Panel ── */}
        <div
          className="left-panel"
          ref={leftPanelRef}
          style={{ width: `${leftPanelWidth}%` }}
        >
          {/* Input Section */}
          <div className="input-section" style={{ height: inputSectionHeight }}>
            <textarea
              className="prompt-textarea"
              rows={4}
              placeholder="Describe the Angular component you want to generate..."
              value={promptText}
              onChange={(e) => setPromptText(e.target.value)}
              onKeyDown={handlePromptKeyDown}
            />

            {/* Screenshot drop zone */}
            <div
              className={`drop-zone${screenshotFile ? ' has-file' : ''}${screenshotDragOver ? ' drag-over' : ''}`}
              onClick={() => screenshotInputRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setScreenshotDragOver(true); }}
              onDragLeave={() => setScreenshotDragOver(false)}
              onDrop={onScreenshotDrop}
            >
              {screenshotFile ? (
                <>
                  <img
                    className="drop-zone-thumb"
                    src={URL.createObjectURL(screenshotFile)}
                    alt="screenshot"
                  />
                  <button
                    className="drop-zone-clear"
                    onClick={(e) => {
                      e.stopPropagation();
                      setScreenshotFile(null);
                      setScreenshotBase64('');
                    }}
                  >
                    &times;
                  </button>
                </>
              ) : (
                'Drop screenshot or click to upload'
              )}
            </div>
            <input
              ref={screenshotInputRef}
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={(e) => handleScreenshotSelect(e.target.files?.[0])}
            />

            {/* Figma JSON drop zone */}
            <div
              className={`drop-zone${figmaFile ? ' has-file' : ''}${figmaDragOver ? ' drag-over' : ''}`}
              onClick={() => figmaInputRef.current?.click()}
              onDragOver={(e) => { e.preventDefault(); setFigmaDragOver(true); }}
              onDragLeave={() => setFigmaDragOver(false)}
              onDrop={onFigmaDrop}
            >
              {figmaFile ? (
                <>
                  <span className="drop-zone-filename">{figmaFile.name}</span>
                  <button
                    className="drop-zone-clear"
                    onClick={(e) => { e.stopPropagation(); setFigmaFile(null); }}
                  >
                    &times;
                  </button>
                </>
              ) : (
                'Drop Figma JSON or click to upload'
              )}
            </div>
            <input
              ref={figmaInputRef}
              type="file"
              accept=".json,application/json"
              style={{ display: 'none' }}
              onChange={(e) => setFigmaFile(e.target.files?.[0] || null)}
            />

            {/* Action button */}
            <div className="action-row">
              <button
                className="btn btn-accent"
                style={{ flex: 1 }}
                disabled={!hasInput || isGenerating || !session}
                onClick={handleGenerate}
              >
                {isGenerating
                  ? 'Processing...'
                  : lastGenerated.html || lastGenerated.scss || lastGenerated.ts
                    ? 'Regenerate'
                    : 'Generate'}
              </button>
            </div>

            <div className="hint-text">
              Prompt, screenshot, Figma JSON, or code in editor. {navigator.platform.includes('Mac') ? '⌘' : 'Ctrl'}+Enter to submit.
            </div>
          </div>

          {/* Horizontal divider */}
          <div
            className={`h-divider${hDragRef.current ? ' active' : ''}`}
            onMouseDown={startHDrag}
          />

          {/* Chat Section */}
          <div className="chat-section">
            {error && (
              <div className="error-banner">
                <span>{error}</span>
                <button onClick={() => setError(null)}>&times;</button>
              </div>
            )}

            {chatHistory.length === 0 && !isGenerating ? (
              <div className="chat-empty">
                Upload a screenshot, paste Figma JSON, describe a component, or import existing files to get started.
              </div>
            ) : (
              <>
                {chatHistory.map((msg, i) => {
                  const rendered = [renderChatMsg(msg, i)];
                  // Show coverage bar after assistant messages that have a corresponding coverage entry
                  if (msg.role === 'assistant' && !msg.metadata?.type) {
                    // Find the coverage entry index for this assistant message
                    const assistantIdx = chatHistory
                      .slice(0, i + 1)
                      .filter((m) => m.role === 'assistant' && !m.metadata?.type).length - 1;
                    const cov = dsCoverageHistory[assistantIdx];
                    if (cov) {
                      rendered.push(
                        <div key={`cov-${i}`} className="ds-coverage-bar">
                          <div
                            className="ds-coverage-fill"
                            style={{ width: `${cov.coverage_pct || 0}%` }}
                          />
                        </div>,
                        <div key={`cov-label-${i}`} className="ds-coverage-label">
                          DS Coverage: {(cov.coverage_pct || 0).toFixed(0)}%
                        </div>
                      );
                    }
                  }
                  return rendered;
                })}
              </>
            )}

            {isGenerating && (
              <div className="generating-pulse">
                <div className="dot" />
                <div className="dot" />
                <div className="dot" />
                <span>Processing...</span>
              </div>
            )}

            <div ref={chatEndRef} />
          </div>
        </div>

        {/* ── Vertical Divider ── */}
        <div
          className={`v-divider${vDragRef.current ? ' active' : ''}`}
          onMouseDown={startVDrag}
        />

        {/* ── Right Panel ── */}
        <div className="right-panel">
          {/* Top bar */}
          <div className="code-topbar">
            <div className="code-topbar-left">
              <AngularShield size={16} color="var(--accent)" />
              <span className="code-topbar-name">{componentName || 'Component'}</span>
            </div>
            <div className="code-topbar-right">
              {hasFiles && (
                <>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={handleCopyAll}
                  >
                    {copyAllState === 'copied' ? 'Copied!' : 'Copy All Files'}
                  </button>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={handleDownloadAll}
                  >
                    Download .zip
                  </button>
                </>
              )}
            </div>
          </div>

          {/* File tabs with import buttons */}
          <div className="file-tabs">
            {['html', 'scss', 'ts'].map((tab) => (
              <div
                key={tab}
                className={`file-tab${activeFileTab === tab ? ' active' : ''}`}
                onClick={() => setActiveFileTab(tab)}
              >
                <span
                  className={`file-tab-dot ${tab}${fileModified[tab] ? ' modified' : ''}`}
                />
                {tabLabels[tab]}
              </div>
            ))}
            <div className="file-tab-actions">
              <button
                className="btn-import"
                title={`Import ${activeFileTab.toUpperCase()} file`}
                onClick={(e) => { e.stopPropagation(); handleImportFile(activeFileTab); }}
              >
                Import
              </button>
            </div>
          </div>

          {/* Hidden file inputs for import */}
          <input
            ref={importHtmlRef}
            type="file"
            accept=".html,.htm"
            style={{ display: 'none' }}
            onChange={(e) => { onImportFileSelected('html', e.target.files?.[0]); e.target.value = ''; }}
          />
          <input
            ref={importScssRef}
            type="file"
            accept=".scss,.css"
            style={{ display: 'none' }}
            onChange={(e) => { onImportFileSelected('scss', e.target.files?.[0]); e.target.value = ''; }}
          />
          <input
            ref={importTsRef}
            type="file"
            accept=".ts,.js"
            style={{ display: 'none' }}
            onChange={(e) => { onImportFileSelected('ts', e.target.files?.[0]); e.target.value = ''; }}
          />

          {/* Code editor */}
          <div className="code-editor-wrap">
            <div className="code-gutter" ref={gutterRef}>
              {gutterNumbers}
            </div>
            <textarea
              ref={codeTextareaRef}
              className="code-textarea"
              value={activeCode}
              onChange={(e) => handleCodeChange(activeFileTab, e.target.value)}
              onScroll={handleCodeScroll}
              spellCheck={false}
              placeholder={`Paste or import your ${activeFileTab.toUpperCase()} code here, or generate from the left panel...`}
            />
          </div>

          {/* Status bar */}
          <div className="code-statusbar">
            <div className="code-statusbar-left">
              <span>{activeFileTab.toUpperCase()}</span>
              <span>{charCount(activeCode)} chars</span>
              <span>{activeLines} lines</span>
            </div>
            <div className="code-statusbar-right">
              {activeCode && (
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={() => handleCopyFile(activeFileTab)}
                >
                  {fileCopyState[activeFileTab] === 'copied' ? 'Copied!' : 'Copy'}
                </button>
              )}
              {fileModified[activeFileTab] && (
                <button
                  className="btn btn-warning btn-sm"
                  onClick={() => handleResetFile(activeFileTab)}
                >
                  Reset
                </button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
