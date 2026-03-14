import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
} from 'react';

const API = 'http://localhost:8000';

export default function NgForgeIDE() {
  // ── Session ───────────────────────────────────────────────────────────────
  const [designSystems, setDesignSystems] = useState([]);
  const [selectedDS, setSelectedDS] = useState('');
  const [session, setSession] = useState(null);
  const [isGenerating, setIsGenerating] = useState(false);

  // ── Editor ────────────────────────────────────────────────────────────────
  const [files, setFiles] = useState({ html: '', scss: '', ts: '' });
  const [lastGenerated, setLastGenerated] = useState({ html: '', scss: '', ts: '' });
  const [activeFileTab, setActiveFileTab] = useState('html');
  const [fileModified, setFileModified] = useState({ html: false, scss: false, ts: false });

  // ── Chat inputs ───────────────────────────────────────────────────────────
  const [prompt, setPrompt] = useState('');
  const [figmaJsonText, setFigmaJsonText] = useState('');
  const [figmaFile, setFigmaFile] = useState(null);
  const [screenshotFile, setScreenshotFile] = useState(null);
  const [screenshotBase64, setScreenshotBase64] = useState(null);
  const [screenshotPreviewUrl, setScreenshotPreviewUrl] = useState(null);
  const [activeInputTab, setActiveInputTab] = useState('prompt');
  const [chatHistory, setChatHistory] = useState([]);
  const [dsCoverage, setDsCoverage] = useState(null);

  // ── Preview ───────────────────────────────────────────────────────────────
  const [previewViewport, setPreviewViewport] = useState('desktop');
  const [previewSrcdoc, setPreviewSrcdoc] = useState('');

  // ── Errors + state ────────────────────────────────────────────────────────
  const [errorLog, setErrorLog] = useState([]);
  const [errorConsoleOpen, setErrorConsoleOpen] = useState(false);
  const [lastRefineAction, setLastRefineAction] = useState(null);
  const [generationFlash, setGenerationFlash] = useState(false);

  // ── Resizable panels ──────────────────────────────────────────────────────
  const [leftWidth, setLeftWidth] = useState(25);
  const [centerWidth, setCenterWidth] = useState(42);
  const [dragging, setDragging] = useState(null);

  // ── Refs ──────────────────────────────────────────────────────────────────
  const chatListRef = useRef(null);
  const iframeRef = useRef(null);
  const dragStartRef = useRef(null);
  const gutterRef = useRef(null);
  const textareaEditorRef = useRef(null);

  // ═══════════════════════════════════════════════════════════════════════════
  // 1. CSS + FONT INJECTION
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    // Google Fonts
    const fonts = [
      "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap",
      "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500&display=swap",
    ];
    fonts.forEach(href => {
      if (!document.querySelector(`link[href="${href}"]`)) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = href;
        document.head.appendChild(link);
      }
    });

    const style = document.createElement('style');
    style.textContent = `
      :root {
        --bg-primary:    #0d0d0d;
        --bg-secondary:  #111111;
        --bg-tertiary:   #1a1a1a;
        --bg-panel:      #121212;
        --border:        #222222;
        --border-active: #333333;
        --text-primary:  #e8e8e8;
        --text-secondary:#a0a0a0;
        --text-dim:      #555555;
        --accent:        #00d4aa;
        --accent-dim:    #00a07a;
        --warning:       #f0a020;
        --error:         #e05050;
        --success:       #40c070;
        --info:          #4090e0;
        --font-mono:     'JetBrains Mono', 'Fira Code', monospace;
        --font-sans:     'IBM Plex Sans', system-ui, sans-serif;
      }
      *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
      html, body { height: 100%; overflow: hidden; background: var(--bg-primary); color: var(--text-primary); font-family: var(--font-sans); }
      ::-webkit-scrollbar { width: 4px; height: 4px; }
      ::-webkit-scrollbar-track { background: transparent; }
      ::-webkit-scrollbar-thumb { background: #333; border-radius: 2px; }
      ::-webkit-scrollbar-thumb:hover { background: #444; }

      @keyframes editor-flash-anim {
        0%   { box-shadow: 0 0 0 1px var(--accent); }
        100% { box-shadow: 0 0 0 1px transparent; }
      }
      .editor-flash { animation: editor-flash-anim 800ms ease-out forwards; }

      @keyframes gen-pulse-anim {
        0%, 100% { box-shadow: 0 0 0 1px var(--accent-dim); }
        50%       { box-shadow: 0 0 0 2px var(--accent); }
      }
      .generating-pulse { animation: gen-pulse-anim 1.2s ease-in-out infinite; }

      @keyframes blink { 0%,100% { opacity:1; } 50% { opacity:0; } }
      .blink { animation: blink 1s step-end infinite; }

      /* Header */
      .ide-header {
        height: 40px; min-height: 40px;
        display: flex; align-items: center; justify-content: space-between;
        padding: 0 12px;
        background: var(--bg-secondary);
        border-bottom: 1px solid var(--border);
        flex-shrink: 0;
        gap: 12px;
      }
      .ide-header-left  { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
      .ide-header-right { display: flex; align-items: center; gap: 8px; flex-shrink: 0; }
      .ide-logo { font-family: var(--font-mono); font-size: 13px; font-weight: 500; color: var(--accent); letter-spacing: 1px; }
      .ide-ds-select {
        background: var(--bg-tertiary); color: var(--text-primary);
        border: 1px solid var(--border); padding: 3px 8px;
        font-family: var(--font-mono); font-size: 12px;
        cursor: pointer; outline: none;
      }
      .ide-ds-select:focus { border-color: var(--border-active); }
      .session-chip {
        display: flex; align-items: center; gap: 6px;
        font-family: var(--font-mono); font-size: 11px;
        color: var(--text-dim); background: var(--bg-tertiary);
        border: 1px solid var(--border); padding: 2px 8px;
      }
      .status-dot {
        width: 8px; height: 8px; border-radius: 50%;
        background: var(--error); flex-shrink: 0;
      }
      .status-dot.active { background: var(--success); }
      .btn {
        background: var(--bg-tertiary); color: var(--text-secondary);
        border: 1px solid var(--border); padding: 4px 10px;
        font-family: var(--font-sans); font-size: 12px;
        cursor: pointer; border-radius: 2px;
        transition: border-color .15s;
      }
      .btn:hover { border-color: var(--border-active); color: var(--text-primary); }
      .btn:disabled { opacity: .4; cursor: not-allowed; }
      .btn-danger:hover { border-color: var(--error); color: var(--error); }

      /* Main layout */
      .ide-main { display: flex; flex: 1; overflow: hidden; min-height: 0; }

      /* Drag handle */
      .drag-handle {
        width: 4px; flex-shrink: 0;
        background: var(--border); cursor: col-resize;
        transition: background .15s;
      }
      .drag-handle:hover, .drag-handle.dragging { background: var(--border-active); }

      /* Panels */
      .panel { display: flex; flex-direction: column; overflow: hidden; min-width: 0; }

      /* Chat panel */
      .chat-panel { background: var(--bg-panel); }
      .chat-input-tabs { display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }
      .chat-tab {
        padding: 8px 16px; font-size: 12px; cursor: pointer;
        color: var(--text-dim); border-bottom: 2px solid transparent;
        white-space: nowrap;
      }
      .chat-tab.active { color: var(--text-primary); border-bottom-color: var(--accent); }
      .chat-tab:hover:not(.active) { color: var(--text-secondary); }
      .chat-input-area { padding: 8px; flex-shrink: 0; border-bottom: 1px solid var(--border); }
      .chat-textarea {
        width: 100%; background: var(--bg-tertiary); color: var(--text-primary);
        border: 1px solid var(--border); padding: 8px;
        font-family: var(--font-mono); font-size: 12px;
        resize: none; outline: none; line-height: 1.5;
      }
      .chat-textarea:focus { border-color: var(--border-active); }
      .chat-textarea:disabled { opacity: .5; }
      .chat-btns { display: flex; gap: 6px; padding: 8px; flex-shrink: 0; }
      .btn-generate {
        flex: 1; background: var(--accent); color: #000; border: none;
        padding: 7px 12px; font-family: var(--font-sans); font-size: 13px; font-weight: 500;
        cursor: pointer; border-radius: 2px; transition: background .15s;
      }
      .btn-generate:hover { background: var(--accent-dim); }
      .btn-generate:disabled { opacity: .4; cursor: not-allowed; background: var(--accent); }
      .btn-refine {
        background: var(--bg-tertiary); color: var(--text-secondary);
        border: 1px solid var(--border); padding: 7px 12px;
        font-size: 12px; cursor: pointer; border-radius: 2px; white-space: nowrap;
      }
      .btn-refine:hover { border-color: var(--accent); color: var(--accent); }
      .btn-refine:disabled { opacity: .4; cursor: not-allowed; }
      .generating-status {
        padding: 4px 8px 8px 8px; font-family: var(--font-mono); font-size: 12px;
        color: var(--accent); flex-shrink: 0;
      }
      .chat-history { flex: 1; overflow-y: auto; padding: 8px; display: flex; flex-direction: column; gap: 8px; }
      .chat-bubble {
        max-width: 90%; padding: 8px 10px; font-size: 12px; line-height: 1.5;
        word-break: break-word;
      }
      .chat-bubble.user { align-self: flex-end; background: var(--bg-tertiary); color: var(--text-primary); border-left: 2px solid var(--border-active); }
      .chat-bubble.assistant { align-self: flex-start; background: var(--bg-secondary); color: var(--text-secondary); border-left: 2px solid var(--border); }
      .chat-bubble.suggestion { border-left-color: var(--accent); color: var(--text-primary); }
      .chat-bubble.warning-msg { border-left-color: var(--warning); color: var(--warning); }
      .chat-bubble.clarify-msg { border-left-color: var(--info); color: var(--info); }
      .chat-bubble.oos-msg { border-left-color: var(--warning); color: var(--warning); }
      .coverage-bar-wrap { padding: 4px 8px; flex-shrink: 0; border-top: 1px solid var(--border); }
      .coverage-label { font-family: var(--font-mono); font-size: 11px; color: var(--text-dim); margin-bottom: 4px; }
      .coverage-track { height: 3px; background: var(--bg-tertiary); border-radius: 1px; overflow: hidden; }
      .coverage-fill { height: 100%; background: var(--accent); transition: width .4s; }
      .coverage-uncovered { font-family: var(--font-mono); font-size: 10px; color: var(--text-dim); margin-top: 2px; }

      /* Drop zone (screenshot) */
      .drop-zone {
        border: 1px dashed var(--border); padding: 20px 12px;
        text-align: center; cursor: pointer; color: var(--text-dim); font-size: 12px;
        margin-bottom: 6px;
      }
      .drop-zone.drag-over { border-color: var(--accent); color: var(--accent); }
      .screenshot-preview { max-width: 100%; max-height: 120px; display: block; margin: 0 auto 6px; border: 1px solid var(--border); }

      /* Editor panel */
      .editor-panel { background: var(--bg-primary); }
      .editor-toolbar {
        display: flex; align-items: center; justify-content: space-between;
        padding: 4px 8px; border-bottom: 1px solid var(--border);
        background: var(--bg-secondary); flex-shrink: 0; height: 32px;
      }
      .editor-toolbar-left, .editor-toolbar-right { display: flex; align-items: center; gap: 6px; }
      .editor-file-tabs { display: flex; border-bottom: 1px solid var(--border); flex-shrink: 0; }
      .editor-tab {
        padding: 8px 16px; font-family: var(--font-mono); font-size: 12px;
        cursor: pointer; color: var(--text-dim);
        border-bottom: 2px solid transparent; white-space: nowrap;
        display: flex; align-items: center; gap: 5px;
      }
      .editor-tab.active { color: var(--text-primary); border-bottom-color: var(--accent); }
      .editor-tab:hover:not(.active) { color: var(--text-secondary); }
      .modified-dot { color: var(--warning); font-size: 14px; line-height: 1; }
      .editor-body { display: flex; flex: 1; overflow: hidden; min-height: 0; position: relative; }
      .line-gutter {
        width: 40px; flex-shrink: 0;
        background: var(--bg-secondary); color: var(--text-dim);
        font-family: var(--font-mono); font-size: 13px; line-height: 1.6;
        padding: 8px 0; text-align: right; padding-right: 8px;
        overflow: hidden; user-select: none;
      }
      .editor-textarea {
        flex: 1; background: #0d0d0d; color: var(--text-primary);
        border: none; outline: none; resize: none;
        font-family: var(--font-mono); font-size: 13px; line-height: 1.6;
        padding: 8px; tab-size: 2; white-space: pre;
        overflow-wrap: normal; overflow-x: auto;
        caret-color: var(--accent);
      }
      .editor-status {
        display: flex; align-items: center; gap: 12px;
        padding: 4px 10px; border-top: 1px solid var(--border);
        background: var(--bg-secondary); flex-shrink: 0;
        font-family: var(--font-mono); font-size: 11px; color: var(--text-dim);
      }
      .modified-indicator { color: var(--warning); }
      .synced-indicator  { color: var(--success); }
      .editor-empty {
        position: absolute; inset: 0; display: flex; align-items: center; justify-content: center;
        font-family: var(--font-mono); font-size: 13px; color: var(--text-dim);
        pointer-events: none;
      }

      /* Preview panel */
      .preview-panel { background: var(--bg-primary); }
      .preview-toolbar {
        display: flex; align-items: center; justify-content: space-between;
        padding: 4px 8px; border-bottom: 1px solid var(--border);
        background: var(--bg-secondary); flex-shrink: 0; height: 32px;
      }
      .viewport-btns { display: flex; gap: 2px; }
      .viewport-btn {
        padding: 3px 9px; font-size: 11px; cursor: pointer;
        background: transparent; color: var(--text-dim);
        border: 1px solid transparent;
      }
      .viewport-btn.active { color: var(--accent); border-color: var(--border); background: var(--bg-tertiary); }
      .viewport-btn:hover:not(.active) { color: var(--text-secondary); }
      .preview-body { flex: 1; overflow: auto; display: flex; flex-direction: column; min-height: 0; position: relative; }
      .preview-iframe-wrap { flex: 1; display: flex; overflow: hidden; min-height: 0; }
      .preview-empty {
        position: absolute; inset: 0;
        display: flex; align-items: center; justify-content: center;
        font-family: var(--font-mono); font-size: 13px; color: var(--text-dim);
        background: repeating-linear-gradient(45deg, #111 0, #111 10px, #0a0a0a 10px, #0a0a0a 20px);
      }
      .error-console { flex-shrink: 0; border-top: 1px solid var(--border); display: flex; flex-direction: column; }
      .error-console-header {
        display: flex; align-items: center; justify-content: space-between;
        padding: 4px 10px; cursor: pointer; background: var(--bg-secondary);
        font-size: 12px; flex-shrink: 0; user-select: none;
      }
      .error-console-header:hover { background: var(--bg-tertiary); }
      .error-console-body { height: 180px; background: #080808; overflow-y: auto; padding: 6px 0; }
      .error-entry {
        display: flex; align-items: flex-start; gap: 8px;
        padding: 3px 10px; font-family: var(--font-mono); font-size: 11px;
        line-height: 1.5;
      }
      .error-ts { color: var(--text-dim); flex-shrink: 0; }
      .error-badge {
        padding: 0 5px; font-size: 10px; border-radius: 2px;
        flex-shrink: 0; margin-top: 1px;
      }
      .error-badge.api  { background: rgba(224,80,80,.2);  color: var(--error); }
      .error-badge.scss { background: rgba(240,160,32,.2); color: var(--warning); }
      .error-badge.js   { background: rgba(240,120,32,.2); color: #f07820; }
      .error-msg { color: var(--text-secondary); word-break: break-all; }
      .error-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--error); flex-shrink: 0; }
    `;
    document.head.appendChild(style);
    return () => { document.head.removeChild(style); };
  }, []);

  // ═══════════════════════════════════════════════════════════════════════════
  // 2. HELPERS
  // ═══════════════════════════════════════════════════════════════════════════
  const pushError = useCallback((type, message) => {
    const ts = new Date().toTimeString().slice(0, 8);
    setErrorLog(prev => [...prev, { id: Date.now() + Math.random(), timestamp: ts, type, message }]);
    setErrorConsoleOpen(true);
  }, []);

  const apiFetch = useCallback(async (url, options = {}) => {
    try {
      const res = await fetch(url, options);
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        pushError('API', `[${res.status}] ${err.detail || JSON.stringify(err)}`);
        return null;
      }
      return await res.json();
    } catch (e) {
      pushError('API', e.message);
      return null;
    }
  }, [pushError]);

  const extractFiles = useCallback((responseFiles) => {
    const map = { html: '', scss: '', ts: '' };
    (responseFiles || []).forEach(f => {
      if (f.file_type === 'html') map.html = f.content;
      else if (f.file_type === 'scss') map.scss = f.content;
      else if (f.file_type === 'typescript') map.ts = f.content;
    });
    return map;
  }, []);

  const flashEditor = useCallback(() => {
    setGenerationFlash(true);
    setTimeout(() => setGenerationFlash(false), 800);
  }, []);

  // ═══════════════════════════════════════════════════════════════════════════
  // 3. SESSION LIFECYCLE
  // ═══════════════════════════════════════════════════════════════════════════
  // Load design systems on mount
  useEffect(() => {
    apiFetch(`${API}/design-systems`).then(data => {
      if (data) {
        const systems = data.design_systems || data || [];
        setDesignSystems(systems);
        if (systems.length > 0) setSelectedDS(systems[0]);
      }
    });
  }, [apiFetch]);

  const clearSessionState = useCallback(() => {
    setFiles({ html: '', scss: '', ts: '' });
    setLastGenerated({ html: '', scss: '', ts: '' });
    setFileModified({ html: false, scss: false, ts: false });
    setChatHistory([]);
    setErrorLog([]);
    setLastRefineAction(null);
    setDsCoverage(null);
    setPreviewSrcdoc('');
    setPrompt('');
    setFigmaJsonText('');
    setFigmaFile(null);
    setScreenshotFile(null);
    setScreenshotBase64(null);
    setScreenshotPreviewUrl(null);
  }, []);

  const createSession = useCallback(async (ds) => {
    const data = await apiFetch(`${API}/sessions`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ design_system: ds }),
    });
    if (data) setSession(data);
    return data;
  }, [apiFetch]);

  const deleteSession = useCallback(async (id) => {
    if (!id) return;
    await apiFetch(`${API}/sessions/${id}`, { method: 'DELETE' });
  }, [apiFetch]);

  // When selectedDS changes, tear down old session and create new one
  useEffect(() => {
    if (!selectedDS) return;
    let cancelled = false;
    const prevId = session?.session_id;
    const run = async () => {
      if (prevId) await deleteSession(prevId);
      if (!cancelled) {
        clearSessionState();
        await createSession(selectedDS);
      }
    };
    run();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedDS]);

  const handleNewSession = useCallback(async () => {
    if (session?.session_id) await deleteSession(session.session_id);
    clearSessionState();
    setSession(null);
    await createSession(selectedDS);
  }, [session, selectedDS, deleteSession, clearSessionState, createSession]);

  const handleEndSession = useCallback(async () => {
    if (session?.session_id) await deleteSession(session.session_id);
    setSession(null);
    clearSessionState();
  }, [session, deleteSession, clearSessionState]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 4. GENERATE HANDLER
  // ═══════════════════════════════════════════════════════════════════════════
  const handleGenerate = useCallback(async () => {
    if (!session) return;
    const fd = new FormData();
    if (figmaFile) fd.append('figma_json', figmaFile, 'figma.json');
    if (screenshotFile) fd.append('screenshot', screenshotFile, screenshotFile.name);
    if (prompt.trim()) fd.append('prompt', prompt.trim());
    if (![...fd.entries()].length) return;

    setIsGenerating(true);
    // DO NOT set Content-Type — browser sets it with boundary
    const data = await apiFetch(`${API}/sessions/${session.session_id}/generate`, {
      method: 'POST',
      body: fd,
    });
    if (data) {
      const f = extractFiles(data.files);
      setFiles(f);
      setLastGenerated(f);
      setFileModified({ html: false, scss: false, ts: false });
      setChatHistory(data.chat_history || []);
      setSession(prev => ({ ...prev, has_generated_code: true }));
      if (data.ds_coverage) setDsCoverage(data.ds_coverage);
      flashEditor();
    }
    setIsGenerating(false);
  }, [session, figmaFile, screenshotFile, prompt, apiFetch, extractFiles, flashEditor]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 5. REFINE HANDLER
  // ═══════════════════════════════════════════════════════════════════════════
  const handleRefine = useCallback(async () => {
    if (!session || !prompt.trim()) return;
    const body = { prompt: prompt.trim() };
    if (screenshotBase64) body.screenshot_base64 = screenshotBase64;

    setIsGenerating(true);
    setLastRefineAction(null);

    const data = await apiFetch(`${API}/sessions/${session.session_id}/refine`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    if (data) {
      const action = data.action;
      if (!action || action === 'APPLY_REFINE' || action === 'RESOLVE_UNRESOLVED') {
        const f = extractFiles(data.files);
        setFiles(f);
        setLastGenerated(f);
        setFileModified({ html: false, scss: false, ts: false });
        if (data.ds_coverage) setDsCoverage(data.ds_coverage);
        flashEditor();
        setLastRefineAction('APPLY_REFINE');
      } else {
        setLastRefineAction(action);
      }
      setChatHistory(data.chat_history || []);
    }
    setIsGenerating(false);
  }, [session, prompt, screenshotBase64, apiFetch, extractFiles, flashEditor]);

  // Cmd/Ctrl+Enter in prompt textarea
  const handlePromptKeyDown = useCallback((e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      if (session?.has_generated_code) {
        if (prompt.trim() && !isGenerating) handleRefine();
      } else {
        if (!isGenerating) handleGenerate();
      }
    }
  }, [session, prompt, isGenerating, handleGenerate, handleRefine]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 6. PREVIEW SRCDOC BUILDER
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    if (!files.html && !files.scss) { setPreviewSrcdoc(''); return; }
    const scss = (files.scss || '').replace(/`/g, '\\`').replace(/\$/g, '\\$');
    const html = files.html || '';
    const srcdoc = `<!DOCTYPE html>
<html><head>
<meta charset="UTF-8">
<script src="https://cdnjs.cloudflare.com/ajax/libs/sass.js/0.11.1/sass.sync.min.js"><\/script>
<style id="s"></style>
</head><body>
${html}
<script>
Sass.compile(\`${scss}\`, function(r){
  if(r.status===0){ document.getElementById('s').textContent=r.text; }
  else { window.parent.postMessage({type:'iframe-error',message:r.formatted||r.message},'*'); }
});
window.onerror=function(m,s,l){ window.parent.postMessage({type:'iframe-error',message:m,line:l},'*'); return true; };
<\/script>
</body></html>`;
    setPreviewSrcdoc(srcdoc);
  }, [files.html, files.scss]);

  // Iframe message listener
  useEffect(() => {
    const handler = (e) => {
      if (e.data?.type === 'iframe-error') {
        pushError('SCSS', e.data.message + (e.data.line ? ` (line ${e.data.line})` : ''));
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [pushError]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 7. RESIZABLE PANELS
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    if (!dragging) return;
    const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

    const onMove = (e) => {
      const { x, leftWidth: l0, centerWidth: c0 } = dragStartRef.current;
      const delta = (e.clientX - x) / window.innerWidth * 100;
      if (dragging === 'left') {
        const newLeft = clamp(l0 + delta, 15, 60);
        const newCenter = clamp(c0 - delta, 15, 60);
        if (newLeft + newCenter <= 85) {
          setLeftWidth(newLeft);
          setCenterWidth(newCenter);
        }
      } else {
        const newCenter = clamp(c0 + delta, 15, 60);
        const right = 100 - l0 - newCenter;
        if (right >= 15 && right <= 60) setCenterWidth(newCenter);
      }
    };
    const onUp = () => {
      setDragging(null);
      document.body.style.cursor = '';
    };
    document.body.style.cursor = 'col-resize';
    window.addEventListener('mousemove', onMove);
    window.addEventListener('mouseup', onUp);
    return () => {
      window.removeEventListener('mousemove', onMove);
      window.removeEventListener('mouseup', onUp);
      document.body.style.cursor = '';
    };
  }, [dragging]);

  const startDrag = useCallback((side, e) => {
    e.preventDefault();
    dragStartRef.current = { x: e.clientX, leftWidth, centerWidth };
    setDragging(side);
  }, [leftWidth, centerWidth]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 8. CHAT AUTO-SCROLL
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    if (chatListRef.current) {
      chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
    }
  }, [chatHistory]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 9. EDITOR SCROLL SYNC
  // ═══════════════════════════════════════════════════════════════════════════
  const syncScroll = useCallback((e) => {
    if (gutterRef.current) gutterRef.current.scrollTop = e.target.scrollTop;
  }, []);

  // ═══════════════════════════════════════════════════════════════════════════
  // SCREENSHOT HANDLER
  // ═══════════════════════════════════════════════════════════════════════════
  const handleScreenshotFile = useCallback((file) => {
    if (!file) return;
    setScreenshotFile(file);
    setScreenshotPreviewUrl(URL.createObjectURL(file));
    const reader = new FileReader();
    reader.onload = (ev) => {
      const b64 = ev.target.result.replace(/^data:[^;]+;base64,/, '');
      setScreenshotBase64(b64);
    };
    reader.readAsDataURL(file);
  }, []);

  // ═══════════════════════════════════════════════════════════════════════════
  // RENDER FUNCTIONS
  // ═══════════════════════════════════════════════════════════════════════════

  // ── Header ─────────────────────────────────────────────────────────────────
  const renderHeader = () => (
    <div className="ide-header">
      <div className="ide-header-left">
        <span className="ide-logo">ngForge</span>
        <select
          className="ide-ds-select"
          value={selectedDS}
          onChange={e => setSelectedDS(e.target.value)}
          disabled={isGenerating}
        >
          {designSystems.length === 0 && <option value="">Loading...</option>}
          {designSystems.map(ds => (
            <option key={ds} value={ds}>{ds}</option>
          ))}
        </select>
        {session && (
          <div className="session-chip">
            <span className={`status-dot ${session ? 'active' : ''}`} />
            {session.session_id?.slice(0, 8)}…
          </div>
        )}
      </div>
      <div className="ide-header-right">
        <button className="btn" onClick={handleNewSession} disabled={isGenerating || !selectedDS}>
          New Session
        </button>
        <button className="btn btn-danger" onClick={handleEndSession} disabled={!session || isGenerating}>
          End Session
        </button>
      </div>
    </div>
  );

  // ── Chat Panel ──────────────────────────────────────────────────────────────
  const renderChatBubble = (msg, i) => {
    if (msg.role === 'user') {
      return (
        <div key={i} className="chat-bubble user">
          {typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)}
        </div>
      );
    }
    // assistant
    const metaType = msg.metadata?.type;
    let extraClass = '';
    let prefix = '';
    if (metaType === 'component_suggestion') { extraClass = 'suggestion'; prefix = '⟨/⟩ '; }
    else if (metaType === 'unresolved_notice') { extraClass = 'warning-msg'; prefix = '⚠ '; }
    else if (i === chatHistory.length - 1) {
      if (lastRefineAction === 'OUT_OF_SCOPE') extraClass = 'oos-msg';
      else if (lastRefineAction === 'CLARIFY') extraClass = 'clarify-msg';
    }
    const text = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
    return (
      <div key={i} className={`chat-bubble assistant ${extraClass}`}>
        {prefix}{text}
      </div>
    );
  };

  const renderCoverageBar = () => {
    if (!dsCoverage) return null;
    const pct = dsCoverage.coverage_pct ?? 0;
    const uncovered = dsCoverage.uncovered_selectors || [];
    return (
      <div className="coverage-bar-wrap">
        <div className="coverage-label">DS Coverage: {pct.toFixed(1)}%</div>
        <div className="coverage-track">
          <div className="coverage-fill" style={{ width: `${pct}%` }} />
        </div>
        {uncovered.length > 0 && (
          <div className="coverage-uncovered">Uncovered: {uncovered.join(', ')}</div>
        )}
      </div>
    );
  };

  const [dropOver, setDropOver] = useState(false);
  const fileInputRef = useRef(null);

  const renderChatPanel = () => (
    <div className="panel chat-panel" style={{ width: `${leftWidth}%` }}>
      {/* Input tabs */}
      <div className="chat-input-tabs">
        {['prompt', 'figma', 'screenshot'].map(t => (
          <div
            key={t}
            className={`chat-tab ${activeInputTab === t ? 'active' : ''}`}
            onClick={() => !isGenerating && setActiveInputTab(t)}
          >
            {t === 'prompt' ? 'Prompt' : t === 'figma' ? 'Figma JSON' : 'Screenshot'}
          </div>
        ))}
      </div>

      {/* Input area */}
      <div className="chat-input-area">
        {activeInputTab === 'prompt' && (
          <textarea
            className="chat-textarea"
            rows={5}
            placeholder="Describe the component to generate or refine…&#10;&#10;Cmd/Ctrl+Enter to send"
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            onKeyDown={handlePromptKeyDown}
            disabled={isGenerating}
          />
        )}
        {activeInputTab === 'figma' && (
          <textarea
            className="chat-textarea"
            rows={5}
            placeholder='Paste Figma JSON tree here…'
            value={figmaJsonText}
            onChange={e => {
              const val = e.target.value;
              setFigmaJsonText(val);
              if (val.trim()) {
                const file = new File(
                  [new Blob([val], { type: 'application/json' })],
                  'figma.json',
                  { type: 'application/json' }
                );
                setFigmaFile(file);
              } else {
                setFigmaFile(null);
              }
            }}
            disabled={isGenerating}
          />
        )}
        {activeInputTab === 'screenshot' && (
          <div>
            {screenshotPreviewUrl && (
              <img src={screenshotPreviewUrl} alt="screenshot" className="screenshot-preview" />
            )}
            <div
              className={`drop-zone ${dropOver ? 'drag-over' : ''}`}
              onClick={() => fileInputRef.current?.click()}
              onDragOver={e => { e.preventDefault(); setDropOver(true); }}
              onDragLeave={() => setDropOver(false)}
              onDrop={e => {
                e.preventDefault(); setDropOver(false);
                const f = e.dataTransfer.files[0];
                if (f) handleScreenshotFile(f);
              }}
            >
              {screenshotFile ? screenshotFile.name : 'Drop PNG/JPG or click to browse'}
            </div>
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              style={{ display: 'none' }}
              onChange={e => { if (e.target.files[0]) handleScreenshotFile(e.target.files[0]); }}
            />
          </div>
        )}
      </div>

      {/* Buttons */}
      <div className="chat-btns">
        <button
          className="btn-generate"
          onClick={handleGenerate}
          disabled={!session || isGenerating || (!prompt.trim() && !figmaFile && !screenshotFile)}
        >
          Generate
        </button>
        {session?.has_generated_code && (
          <button
            className="btn-refine"
            onClick={handleRefine}
            disabled={!prompt.trim() || isGenerating}
          >
            Refine
          </button>
        )}
      </div>

      {isGenerating && (
        <div className="generating-status">
          <span className="blink">▋</span> Generating…
        </div>
      )}

      {/* Coverage bar */}
      {renderCoverageBar()}

      {/* Chat history */}
      <div className="chat-history" ref={chatListRef}>
        {chatHistory.map((msg, i) => renderChatBubble(msg, i))}
        {chatHistory.length === 0 && (
          <div style={{ color: 'var(--text-dim)', fontSize: '12px', textAlign: 'center', marginTop: '20px' }}>
            No messages yet
          </div>
        )}
      </div>
    </div>
  );

  // ── Editor Panel ────────────────────────────────────────────────────────────
  const FILE_TABS = [
    { key: 'html', label: 'component.html' },
    { key: 'scss', label: 'component.scss' },
    { key: 'ts',   label: 'component.ts' },
  ];

  const currentContent = files[activeFileTab] || '';
  const lineCount = currentContent.split('\n').length;
  const lineNumbers = Array.from({ length: lineCount }, (_, i) => i + 1).join('\n');
  const hasAnyCode = !!(files.html || files.scss || files.ts);

  const editorClasses = [
    'panel', 'editor-panel',
    generationFlash ? 'editor-flash' : '',
    isGenerating ? 'generating-pulse' : '',
  ].filter(Boolean).join(' ');

  const renderEditorPanel = () => (
    <div className={editorClasses} style={{ width: `${centerWidth}%` }}>
      {/* Toolbar */}
      <div className="editor-toolbar">
        <div className="editor-toolbar-left">
          <span style={{ fontSize: '12px', color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>
            {FILE_TABS.find(t => t.key === activeFileTab)?.label}
          </span>
        </div>
        <div className="editor-toolbar-right">
          {fileModified[activeFileTab] ? (
            <span className="modified-indicator" style={{ fontSize: '11px' }}>● Modified</span>
          ) : hasAnyCode ? (
            <span className="synced-indicator" style={{ fontSize: '11px' }}>● Synced</span>
          ) : null}
          <button
            className="btn"
            style={{ padding: '2px 8px', fontSize: '11px' }}
            onClick={() => navigator.clipboard?.writeText(currentContent)}
            disabled={!currentContent}
          >
            Copy
          </button>
          <button
            className="btn"
            style={{ padding: '2px 8px', fontSize: '11px' }}
            onClick={() => {
              setFiles(prev => ({ ...prev, [activeFileTab]: lastGenerated[activeFileTab] }));
              setFileModified(prev => ({ ...prev, [activeFileTab]: false }));
            }}
            disabled={!fileModified[activeFileTab]}
          >
            Reset
          </button>
        </div>
      </div>

      {/* File tabs */}
      <div className="editor-file-tabs">
        {FILE_TABS.map(({ key, label }) => (
          <div
            key={key}
            className={`editor-tab ${activeFileTab === key ? 'active' : ''}`}
            onClick={() => setActiveFileTab(key)}
          >
            {label}
            {fileModified[key] && <span className="modified-dot">●</span>}
          </div>
        ))}
      </div>

      {/* Body */}
      <div className="editor-body">
        {!hasAnyCode && (
          <div className="editor-empty">// No code generated yet</div>
        )}
        <div ref={gutterRef} className="line-gutter">
          {lineNumbers}
        </div>
        <textarea
          ref={textareaEditorRef}
          className="editor-textarea"
          value={currentContent}
          spellCheck={false}
          wrap="off"
          onChange={e => {
            setFiles(prev => ({ ...prev, [activeFileTab]: e.target.value }));
            setFileModified(prev => ({ ...prev, [activeFileTab]: true }));
          }}
          onScroll={syncScroll}
        />
      </div>

      {/* Status bar */}
      <div className="editor-status">
        <span>{FILE_TABS.find(t => t.key === activeFileTab)?.label.split('.').pop()?.toUpperCase()}</span>
        <span>{currentContent.length} chars</span>
        <span>{lineCount} lines</span>
      </div>
    </div>
  );

  // ── Preview Panel ───────────────────────────────────────────────────────────
  const VIEWPORTS = [
    { key: 'mobile',   label: 'Mobile',   width: '375px' },
    { key: 'tablet',   label: 'Tablet',   width: '768px' },
    { key: 'desktop',  label: 'Desktop',  width: '100%' },
  ];

  const iframeWidth = VIEWPORTS.find(v => v.key === previewViewport)?.width || '100%';

  const handleRefreshPreview = useCallback(() => {
    const saved = previewSrcdoc;
    setPreviewSrcdoc('');
    setTimeout(() => setPreviewSrcdoc(saved), 50);
  }, [previewSrcdoc]);

  const handleOpenNewTab = useCallback(() => {
    if (!previewSrcdoc) return;
    const blob = new Blob([previewSrcdoc], { type: 'text/html' });
    window.open(URL.createObjectURL(blob));
  }, [previewSrcdoc]);

  const rightWidth = 100 - leftWidth - centerWidth;

  const renderPreviewPanel = () => (
    <div className="panel preview-panel" style={{ width: `${rightWidth}%` }}>
      {/* Toolbar */}
      <div className="preview-toolbar">
        <div className="viewport-btns">
          {VIEWPORTS.map(({ key, label }) => (
            <button
              key={key}
              className={`viewport-btn ${previewViewport === key ? 'active' : ''}`}
              onClick={() => setPreviewViewport(key)}
            >
              {label}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', gap: '6px' }}>
          <button className="btn" style={{ padding: '2px 8px', fontSize: '11px' }} onClick={handleRefreshPreview} disabled={!previewSrcdoc}>
            Refresh
          </button>
          <button className="btn" style={{ padding: '2px 8px', fontSize: '11px' }} onClick={handleOpenNewTab} disabled={!previewSrcdoc}>
            Open ↗
          </button>
        </div>
      </div>

      {/* Preview body */}
      <div className="preview-body">
        <div className="preview-iframe-wrap" style={{ position: 'relative', flex: 1 }}>
          {!previewSrcdoc && (
            <div className="preview-empty">
              &lt; Preview will appear here &gt;
            </div>
          )}
          {previewSrcdoc && (
            <div style={{ width: '100%', height: '100%', overflow: 'auto', display: 'flex', justifyContent: 'center' }}>
              <iframe
                ref={iframeRef}
                srcDoc={previewSrcdoc}
                sandbox="allow-scripts"
                style={{
                  width: iframeWidth,
                  height: '100%',
                  border: 'none',
                  display: 'block',
                  maxWidth: '100%',
                }}
                title="preview"
              />
            </div>
          )}
        </div>

        {/* Error console */}
        <div className="error-console">
          <div className="error-console-header" onClick={() => setErrorConsoleOpen(o => !o)}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              {errorLog.length > 0
                ? <><span className="error-dot" /><span style={{ color: 'var(--error)' }}>● {errorLog.length} error{errorLog.length !== 1 ? 's' : ''}</span></>
                : <span style={{ color: 'var(--text-dim)' }}>○ No errors</span>
              }
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
              {errorLog.length > 0 && (
                <button
                  className="btn"
                  style={{ padding: '1px 6px', fontSize: '10px' }}
                  onClick={e => { e.stopPropagation(); setErrorLog([]); }}
                >
                  Clear
                </button>
              )}
              <span style={{ color: 'var(--text-dim)', fontSize: '11px' }}>{errorConsoleOpen ? '▼' : '▲'}</span>
            </div>
          </div>
          {errorConsoleOpen && (
            <div className="error-console-body">
              {errorLog.length === 0 && (
                <div style={{ padding: '8px 10px', color: 'var(--text-dim)', fontSize: '11px', fontFamily: 'var(--font-mono)' }}>
                  No errors logged.
                </div>
              )}
              {errorLog.map(entry => (
                <div key={entry.id} className="error-entry">
                  <span className="error-ts">[{entry.timestamp}]</span>
                  <span className={`error-badge ${entry.type.toLowerCase()}`}>{entry.type}</span>
                  <span className="error-msg">{entry.message}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // 10. MAIN RENDER
  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'var(--bg-primary)' }}>
      {renderHeader()}
      <div className="ide-main">
        {renderChatPanel()}
        <div
          className={`drag-handle ${dragging === 'left' ? 'dragging' : ''}`}
          onMouseDown={e => startDrag('left', e)}
        />
        {renderEditorPanel()}
        <div
          className={`drag-handle ${dragging === 'right' ? 'dragging' : ''}`}
          onMouseDown={e => startDrag('right', e)}
        />
        {renderPreviewPanel()}
      </div>
    </div>
  );
}
