import React, {
  useState,
  useEffect,
  useRef,
  useCallback,
} from 'react';

const API = 'http://localhost:8000';

// ─── Static scaffold constants ────────────────────────────────────────────────
const ANGULAR_JSON = JSON.stringify({
  "$schema": "./node_modules/@angular/cli/lib/config/schema.json",
  "version": 1,
  "newProjectRoot": "projects",
  "projects": {
    "ngforge-preview": {
      "projectType": "application",
      "root": "",
      "sourceRoot": "src",
      "architect": {
        "build": {
          "builder": "@angular-devkit/build-angular:application",
          "options": {
            "outputPath": "dist/ngforge-preview",
            "index": "src/index.html",
            "browser": "src/main.ts",
            "polyfills": ["zone.js"],
            "tsConfig": "tsconfig.app.json",
            "styles": ["src/styles.scss"],
            "scripts": []
          }
        },
        "serve": {
          "builder": "@angular-devkit/build-angular:dev-server",
          "configurations": {
            "development": { "buildTarget": "ngforge-preview:build:development" }
          },
          "defaultConfiguration": "development"
        }
      }
    }
  }
}, null, 2);

const TSCONFIG_JSON = JSON.stringify({
  "compileOnSave": false,
  "compilerOptions": {
    "baseUrl": "./",
    "outDir": "./dist/out-tsc",
    "strict": true,
    "noImplicitOverride": true,
    "noPropertyAccessFromIndexSignature": true,
    "noImplicitReturns": true,
    "noFallthroughCasesInSwitch": true,
    "esModuleInterop": true,
    "sourceMap": true,
    "declaration": false,
    "downlevelIteration": true,
    "experimentalDecorators": true,
    "moduleResolution": "bundler",
    "importHelpers": true,
    "target": "ES2022",
    "module": "ES2022",
    "useDefineForClassFields": false,
    "lib": ["ES2022", "dom"]
  },
  "angularCompilerOptions": {
    "enableI18nLegacyMessageIdFormat": false,
    "strictInjectionParameters": true,
    "strictInputAccessModifiers": true,
    "strictTemplates": true
  }
}, null, 2);

const TSCONFIG_APP_JSON = JSON.stringify({
  "extends": "./tsconfig.json",
  "compilerOptions": { "outDir": "./out-tsc/app", "types": [] },
  "files": ["src/main.ts"],
  "include": ["src/**/*.d.ts"]
}, null, 2);

const APP_COMPONENT_TS = `import { Component } from '@angular/core';
import { PreviewComponent } from './preview/preview.component';

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [PreviewComponent],
  template: \`<app-preview></app-preview>\`,
  styles: [\`
    :host { display: block; padding: 24px; min-height: 100vh; }
  \`]
})
export class AppComponent {}`;

// ─── Dynamic scaffold builders ────────────────────────────────────────────────
function buildPackageJson(designSystem, extraDeps = {}) {
  const base = {
    name: 'ngforge-preview',
    version: '0.0.0',
    scripts: { start: 'ng serve --host 0.0.0.0', build: 'ng build' },
    dependencies: {
      '@angular/animations': '^17.0.0',
      '@angular/common': '^17.0.0',
      '@angular/compiler': '^17.0.0',
      '@angular/core': '^17.0.0',
      '@angular/forms': '^17.0.0',
      '@angular/platform-browser': '^17.0.0',
      '@angular/platform-browser-dynamic': '^17.0.0',
      'rxjs': '~7.8.0',
      'tslib': '^2.3.0',
      'zone.js': '~0.14.0',
    },
    devDependencies: {
      '@angular/cli': '^17.0.0',
      '@angular/compiler-cli': '^17.0.0',
      'typescript': '~5.2.2',
    }
  };
  if (designSystem === 'primeng') {
    base.dependencies['primeng'] = '^17.0.0';
    base.dependencies['primeicons'] = '^6.0.0';
    base.dependencies['primeflex'] = '^3.3.1';
  }
  Object.assign(base.dependencies, extraDeps);
  return JSON.stringify(base, null, 2);
}

function buildIndexHtml(designSystem) {
  const dsLinks = {
    primeng: `
  <link rel="stylesheet" href="https://unpkg.com/primeng/resources/themes/lara-light-blue/theme.css"/>
  <link rel="stylesheet" href="https://unpkg.com/primeng/resources/primeng.min.css"/>
  <link rel="stylesheet" href="https://unpkg.com/primeicons/primeicons.css"/>
  <link rel="stylesheet" href="https://unpkg.com/primeflex@3/primeflex.css"/>`,
    myds: ``
  };
  return `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>ngForge Preview</title>
  <base href="/">
  <meta name="viewport" content="width=device-width, initial-scale=1">${dsLinks[designSystem] || ''}
</head>
<body>
  <app-root></app-root>
</body>
</html>`;
}

function buildMainTs() {
  return `import { bootstrapApplication } from '@angular/platform-browser';
import { AppComponent } from './app/app.component';
import { provideAnimations } from '@angular/platform-browser/animations';

bootstrapApplication(AppComponent, {
  providers: [provideAnimations()]
}).catch(err => console.error(err));

// ngForge error reporter
window.addEventListener('error', (e) => {
  window.parent?.postMessage({ type: 'ngforge-error', message: e.message, filename: e.filename, lineno: e.lineno }, '*');
});
window.addEventListener('unhandledrejection', (e) => {
  window.parent?.postMessage({ type: 'ngforge-error', message: String(e.reason) }, '*');
});`;
}

function ensureStandalone(tsContent) {
  if (!tsContent) return tsContent;
  if (!tsContent.includes('standalone: true')) {
    tsContent = tsContent.replace(
      /@Component\s*\(\s*\{/,
      '@Component({\n  standalone: true,'
    );
  }
  return tsContent;
}

function buildAngularProject(files, configFiles) {
  return {
    'angular.json': configFiles.angularJson || ANGULAR_JSON,
    'package.json': configFiles.packageJson || buildPackageJson(''),
    'tsconfig.json': configFiles.tsconfigJson || TSCONFIG_JSON,
    'tsconfig.app.json': TSCONFIG_APP_JSON,
    'src/index.html': configFiles.indexHtml || buildIndexHtml(''),
    'src/styles.scss': configFiles.stylesScss || `* { box-sizing: border-box; margin: 0; padding: 0; }\nbody { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }`,
    'src/main.ts': configFiles.mainTs || buildMainTs(),
    'src/app/app.component.ts': APP_COMPONENT_TS,
    'src/app/preview/preview.component.ts': ensureStandalone(files.ts || '// Component not yet generated'),
    'src/app/preview/preview.component.html': files.html || '<p>Component not yet generated</p>',
    'src/app/preview/preview.component.scss': files.scss || '',
  };
}

// ─── Angular logo for empty state ─────────────────────────────────────────────
const AngularLogo = () => (
  <svg width="56" height="56" viewBox="0 0 250 250" style={{ opacity: 0.18 }}>
    <polygon fill="#DD0031" points="125,30 125,30 125,30 31.9,63.2 46.1,186.3 125,230 125,230 125,230 203.9,186.3 218.1,63.2"/>
    <polygon fill="#C3002F" points="125,30 125,52.2 125,52.1 125,153.4 125,153.4 125,230 125,230 203.9,186.3 218.1,63.2"/>
    <path fill="#FFFFFF" d="M125,52.1L66.8,182.6h0h21.7h0l11.7-29.2h49.4l11.7,29.2h0h21.7h0L125,52.1L125,52.1z M142,135.4H108l17-40.9L142,135.4z"/>
  </svg>
);

// ─── Package classification ───────────────────────────────────────────────────
const ANGULAR_CORE_PKGS = new Set([
  '@angular/animations','@angular/common','@angular/compiler','@angular/core',
  '@angular/forms','@angular/platform-browser','@angular/platform-browser-dynamic',
  'rxjs','tslib','zone.js'
]);
const DS_PKGS = {
  primeng: new Set(['primeng','primeicons','primeflex']),
};

// ─── Config tab definitions ───────────────────────────────────────────────────
const CONFIG_TABS = [
  { key: 'angularJson',  label: 'angular.json' },
  { key: 'packageJson',  label: 'package.json' },
  { key: 'tsconfigJson', label: 'tsconfig.json' },
  { key: 'indexHtml',    label: 'src/index.html' },
  { key: 'stylesScss',   label: 'src/styles.scss' },
  { key: 'mainTs',       label: 'src/main.ts' },
];

const FILE_TABS = [
  { key: 'html', label: 'component.html' },
  { key: 'scss', label: 'component.scss' },
  { key: 'ts',   label: 'component.ts' },
];

// ═════════════════════════════════════════════════════════════════════════════
export default function NgForgeIDE() {
  // ── Session ────────────────────────────────────────────────────────────────
  const [designSystems, setDesignSystems] = useState([]);
  const [selectedDS, setSelectedDS] = useState('');
  const [session, setSession] = useState(null);
  const [isGenerating, setIsGenerating] = useState(false);

  // ── Component files ────────────────────────────────────────────────────────
  const [files, setFiles] = useState({ html: '', scss: '', ts: '' });
  const [lastGenerated, setLastGenerated] = useState({ html: '', scss: '', ts: '' });
  const [activeFileTab, setActiveFileTab] = useState('html');
  const [fileModified, setFileModified] = useState({ html: false, scss: false, ts: false });

  // ── Config files ───────────────────────────────────────────────────────────
  const [configFiles, setConfigFiles] = useState({
    angularJson: '', packageJson: '', tsconfigJson: '',
    indexHtml: '', stylesScss: '', mainTs: '',
  });
  const [configModified, setConfigModified] = useState({});
  const [configExpanded, setConfigExpanded] = useState(false);
  const [activeConfigTab, setActiveConfigTab] = useState('angularJson');
  const [activeEditorArea, setActiveEditorArea] = useState('component');

  // ── npm packages ───────────────────────────────────────────────────────────
  const [newPkgInput, setNewPkgInput] = useState('');
  const [coreExpanded, setCoreExpanded] = useState(false);

  // ── Chat inputs ────────────────────────────────────────────────────────────
  const [prompt, setPrompt] = useState('');
  const [figmaJsonText, setFigmaJsonText] = useState('');
  const [figmaFile, setFigmaFile] = useState(null);
  const [screenshotFile, setScreenshotFile] = useState(null);
  const [screenshotBase64, setScreenshotBase64] = useState(null);
  const [screenshotPreviewUrl, setScreenshotPreviewUrl] = useState(null);
  const [figmaExpanded, setFigmaExpanded] = useState(false);
  const [chatHistory, setChatHistory] = useState([]);
  const [dsCoverage, setDsCoverage] = useState(null);
  const [dropOver, setDropOver] = useState(false);

  // ── Preview ────────────────────────────────────────────────────────────────
  const [previewDirty, setPreviewDirty] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [sbkReady, setSbkReady] = useState(false);

  // ── Errors ─────────────────────────────────────────────────────────────────
  const [errorLog, setErrorLog] = useState([]);
  const [errorConsoleOpen, setErrorConsoleOpen] = useState(false);
  const [lastRefineAction, setLastRefineAction] = useState(null);
  const [generationFlash, setGenerationFlash] = useState(false);

  // ── Panels ─────────────────────────────────────────────────────────────────
  const [leftWidth, setLeftWidth] = useState(25);
  const [centerWidth, setCenterWidth] = useState(42);
  const [dragging, setDragging] = useState(null);

  // ── Refs ───────────────────────────────────────────────────────────────────
  const chatListRef = useRef(null);
  const dragStartRef = useRef(null);
  const gutterRef = useRef(null);
  const textareaEditorRef = useRef(null);
  const sbkContainerRef = useRef(null);
  const currentProjectRef = useRef(null);
  const fileInputRef = useRef(null);

  // ═══════════════════════════════════════════════════════════════════════════
  // 1. CSS INJECTION
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    const fonts = [
      "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap",
      "https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500&display=swap",
    ];
    fonts.forEach(href => {
      if (!document.querySelector(`link[href="${href}"]`)) {
        const l = document.createElement('link');
        l.rel = 'stylesheet'; l.href = href;
        document.head.appendChild(l);
      }
    });
    if (document.getElementById('ngforge-styles')) return;
    const style = document.createElement('style');
    style.id = 'ngforge-styles';
    style.textContent = `
      :root {
        --bg-primary:#0d0d0d; --bg-secondary:#111111; --bg-tertiary:#1a1a1a;
        --bg-panel:#121212; --border:#222222; --border-active:#333333;
        --text-primary:#e8e8e8; --text-secondary:#a0a0a0; --text-dim:#555555;
        --accent:#00d4aa; --accent-dim:#00a07a; --warning:#f0a020;
        --error:#e05050; --success:#40c070; --info:#4090e0;
        --font-mono:'JetBrains Mono','Fira Code',monospace;
        --font-sans:'IBM Plex Sans',system-ui,sans-serif;
      }
      *,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
      html,body{height:100%;overflow:hidden;background:var(--bg-primary);color:var(--text-primary);font-family:var(--font-sans)}
      ::-webkit-scrollbar{width:4px;height:4px}
      ::-webkit-scrollbar-track{background:transparent}
      ::-webkit-scrollbar-thumb{background:#333;border-radius:2px}
      ::-webkit-scrollbar-thumb:hover{background:#444}

      @keyframes editor-flash-anim{0%{box-shadow:0 0 0 1px var(--accent)}100%{box-shadow:0 0 0 1px transparent}}
      .editor-flash{animation:editor-flash-anim 800ms ease-out forwards}
      @keyframes gen-pulse-anim{0%,100%{box-shadow:0 0 0 1px var(--accent-dim)}50%{box-shadow:0 0 0 2px var(--accent)}}
      .generating-pulse{animation:gen-pulse-anim 1.2s ease-in-out infinite}
      @keyframes blink{0%,100%{opacity:1}50%{opacity:0}}
      .blink{animation:blink 1s step-end infinite}
      @keyframes loading-pulse{0%,100%{opacity:.4}50%{opacity:1}}
      .loading-pulse{animation:loading-pulse 1.2s ease-in-out infinite}

      .ide-header{height:44px;min-height:44px;display:flex;align-items:center;justify-content:space-between;padding:0 12px;background:var(--bg-secondary);border-bottom:1px solid var(--border);flex-shrink:0;gap:12px}
      .ide-header-left{display:flex;align-items:center;gap:10px;flex-shrink:0}
      .ide-header-right{display:flex;align-items:center;gap:8px;flex-shrink:0}
      .ide-logo{font-family:var(--font-mono);font-size:13px;font-weight:500;color:var(--accent);letter-spacing:1px}
      .ide-subtitle{font-family:var(--font-sans);font-size:10px;color:var(--text-dim);letter-spacing:.3px;padding-left:8px;border-left:1px solid var(--border)}
      .ide-ds-select{background:var(--bg-tertiary);color:var(--text-primary);border:1px solid var(--border);padding:3px 8px;font-family:var(--font-mono);font-size:12px;cursor:pointer;outline:none}
      .ide-ds-select:focus{border-color:var(--border-active)}
      .session-chip{display:flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:11px;color:var(--text-dim);background:var(--bg-tertiary);border:1px solid var(--border);padding:2px 8px}
      .status-dot{width:8px;height:8px;border-radius:50%;background:var(--error);flex-shrink:0}
      .status-dot.active{background:var(--success)}
      .btn{background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border);padding:4px 10px;font-family:var(--font-sans);font-size:12px;cursor:pointer;border-radius:2px;transition:border-color .15s}
      .btn:hover{border-color:var(--border-active);color:var(--text-primary)}
      .btn:disabled{opacity:.4;cursor:not-allowed}
      .btn-danger:hover{border-color:var(--error);color:var(--error)}
      .btn-run{background:var(--accent);color:#000;border:1px solid var(--accent);padding:3px 10px;font-family:var(--font-sans);font-size:12px;font-weight:500;cursor:pointer;border-radius:2px;transition:background .15s;white-space:nowrap}
      .btn-run:hover{background:var(--accent-dim);border-color:var(--accent-dim)}
      .btn-run:disabled{opacity:.5;cursor:not-allowed}
      .btn-rerun{background:transparent;color:var(--text-dim);border:1px solid var(--border);padding:3px 10px;font-family:var(--font-sans);font-size:12px;cursor:pointer;border-radius:2px;transition:border-color .15s,color .15s;white-space:nowrap}
      .btn-rerun:hover{border-color:var(--border-active);color:var(--text-secondary)}
      .btn-rerun:disabled{opacity:.4;cursor:not-allowed}

      .ide-main{display:flex;flex:1;overflow:hidden;min-height:0}
      .drag-handle{width:4px;flex-shrink:0;background:var(--border);cursor:col-resize;transition:background .15s}
      .drag-handle:hover,.drag-handle.dragging{background:var(--border-active)}
      .panel{display:flex;flex-direction:column;overflow:hidden;min-width:0}

      /* Chat */
      .chat-panel{background:var(--bg-panel)}
      .input-sections-wrap{flex-shrink:0;border-bottom:1px solid var(--border)}
      .input-section{padding:6px 8px}
      .input-section+.input-section{border-top:1px solid var(--border)}
      .input-section-header{display:flex;align-items:center;justify-content:space-between;margin-bottom:4px}
      .section-label{display:flex;align-items:center;gap:5px;font-size:10px;font-family:var(--font-mono);color:var(--text-dim);text-transform:uppercase;letter-spacing:.5px}
      .section-label.clickable{cursor:pointer}
      .section-label.clickable:hover{color:var(--text-secondary)}
      .section-toggle{font-size:9px}
      .input-badge{padding:1px 5px;font-size:10px;border-radius:2px;background:rgba(0,212,170,.15);color:var(--accent);border:1px solid rgba(0,212,170,.3)}
      .section-clear-btn{background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:11px;padding:0 2px;line-height:1}
      .section-clear-btn:hover{color:var(--error)}
      .screenshot-compact{display:flex;align-items:center;gap:6px;border:1px dashed var(--border);padding:5px 8px;cursor:pointer;font-size:11px;color:var(--text-dim);font-family:var(--font-mono)}
      .screenshot-compact.has-file{border-color:var(--border-active);color:var(--text-secondary)}
      .screenshot-compact.drag-over{border-color:var(--accent);color:var(--accent)}
      .screenshot-compact:hover:not(.has-file){border-color:var(--border-active)}
      .screenshot-thumb{width:28px;height:28px;object-fit:cover;border:1px solid var(--border);flex-shrink:0}
      .screenshot-filename{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:11px}
      .chat-textarea{width:100%;background:var(--bg-tertiary);color:var(--text-primary);border:1px solid var(--border);padding:8px;font-family:var(--font-mono);font-size:12px;resize:none;outline:none;line-height:1.5}
      .chat-textarea:focus{border-color:var(--border-active)}
      .chat-textarea:disabled{opacity:.5}
      .chat-btns{display:flex;gap:6px;padding:8px;flex-shrink:0}
      .btn-generate{flex:1;background:var(--accent);color:#000;border:none;padding:7px 12px;font-family:var(--font-sans);font-size:13px;font-weight:500;cursor:pointer;border-radius:2px;transition:background .15s}
      .btn-generate:hover{background:var(--accent-dim)}
      .btn-generate:disabled{opacity:.4;cursor:not-allowed;background:var(--accent)}
      .btn-refine{background:var(--bg-tertiary);color:var(--text-secondary);border:1px solid var(--border);padding:7px 12px;font-size:12px;cursor:pointer;border-radius:2px;white-space:nowrap}
      .btn-refine:hover{border-color:var(--accent);color:var(--accent)}
      .btn-refine:disabled{opacity:.4;cursor:not-allowed}
      .generating-status{padding:4px 8px 8px;font-family:var(--font-mono);font-size:12px;color:var(--accent);flex-shrink:0}
      .chat-history{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:8px}
      .chat-bubble{max-width:90%;padding:8px 10px;font-size:12px;line-height:1.5;word-break:break-word}
      .chat-bubble.user{align-self:flex-end;background:var(--bg-tertiary);color:var(--text-primary);border-left:2px solid var(--border-active)}
      .chat-bubble.assistant{align-self:flex-start;background:var(--bg-secondary);color:var(--text-secondary);border-left:2px solid var(--border)}
      .chat-bubble.suggestion{border-left-color:var(--accent);color:var(--text-primary)}
      .chat-bubble.warning-msg{border-left-color:var(--warning);color:var(--warning)}
      .chat-bubble.clarify-msg{border-left-color:var(--info);color:var(--info)}
      .chat-bubble.oos-msg{border-left-color:var(--warning);color:var(--warning)}
      .coverage-bar-wrap{padding:4px 8px;flex-shrink:0;border-top:1px solid var(--border)}
      .coverage-label{font-family:var(--font-mono);font-size:11px;color:var(--text-dim);margin-bottom:4px}
      .coverage-track{height:3px;background:var(--bg-tertiary);border-radius:1px;overflow:hidden}
      .coverage-fill{height:100%;background:var(--accent);transition:width .4s}
      .coverage-uncovered{font-family:var(--font-mono);font-size:10px;color:var(--text-dim);margin-top:2px}

      /* Editor */
      .editor-panel{background:var(--bg-primary)}
      .editor-toolbar{display:flex;align-items:center;justify-content:space-between;padding:4px 8px;border-bottom:1px solid var(--border);background:var(--bg-secondary);flex-shrink:0;height:32px}
      .editor-toolbar-left,.editor-toolbar-right{display:flex;align-items:center;gap:6px}
      .editor-file-tabs{display:flex;border-bottom:1px solid var(--border);flex-shrink:0}
      .editor-tab{padding:8px 14px;font-family:var(--font-mono);font-size:11px;cursor:pointer;color:var(--text-dim);border-bottom:2px solid transparent;white-space:nowrap;display:flex;align-items:center;gap:5px}
      .editor-tab.active{color:var(--text-primary);border-bottom-color:var(--accent)}
      .editor-tab:hover:not(.active){color:var(--text-secondary)}
      .modified-dot{color:var(--warning);font-size:14px;line-height:1}
      .editor-body{display:flex;flex:1;overflow:hidden;min-height:0;position:relative}
      .line-gutter{width:40px;flex-shrink:0;background:var(--bg-secondary);color:var(--text-dim);font-family:var(--font-mono);font-size:13px;line-height:1.6;padding:8px 0;text-align:right;padding-right:8px;overflow:hidden;user-select:none}
      .editor-textarea{flex:1;background:#0d0d0d;color:var(--text-primary);border:none;outline:none;resize:none;font-family:var(--font-mono);font-size:13px;line-height:1.6;padding:8px;tab-size:2;white-space:pre;overflow-wrap:normal;overflow-x:auto;caret-color:var(--accent)}
      .editor-status{display:flex;align-items:center;gap:12px;padding:4px 10px;border-top:1px solid var(--border);background:var(--bg-secondary);flex-shrink:0;font-family:var(--font-mono);font-size:11px;color:var(--text-dim)}
      .modified-indicator{color:var(--warning)}
      .synced-indicator{color:var(--success)}
      .editor-empty{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-family:var(--font-mono);font-size:13px;color:var(--text-dim);pointer-events:none}

      /* Config section */
      .config-section{flex-shrink:0;border-bottom:1px solid var(--border)}
      .config-section-header{display:flex;align-items:center;justify-content:space-between;padding:5px 10px;cursor:pointer;background:var(--bg-secondary);font-size:11px;font-family:var(--font-mono);color:var(--text-dim);user-select:none;border-top:1px solid var(--border)}
      .config-section-header:hover{color:var(--text-secondary);background:var(--bg-tertiary)}
      .config-tabs{display:flex;overflow-x:auto;border-bottom:1px solid var(--border);flex-shrink:0;background:var(--bg-secondary)}
      .config-tab{padding:5px 11px;font-family:var(--font-mono);font-size:10px;cursor:pointer;color:var(--text-dim);white-space:nowrap;border-bottom:2px solid transparent;display:flex;align-items:center;gap:4px}
      .config-tab.active{color:var(--accent);border-bottom-color:var(--accent)}
      .config-tab:hover:not(.active){color:var(--text-secondary)}

      /* npm packages */
      .npm-section{padding:8px;border-bottom:1px solid var(--border);background:var(--bg-secondary)}
      .npm-title{font-family:var(--font-mono);font-size:10px;color:var(--text-dim);margin-bottom:6px;text-transform:uppercase;letter-spacing:.5px}
      .pkg-group-header{display:flex;align-items:center;gap:6px;font-family:var(--font-mono);font-size:10px;color:var(--text-dim);cursor:pointer;padding:3px 0;user-select:none}
      .pkg-group-header:hover{color:var(--text-secondary)}
      .pkg-item{display:flex;align-items:center;padding:2px 0 2px 12px;font-family:var(--font-mono);font-size:10px;color:var(--text-secondary)}
      .pkg-name{flex:1}
      .pkg-version{color:var(--text-dim);margin-right:6px}
      .pkg-remove{background:none;border:none;color:var(--text-dim);cursor:pointer;font-size:11px;padding:0 2px;line-height:1}
      .pkg-remove:hover{color:var(--error)}
      .pkg-add-row{display:flex;gap:4px;margin-top:6px}
      .pkg-input{flex:1;background:var(--bg-tertiary);color:var(--text-primary);border:1px solid var(--border);padding:3px 6px;font-family:var(--font-mono);font-size:11px;outline:none}
      .pkg-input:focus{border-color:var(--border-active)}
      .pkg-warning{font-family:var(--font-mono);font-size:10px;color:var(--warning);margin-top:4px;line-height:1.4}

      /* Preview */
      .preview-panel{background:var(--bg-primary)}
      .preview-toolbar{display:flex;align-items:center;justify-content:space-between;padding:4px 8px;border-bottom:1px solid var(--border);background:var(--bg-secondary);flex-shrink:0;height:32px}
      .preview-body{flex:1;overflow:hidden;display:flex;flex-direction:column;min-height:0;position:relative}
      .preview-sbk-wrap{flex:1;min-height:0;position:relative;overflow:hidden}
      .preview-empty{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;font-family:var(--font-mono);font-size:12px;color:var(--text-dim);background:repeating-linear-gradient(45deg,#111 0,#111 10px,#0a0a0a 10px,#0a0a0a 20px);pointer-events:none}
      .sbk-container{width:100%;height:100%;overflow:hidden}
      .sbk-container iframe{width:100%;height:100%;border:none;display:block}

      /* Error console */
      .error-console{flex-shrink:0;border-top:1px solid var(--border);display:flex;flex-direction:column}
      .error-console-header{display:flex;align-items:center;justify-content:space-between;padding:4px 10px;cursor:pointer;background:var(--bg-secondary);font-size:12px;flex-shrink:0;user-select:none}
      .error-console-header:hover{background:var(--bg-tertiary)}
      .error-console-body{height:160px;background:#080808;overflow-y:auto;padding:6px 0}
      .error-entry{display:flex;align-items:flex-start;gap:8px;padding:3px 10px;font-family:var(--font-mono);font-size:11px;line-height:1.5}
      .error-ts{color:var(--text-dim);flex-shrink:0}
      .error-badge{padding:0 5px;font-size:10px;border-radius:2px;flex-shrink:0;margin-top:1px}
      .error-badge.api{background:rgba(224,80,80,.2);color:var(--error)}
      .error-badge.ng{background:rgba(224,80,80,.9);color:#fff}
      .error-badge.scss{background:rgba(240,160,32,.2);color:var(--warning)}
      .error-badge.js{background:rgba(240,120,32,.2);color:#f07820}
      .error-msg{color:var(--text-secondary);word-break:break-all}
      .error-dot{width:8px;height:8px;border-radius:50%;background:var(--error);flex-shrink:0}
    `;
    document.head.appendChild(style);
    return () => {
      const el = document.getElementById('ngforge-styles');
      if (el) el.remove();
    };
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

  const buildDefaultConfigFiles = useCallback((ds) => ({
    angularJson: ANGULAR_JSON,
    packageJson: buildPackageJson(ds),
    tsconfigJson: TSCONFIG_JSON,
    indexHtml: buildIndexHtml(ds),
    stylesScss: `* { box-sizing: border-box; margin: 0; padding: 0; }\nbody { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; }`,
    mainTs: buildMainTs(),
  }), []);

  // ═══════════════════════════════════════════════════════════════════════════
  // 3. STACKBLITZ SDK LOADER
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    if (window.StackBlitzSDK) { setSbkReady(true); return; }
    if (document.querySelector('script[data-sbk]')) return;
    const s = document.createElement('script');
    s.src = 'https://unpkg.com/@stackblitz/sdk@1/bundles/sdk.umd.js';
    s.setAttribute('data-sbk', '1');
    s.onload = () => setSbkReady(true);
    s.onerror = () => pushError('API', 'Failed to load StackBlitz SDK');
    document.head.appendChild(s);
  }, [pushError]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 4. SESSION LIFECYCLE
  // ═══════════════════════════════════════════════════════════════════════════
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
    setPreviewDirty(false);
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

  useEffect(() => {
    if (!selectedDS) return;
    let cancelled = false;
    const prevId = session?.session_id;
    const run = async () => {
      if (prevId) await deleteSession(prevId);
      if (!cancelled) {
        clearSessionState();
        setConfigFiles(buildDefaultConfigFiles(selectedDS));
        setConfigModified({});
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
    setConfigFiles(buildDefaultConfigFiles(selectedDS));
    setConfigModified({});
    await createSession(selectedDS);
  }, [session, selectedDS, deleteSession, clearSessionState, createSession, buildDefaultConfigFiles]);

  const handleEndSession = useCallback(async () => {
    if (session?.session_id) await deleteSession(session.session_id);
    setSession(null);
    clearSessionState();
  }, [session, deleteSession, clearSessionState]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 5. GENERATE
  // ═══════════════════════════════════════════════════════════════════════════
  const handleGenerate = useCallback(async () => {
    if (!session) return;
    const fd = new FormData();
    if (figmaFile) fd.append('figma_json', figmaFile, 'figma.json');
    if (screenshotFile) fd.append('screenshot', screenshotFile, screenshotFile.name);
    if (prompt.trim()) fd.append('prompt', prompt.trim());
    if (![...fd.entries()].length) return;

    setIsGenerating(true);
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
      setPreviewDirty(true);
      // Rebuild config files, preserving user-modified ones
      setConfigFiles(prev => {
        const fresh = buildDefaultConfigFiles(selectedDS);
        const merged = { ...fresh };
        Object.keys(prev).forEach(k => {
          if (configModified[k]) merged[k] = prev[k];
        });
        return merged;
      });
    }
    setIsGenerating(false);
  }, [session, figmaFile, screenshotFile, prompt, apiFetch, extractFiles, flashEditor, selectedDS, buildDefaultConfigFiles, configModified]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 6. REFINE
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
        setPreviewDirty(true);
        setLastRefineAction('APPLY_REFINE');
      } else {
        setLastRefineAction(action);
      }
      setChatHistory(data.chat_history || []);
    }
    setIsGenerating(false);
  }, [session, prompt, screenshotBase64, apiFetch, extractFiles, flashEditor]);

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
  // 7. STACKBLITZ PREVIEW
  // ═══════════════════════════════════════════════════════════════════════════
  const handleRenderPreview = useCallback(async () => {
    if (!sbkReady || !window.StackBlitzSDK) {
      pushError('API', 'StackBlitz SDK not ready yet');
      return;
    }
    const container = sbkContainerRef.current;
    if (!container) return;

    setPreviewLoading(true);
    try {
      const projectFiles = buildAngularProject(files, configFiles);
      const project = {
        title: 'ngForge Preview',
        description: 'Generated Angular Component',
        template: 'node',
        files: projectFiles,
        tags: ['angular', 'ngforge'],
      };
      currentProjectRef.current = project;
      await window.StackBlitzSDK.embedProject(container, project, {
        openFile: 'src/app/preview/preview.component.html',
        view: 'preview',
        height: '100%',
        hideNavigation: true,
        hideDevTools: false,
        clickToLoad: false,
        theme: 'dark',
      });
      setPreviewDirty(false);
    } catch (err) {
      pushError('API', `StackBlitz: ${err.message}`);
    } finally {
      setPreviewLoading(false);
    }
  }, [sbkReady, files, configFiles, pushError]);

  const handleOpenInStackBlitz = useCallback(() => {
    if (!window.StackBlitzSDK || !currentProjectRef.current) return;
    window.StackBlitzSDK.openProject(currentProjectRef.current, { newWindow: true });
  }, []);

  // ═══════════════════════════════════════════════════════════════════════════
  // 8. NGFORGE ERROR REPORTER (postMessage from StackBlitz iframe)
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    const handler = (e) => {
      if (e.data?.type === 'ngforge-error') {
        const loc = e.data.lineno ? ` (line ${e.data.lineno})` : '';
        pushError('NG', e.data.message + loc);
      }
    };
    window.addEventListener('message', handler);
    return () => window.removeEventListener('message', handler);
  }, [pushError]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 9. RESIZABLE PANELS
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
        if (newLeft + newCenter <= 85) { setLeftWidth(newLeft); setCenterWidth(newCenter); }
      } else {
        const newCenter = clamp(c0 + delta, 15, 60);
        const right = 100 - l0 - newCenter;
        if (right >= 15 && right <= 60) setCenterWidth(newCenter);
      }
    };
    const onUp = () => { setDragging(null); document.body.style.cursor = ''; };
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
  // 10. CHAT AUTO-SCROLL
  // ═══════════════════════════════════════════════════════════════════════════
  useEffect(() => {
    if (chatListRef.current) chatListRef.current.scrollTop = chatListRef.current.scrollHeight;
  }, [chatHistory]);

  // ═══════════════════════════════════════════════════════════════════════════
  // 11. SCREENSHOT HANDLER
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
  // 12. NPM PACKAGE MANAGEMENT
  // ═══════════════════════════════════════════════════════════════════════════
  const getParsedPkg = useCallback(() => {
    try { return JSON.parse(configFiles.packageJson); } catch { return null; }
  }, [configFiles.packageJson]);

  const handleAddPackage = useCallback(() => {
    const input = newPkgInput.trim();
    if (!input) return;
    const atIdx = input.lastIndexOf('@');
    const name = atIdx > 0 ? input.slice(0, atIdx) : input;
    const version = atIdx > 0 ? input.slice(atIdx + 1) || 'latest' : 'latest';
    const parsed = getParsedPkg();
    if (!parsed) { pushError('API', 'Cannot parse package.json'); return; }
    parsed.dependencies[name] = version;
    setConfigFiles(prev => ({ ...prev, packageJson: JSON.stringify(parsed, null, 2) }));
    setConfigModified(prev => ({ ...prev, packageJson: true }));
    setPreviewDirty(true);
    setNewPkgInput('');
  }, [newPkgInput, getParsedPkg, pushError]);

  const handleRemovePackage = useCallback((pkgName) => {
    const parsed = getParsedPkg();
    if (!parsed) return;
    delete parsed.dependencies[pkgName];
    setConfigFiles(prev => ({ ...prev, packageJson: JSON.stringify(parsed, null, 2) }));
    setConfigModified(prev => ({ ...prev, packageJson: true }));
    setPreviewDirty(true);
  }, [getParsedPkg]);

  const handleResetConfig = useCallback(() => {
    setConfigFiles(buildDefaultConfigFiles(selectedDS));
    setConfigModified({});
    setPreviewDirty(true);
  }, [selectedDS, buildDefaultConfigFiles]);

  // ═══════════════════════════════════════════════════════════════════════════
  // RENDER — HEADER
  // ═══════════════════════════════════════════════════════════════════════════
  const renderHeader = () => (
    <div className="ide-header">
      <div className="ide-header-left">
        <span className="ide-logo">ngForge</span>
        <span className="ide-subtitle">Angular Component Studio</span>
        <select
          className="ide-ds-select"
          value={selectedDS}
          onChange={e => setSelectedDS(e.target.value)}
          disabled={isGenerating}
        >
          {designSystems.length === 0 && <option value="">Loading...</option>}
          {designSystems.map(ds => <option key={ds} value={ds}>{ds}</option>)}
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

  // ═══════════════════════════════════════════════════════════════════════════
  // RENDER — CHAT PANEL
  // ═══════════════════════════════════════════════════════════════════════════
  const renderChatBubble = (msg, i) => {
    if (msg.role === 'user') {
      return (
        <div key={i} className="chat-bubble user">
          {typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content)}
        </div>
      );
    }
    const metaType = msg.metadata?.type;
    let extraClass = '', prefix = '';
    if (metaType === 'component_suggestion') { extraClass = 'suggestion'; prefix = '⟨/⟩ '; }
    else if (metaType === 'unresolved_notice') { extraClass = 'warning-msg'; prefix = '⚠ '; }
    else if (i === chatHistory.length - 1) {
      if (lastRefineAction === 'OUT_OF_SCOPE') extraClass = 'oos-msg';
      else if (lastRefineAction === 'CLARIFY') extraClass = 'clarify-msg';
    }
    const text = typeof msg.content === 'string' ? msg.content : JSON.stringify(msg.content);
    return (
      <div key={i} className={`chat-bubble assistant ${extraClass}`}>{prefix}{text}</div>
    );
  };

  const renderCoverageBar = () => {
    if (!dsCoverage) return null;
    const pct = dsCoverage.coverage_pct ?? 0;
    const uncovered = dsCoverage.uncovered_selectors || [];
    return (
      <div className="coverage-bar-wrap">
        <div className="coverage-label">DS Coverage: {pct.toFixed(1)}%</div>
        <div className="coverage-track"><div className="coverage-fill" style={{ width: `${pct}%` }} /></div>
        {uncovered.length > 0 && <div className="coverage-uncovered">Uncovered: {uncovered.join(', ')}</div>}
      </div>
    );
  };

  const renderChatPanel = () => (
    <div className="panel chat-panel" style={{ width: `${leftWidth}%` }}>
      <div className="input-sections-wrap">
        {/* Prompt */}
        <div className="input-section">
          <div className="input-section-header">
            <span className="section-label">Prompt</span>
          </div>
          <textarea
            className="chat-textarea"
            rows={4}
            placeholder={"Describe an Angular component, paste a Figma JSON, or upload a screenshot.\n\nCmd/Ctrl+Enter to send"}
            value={prompt}
            onChange={e => setPrompt(e.target.value)}
            onKeyDown={handlePromptKeyDown}
            disabled={isGenerating}
          />
        </div>

        {/* Figma JSON */}
        <div className="input-section">
          <div className="input-section-header">
            <span className="section-label clickable" onClick={() => !isGenerating && setFigmaExpanded(v => !v)}>
              <span className="section-toggle">{figmaExpanded ? '▾' : '▸'}</span>
              Figma JSON
              {figmaFile && <span className="input-badge">loaded</span>}
            </span>
            {figmaFile && (
              <button className="section-clear-btn" onClick={() => { setFigmaJsonText(''); setFigmaFile(null); }} disabled={isGenerating}>×</button>
            )}
          </div>
          {figmaExpanded && (
            <textarea
              className="chat-textarea"
              rows={5}
              placeholder="Paste Figma JSON tree here…"
              value={figmaJsonText}
              onChange={e => {
                const val = e.target.value;
                setFigmaJsonText(val);
                if (val.trim()) {
                  setFigmaFile(new File([new Blob([val], { type: 'application/json' })], 'figma.json', { type: 'application/json' }));
                } else {
                  setFigmaFile(null);
                }
              }}
              disabled={isGenerating}
            />
          )}
        </div>

        {/* Screenshot */}
        <div className="input-section">
          <div className="input-section-header">
            <span className="section-label">Screenshot</span>
            {screenshotFile && (
              <button className="section-clear-btn" onClick={() => {
                setScreenshotFile(null); setScreenshotBase64(null);
                if (screenshotPreviewUrl) URL.revokeObjectURL(screenshotPreviewUrl);
                setScreenshotPreviewUrl(null);
              }} disabled={isGenerating}>×</button>
            )}
          </div>
          <div
            className={`screenshot-compact ${screenshotFile ? 'has-file' : ''} ${dropOver ? 'drag-over' : ''}`}
            onClick={() => !isGenerating && fileInputRef.current?.click()}
            onDragOver={e => { e.preventDefault(); setDropOver(true); }}
            onDragLeave={() => setDropOver(false)}
            onDrop={e => { e.preventDefault(); setDropOver(false); const f = e.dataTransfer.files[0]; if (f && !isGenerating) handleScreenshotFile(f); }}
          >
            {screenshotPreviewUrl
              ? <><img src={screenshotPreviewUrl} alt="" className="screenshot-thumb" /><span className="screenshot-filename">{screenshotFile?.name}</span></>
              : <span>↑ Drop image or click to browse</span>}
          </div>
          <input ref={fileInputRef} type="file" accept="image/*" style={{ display: 'none' }}
            onChange={e => { if (e.target.files[0]) handleScreenshotFile(e.target.files[0]); }} />
        </div>
      </div>

      {/* Buttons */}
      <div className="chat-btns">
        <button
          className="btn-generate"
          onClick={handleGenerate}
          disabled={!session || isGenerating || (!prompt.trim() && !figmaFile && !screenshotFile)}
        >
          ⚡ Generate Component
        </button>
        {session?.has_generated_code && (
          <button className="btn-refine" onClick={handleRefine} disabled={!prompt.trim() || isGenerating}>
            ✦ Refine
          </button>
        )}
      </div>

      {isGenerating && (
        <div className="generating-status"><span className="blink">▋</span> Generating…</div>
      )}

      {renderCoverageBar()}

      <div className="chat-history" ref={chatListRef}>
        {chatHistory.map((msg, i) => renderChatBubble(msg, i))}
        {chatHistory.length === 0 && (
          <div style={{ color: 'var(--text-dim)', fontSize: '12px', textAlign: 'center', marginTop: '20px', fontFamily: 'var(--font-mono)' }}>
            No messages yet
          </div>
        )}
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // RENDER — EDITOR PANEL
  // ═══════════════════════════════════════════════════════════════════════════
  const isConfigArea = activeEditorArea === 'config';
  const currentContent = isConfigArea ? (configFiles[activeConfigTab] || '') : (files[activeFileTab] || '');
  const lineCount = currentContent.split('\n').length;
  const lineNumbers = Array.from({ length: lineCount }, (_, i) => i + 1).join('\n');
  const hasAnyCode = !!(files.html || files.scss || files.ts);
  const isCurrentModified = isConfigArea ? !!configModified[activeConfigTab] : !!fileModified[activeFileTab];
  const currentLabel = isConfigArea
    ? CONFIG_TABS.find(t => t.key === activeConfigTab)?.label
    : FILE_TABS.find(t => t.key === activeFileTab)?.label;

  const editorClasses = ['panel', 'editor-panel', generationFlash ? 'editor-flash' : '', isGenerating ? 'generating-pulse' : ''].filter(Boolean).join(' ');

  const renderNpmSection = () => {
    const parsed = getParsedPkg();
    if (!parsed) return null;
    const deps = parsed.dependencies || {};
    const dsPkgSet = DS_PKGS[selectedDS] || new Set();
    const corePkgs = Object.entries(deps).filter(([n]) => ANGULAR_CORE_PKGS.has(n));
    const dsPkgEntries = Object.entries(deps).filter(([n]) => dsPkgSet.has(n));
    const extraPkgs = Object.entries(deps).filter(([n]) => !ANGULAR_CORE_PKGS.has(n) && !dsPkgSet.has(n));
    return (
      <div className="npm-section">
        <div className="npm-title">npm Packages</div>

        {/* Angular Core */}
        <div className="pkg-group-header" onClick={() => setCoreExpanded(v => !v)}>
          <span>{coreExpanded ? '▾' : '▸'}</span>
          <span>Angular Core ({corePkgs.length})</span>
          <span style={{ fontSize: '9px', marginLeft: 4, color: 'var(--text-dim)' }}>locked</span>
        </div>
        {coreExpanded && corePkgs.map(([name, ver]) => (
          <div key={name} className="pkg-item">
            <span className="pkg-name">{name}</span>
            <span className="pkg-version">{ver}</span>
          </div>
        ))}

        {/* DS packages */}
        {dsPkgEntries.length > 0 && (
          <>
            <div style={{ padding: '4px 0 2px', fontSize: '10px', fontFamily: 'var(--font-mono)', color: 'var(--text-dim)' }}>
              {selectedDS} packages
            </div>
            {dsPkgEntries.map(([name, ver]) => (
              <div key={name} className="pkg-item">
                <span className="pkg-name">{name}</span>
                <span className="pkg-version">{ver}</span>
              </div>
            ))}
          </>
        )}

        {/* Extra packages */}
        {extraPkgs.map(([name, ver]) => (
          <div key={name} className="pkg-item">
            <span className="pkg-name">{name}</span>
            <span className="pkg-version">{ver}</span>
            <button className="pkg-remove" onClick={() => handleRemovePackage(name)}>×</button>
          </div>
        ))}

        {/* Add package */}
        <div className="pkg-add-row">
          <input
            className="pkg-input"
            placeholder="@ng-bootstrap/ng-bootstrap@^14"
            value={newPkgInput}
            onChange={e => setNewPkgInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && handleAddPackage()}
          />
          <button className="btn" style={{ padding: '3px 8px', fontSize: '11px' }} onClick={handleAddPackage}>Add</button>
        </div>
        {newPkgInput && (
          <div className="pkg-warning">⚠ Adding packages triggers a full npm install. This may take 30–60 seconds.</div>
        )}
      </div>
    );
  };

  const renderEditorPanel = () => (
    <div className={editorClasses} style={{ width: `${centerWidth}%` }}>
      {/* Toolbar */}
      <div className="editor-toolbar">
        <div className="editor-toolbar-left">
          <span style={{ fontSize: '12px', color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>{currentLabel}</span>
        </div>
        <div className="editor-toolbar-right">
          {isCurrentModified
            ? <span className="modified-indicator" style={{ fontSize: '11px' }}>● Modified</span>
            : hasAnyCode ? <span className="synced-indicator" style={{ fontSize: '11px' }}>● Synced</span> : null}
          <button className="btn" style={{ padding: '2px 8px', fontSize: '11px' }}
            onClick={() => navigator.clipboard?.writeText(currentContent)} disabled={!currentContent}>
            Copy
          </button>
          {!isConfigArea && (
            <button className="btn" style={{ padding: '2px 8px', fontSize: '11px' }}
              onClick={() => { setFiles(prev => ({ ...prev, [activeFileTab]: lastGenerated[activeFileTab] })); setFileModified(prev => ({ ...prev, [activeFileTab]: false })); }}
              disabled={!fileModified[activeFileTab]}>
              Reset
            </button>
          )}
        </div>
      </div>

      {/* Component file tabs */}
      <div className="editor-file-tabs">
        {FILE_TABS.map(({ key, label }) => (
          <div
            key={key}
            className={`editor-tab ${!isConfigArea && activeFileTab === key ? 'active' : ''}`}
            onClick={() => { setActiveFileTab(key); setActiveEditorArea('component'); }}
          >
            {label}
            {fileModified[key] && <span className="modified-dot">●</span>}
          </div>
        ))}
      </div>

      {/* Config collapsible */}
      <div className="config-section">
        <div className="config-section-header" onClick={() => setConfigExpanded(v => !v)}>
          <span>⚙ Angular Config Files</span>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {Object.values(configModified).some(Boolean) && (
              <span style={{ fontSize: '10px', color: 'var(--warning)' }}>Modified</span>
            )}
            <button
              className="btn"
              style={{ padding: '1px 6px', fontSize: '10px' }}
              onClick={e => { e.stopPropagation(); handleResetConfig(); }}
            >
              Reset Config
            </button>
            <span>{configExpanded ? '▼' : '▶'}</span>
          </div>
        </div>

        {configExpanded && (
          <>
            {renderNpmSection()}
            <div className="config-tabs">
              {CONFIG_TABS.map(({ key, label }) => (
                <div
                  key={key}
                  className={`config-tab ${isConfigArea && activeConfigTab === key ? 'active' : ''}`}
                  onClick={() => { setActiveConfigTab(key); setActiveEditorArea('config'); }}
                >
                  {label}
                  {configModified[key] && <span className="modified-dot" style={{ fontSize: '10px' }}>●</span>}
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      {/* Editor body */}
      <div className="editor-body">
        {!hasAnyCode && !isConfigArea && (
          <div className="editor-empty">// Generate a component to see Angular code here</div>
        )}
        <div ref={gutterRef} className="line-gutter">{lineNumbers}</div>
        <textarea
          ref={textareaEditorRef}
          className="editor-textarea"
          value={currentContent}
          spellCheck={false}
          wrap="off"
          onChange={e => {
            const val = e.target.value;
            if (isConfigArea) {
              setConfigFiles(prev => ({ ...prev, [activeConfigTab]: val }));
              setConfigModified(prev => ({ ...prev, [activeConfigTab]: true }));
              setPreviewDirty(true);
            } else {
              setFiles(prev => ({ ...prev, [activeFileTab]: val }));
              setFileModified(prev => ({ ...prev, [activeFileTab]: true }));
              setPreviewDirty(true);
            }
          }}
          onScroll={e => { if (gutterRef.current) gutterRef.current.scrollTop = e.target.scrollTop; }}
        />
      </div>

      {/* Status bar */}
      <div className="editor-status">
        <span>{currentLabel?.split('.').pop()?.toUpperCase()}</span>
        <span>{currentContent.length} chars</span>
        <span>{lineCount} lines</span>
      </div>
    </div>
  );

  // ═══════════════════════════════════════════════════════════════════════════
  // RENDER — PREVIEW PANEL
  // ═══════════════════════════════════════════════════════════════════════════
  const rightWidth = 100 - leftWidth - centerWidth;
  const hasFiles = !!(files.html || files.ts);

  const renderRunButton = () => {
    if (previewLoading) {
      return (
        <button className="btn-rerun loading-pulse" disabled style={{ padding: '3px 10px', fontSize: '11px' }}>
          ◌ Loading
        </button>
      );
    }
    if (previewDirty) {
      return (
        <button className="btn-run" onClick={handleRenderPreview} disabled={!sbkReady} style={{ padding: '3px 10px', fontSize: '11px' }}>
          ▶ Run
        </button>
      );
    }
    return (
      <button className="btn-rerun" onClick={handleRenderPreview} disabled={!sbkReady || previewLoading} style={{ padding: '3px 10px', fontSize: '11px' }}>
        ↺ Re-run
      </button>
    );
  };

  const renderPreviewPanel = () => (
    <div className="panel preview-panel" style={{ width: `${rightWidth}%` }}>
      {/* Toolbar */}
      <div className="preview-toolbar">
        <span style={{ fontSize: '11px', color: 'var(--text-dim)', fontFamily: 'var(--font-mono)' }}>
          Preview
          {!sbkReady && <span style={{ marginLeft: 6, color: 'var(--warning)' }}>· loading SDK…</span>}
        </span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {renderRunButton()}
          <button
            className="btn"
            style={{ padding: '2px 8px', fontSize: '11px' }}
            onClick={handleOpenInStackBlitz}
            disabled={!currentProjectRef.current || previewLoading}
          >
            Open in StackBlitz ↗
          </button>
        </div>
      </div>

      {/* Preview body */}
      <div className="preview-body">
        <div className="preview-sbk-wrap">
          {!hasFiles && (
            <div className="preview-empty">
              <AngularLogo />
              <span>Run a generation to preview the live component.</span>
            </div>
          )}
          <div
            ref={sbkContainerRef}
            className="sbk-container"
            style={{ display: hasFiles ? 'block' : 'none' }}
          />
        </div>

        {/* Error console */}
        <div className="error-console">
          <div className="error-console-header" onClick={() => setErrorConsoleOpen(o => !o)}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {errorLog.length > 0
                ? <><span className="error-dot" /><span style={{ color: 'var(--error)' }}>● {errorLog.length} error{errorLog.length !== 1 ? 's' : ''}</span></>
                : <span style={{ color: 'var(--text-dim)' }}>○ No errors</span>}
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {errorLog.length > 0 && (
                <button className="btn" style={{ padding: '1px 6px', fontSize: '10px' }}
                  onClick={e => { e.stopPropagation(); setErrorLog([]); }}>
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
  // MAIN RENDER
  // ═══════════════════════════════════════════════════════════════════════════
  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden', background: 'var(--bg-primary)' }}>
      {renderHeader()}
      <div className="ide-main">
        {renderChatPanel()}
        <div className={`drag-handle ${dragging === 'left' ? 'dragging' : ''}`} onMouseDown={e => startDrag('left', e)} />
        {renderEditorPanel()}
        <div className={`drag-handle ${dragging === 'right' ? 'dragging' : ''}`} onMouseDown={e => startDrag('right', e)} />
        {renderPreviewPanel()}
      </div>
    </div>
  );
}
