/* ============================================================
   Remnant Web UI — Alpine.js App
   ============================================================ */

/* ---- Markdown + highlight setup ---- */
marked.setOptions({
  breaks: true,
  gfm: true,
  highlight(code, lang) {
    if (lang && hljs.getLanguage(lang)) {
      return hljs.highlight(code, { language: lang }).value;
    }
    return hljs.highlightAuto(code).value;
  },
});

/* ---- Badge parsing ---- */
const BADGE_MAP = {
  '[GEN]': 'gen', '[USE]': 'use', '[EXE]': 'exe',
  '[MCP]': 'mcp', '[MEM]': 'mem', '[REC]': 'rec',
  '[ERR]': 'err', '[SYS]': 'sys',
};

const BADGE_LABELS = {
  gen: 'Generating', use: 'Using tool', exe: 'Executing',
  mcp: 'MCP', mem: 'Memory', rec: 'Recording', err: 'Error', sys: 'System',
};

function parseBadgeLine(text) {
  for (const [tag, cls] of Object.entries(BADGE_MAP)) {
    if (text.startsWith(tag)) {
      return { badge: tag.slice(1, -1), cls, text: text.slice(tag.length).trim() };
    }
  }
  return null;
}

function parseAgentOutput(raw) {
  const lines = raw.split('\n');
  const parts = [];
  let mdBuf = [];

  const flushMd = () => {
    const t = mdBuf.join('\n').trim();
    if (t) parts.push({ type: 'markdown', text: t });
    mdBuf = [];
  };

  for (const line of lines) {
    const b = parseBadgeLine(line.trim());
    if (b) {
      flushMd();
      parts.push({ type: 'badge', ...b });
    } else {
      mdBuf.push(line);
    }
  }
  flushMd();
  return parts;
}

/* ---- LocalStorage helpers ---- */
const LS_CHATS  = 'remnant_chats_v2';
const LS_WIZARD = 'remnant_wizard_done';

function saveChats(chats) {
  try {
    const toSave = chats.slice(0, 50).map(c => ({
      ...c,
      messages: (c.messages || []).slice(-200).map(m => ({ ...m, streaming: false })),
    }));
    localStorage.setItem(LS_CHATS, JSON.stringify(toSave));
  } catch (_) {}
}

function loadChats() {
  try {
    const raw = localStorage.getItem(LS_CHATS);
    return raw ? (JSON.parse(raw) || []) : [];
  } catch (_) { return []; }
}

/* ---- Misc helpers ---- */
function tsNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function uid() { return Math.random().toString(36).slice(2); }
function renderMd(text) { return marked.parse(text || ''); }

/* ============================================================
   Alpine.js main component
   ============================================================ */
document.addEventListener('alpine:init', () => {

  Alpine.data('remnantApp', () => ({
    /* --- WS state --- */
    ws: null,
    wsStatus: 'disconnected',
    wsRetries: 0,
    wsRetryTimer: null,

    /* --- Projects --- */
    projects: [],              // from API
    activeProjectId: null,
    expandedProjects: new Set(),
    editingProjectId: null,
    editingProjectName: '',

    /* --- Chats --- */
    chats: [],
    activeChatId: null,
    get activeChat() { return this.chats.find(c => c.id === this.activeChatId) || null; },
    get activeMessages() { return this.activeChat?.messages || []; },

    /* --- View state --- */
    activeView: null,         // null = chat, 'project-memory-<id>' = project memory view
    projectMemoryContent: '',

    /* --- Input --- */
    inputText: '',
    isStreaming: false,
    currentActivity: '',
    sseAbort: null,

    /* --- Memory panel --- */
    memoryOpen: false,
    memoryTab: 'recent',
    memorySearch: '',
    memoryChunks: [],
    memoryStats: {},
    memoryFiles: [],
    editingFile: null,
    editingFileContent: '',

    /* --- Settings modal --- */
    settingsOpen: false,
    settingsTab: 'api',
    settingsData: {},
    usageSummary: {},
    availableModels: [],
    connectorForm: { telegram: '', whatsapp: '' },
    settingsEditingFile: null,
    settingsEditingContent: '',

    /* --- Wizard --- */
    wizardOpen: false,
    wizardStep: 1,
    wizardTotal: 4,
    wizardSaving: false,
    wizardMemoryContent: '',
    wizardUser: { name: '', location: '', prefs: '' },
    wizardKeys: { openrouter: '', anthropic: '', openai: '' },
    wizardConnectors: { telegram: '', whatsapp: '' },

    /* ================================================================
       COMPUTED
       ================================================================ */
    get sidebarProjects() {
      return this.projects.map(p => ({
        id: p.project_id,
        name: p.name || p.project_id,
      }));
    },

    get activeProjectName() {
      if (!this.activeProjectId) return 'Default';
      const p = this.projects.find(p => p.project_id === this.activeProjectId);
      return p ? (p.name || p.project_id) : this.activeProjectId;
    },

    chatsForProject(projectId) {
      return this.chats.filter(c => (c.projectId || null) === projectId);
    },

    /* ================================================================
       INIT
       ================================================================ */
    async init() {
      const saved = loadChats();
      if (saved.length > 0) {
        this.chats = saved;
        this.activeChatId = saved[0].id;
      } else {
        this.newChat();
      }

      this.connectWS();
      await this.loadProjects();
      await this.loadMemoryStats();

      // Auto-save chats whenever they change
      this.$watch('chats', (v) => saveChats(v), { deep: true });

      // Show wizard on first launch
      if (!localStorage.getItem(LS_WIZARD)) {
        await this.startWizard();
      }
    },

    /* ================================================================
       WEBSOCKET
       ================================================================ */
    wsUrl() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      return `${proto}://${location.host}/api/ws`;
    },

    connectWS() {
      if (this.ws) {
        try { this.ws.onclose = null; this.ws.close(); } catch (_) {}
        this.ws = null;
      }
      try {
        this.ws = new WebSocket(this.wsUrl());
      } catch (e) {
        this.wsStatus = 'disconnected';
        this.scheduleReconnect();
        return;
      }
      this.ws.onopen = () => {
        this.wsStatus = 'connected';
        this.wsRetries = 0;
        clearTimeout(this.wsRetryTimer);
        this.wsRetryTimer = null;
        if (this.isStreaming) {
          this.isStreaming = false;
          this.currentActivity = '';
          const last = this.activeChat?.messages?.at(-1);
          if (last?.streaming) {
            last.streaming = false;
            last.raw += '\n[SYS] Connection restored — please resend.';
            last.parts = parseAgentOutput(last.raw);
          }
        }
      };
      this.ws.onclose = () => {
        this.wsStatus = 'disconnected';
        if (this.isStreaming) {
          this.isStreaming = false;
          this.currentActivity = '';
          const last = this.activeChat?.messages?.at(-1);
          if (last?.streaming) {
            last.streaming = false;
            last.raw += '\n[ERR] Connection lost.';
            last.parts = parseAgentOutput(last.raw);
          }
        }
        this.scheduleReconnect();
      };
      this.ws.onerror = () => { this.wsStatus = 'degraded'; };
      this.ws.onmessage = (ev) => {
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        this.handleWsMessage(msg);
      };
    },

    scheduleReconnect() {
      if (this.wsRetryTimer) return;
      const delay = Math.min(1000 * Math.pow(2, this.wsRetries), 16000);
      this.wsRetryTimer = setTimeout(() => {
        this.wsRetryTimer = null;
        this.wsRetries++;
        this.connectWS();
      }, delay);
    },

    handleWsMessage(msg) {
      const chat = this.activeChat;
      if (!chat) return;
      if (msg.type === 'start') return;

      if (msg.type === 'chunk') {
        const last = chat.messages.at(-1);
        if (last?.role === 'agent') {
          last.raw += msg.content || '';
          last.parts = parseAgentOutput(last.raw);
          const lastBadge = [...last.parts].reverse().find(p => p.type === 'badge');
          if (lastBadge) this.currentActivity = BADGE_LABELS[lastBadge.cls] || lastBadge.badge;
        }
        this.scrollToBottom();
        return;
      }

      if (msg.type === 'done') {
        this.isStreaming = false;
        this.currentActivity = '';
        const last = chat.messages.at(-1);
        if (last?.role === 'agent') {
          last.streaming = false;
          last.parts = parseAgentOutput(last.raw);
        }
        this._autoTitle(chat);
        this.scrollToBottom();
        return;
      }

      if (msg.error) {
        this.isStreaming = false;
        this.currentActivity = '';
        const last = chat.messages.at(-1);
        if (last?.role === 'agent') {
          last.raw += `\n[ERR] ${msg.error}`;
          last.parts = parseAgentOutput(last.raw);
          last.streaming = false;
        }
      }
    },

    /* ================================================================
       SEND
       ================================================================ */
    async send() {
      const text = this.inputText.trim();
      if (!text || this.isStreaming) return;

      // Ensure we're in chat view
      this.activeView = null;

      if (!this.activeChat) this.newChat();
      const chat = this.activeChat;

      chat.messages.push({ id: uid(), role: 'user', text, ts: tsNow() });
      this.inputText = '';
      this.autoResize();
      this.scrollToBottom();

      const agentMsg = {
        id: uid(), role: 'agent', raw: '', parts: [],
        streaming: true, ts: tsNow(), stepOpen: true,
      };
      chat.messages.push(agentMsg);
      this.isStreaming = true;
      this.currentActivity = 'Thinking';
      this.scrollToBottom();

      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({
          message: text,
          project_id: this.activeProjectId || null,
          session_id: chat.sessionId,
        }));
      } else {
        await this.sendViaSSE(text, chat, agentMsg);
      }
    },

    stop() {
      if (!this.isStreaming) return;
      if (this.sseAbort) { this.sseAbort.abort(); this.sseAbort = null; }
      this.isStreaming = false;
      this.currentActivity = '';
      const last = this.activeChat?.messages?.at(-1);
      if (last?.streaming) {
        last.streaming = false;
        last.raw += '\n[SYS] Stopped by user.';
        last.parts = parseAgentOutput(last.raw);
      }
    },

    async sendViaSSE(text, chat, agentMsg) {
      const controller = new AbortController();
      this.sseAbort = controller;
      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: text,
            project_id: this.activeProjectId || null,
            session_id: chat.sessionId,
            channel: 'api',
          }),
          signal: controller.signal,
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop();
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const json = line.slice(6).trim();
            if (!json) continue;
            try {
              const msg = JSON.parse(json);
              if (msg.type === 'chunk') {
                agentMsg.raw += msg.content || '';
                agentMsg.parts = parseAgentOutput(agentMsg.raw);
                const lastBadge = [...agentMsg.parts].reverse().find(p => p.type === 'badge');
                if (lastBadge) this.currentActivity = BADGE_LABELS[lastBadge.cls] || lastBadge.badge;
                this.scrollToBottom();
              } else if (msg.type === 'done') {
                agentMsg.streaming = false;
                this.isStreaming = false;
                this.currentActivity = '';
                this._autoTitle(chat);
                this.scrollToBottom();
              }
            } catch (_) {}
          }
        }
      } catch (e) {
        if (e.name !== 'AbortError') {
          agentMsg.raw += `\n[ERR] Failed to connect: ${e.message}`;
          agentMsg.parts = parseAgentOutput(agentMsg.raw);
        }
        agentMsg.streaming = false;
        this.isStreaming = false;
        this.currentActivity = '';
      } finally {
        this.sseAbort = null;
      }
    },

    _autoTitle(chat) {
      if (chat.title === 'New chat') {
        const firstUser = chat.messages.find(m => m.role === 'user');
        if (firstUser) chat.title = firstUser.text.slice(0, 40) + (firstUser.text.length > 40 ? '…' : '');
      }
    },

    /* ================================================================
       CHAT MANAGEMENT
       ================================================================ */
    newChat() {
      const chat = {
        id: uid(),
        title: 'New chat',
        projectId: this.activeProjectId,
        sessionId: uid(),
        messages: [],
        ts: new Date().toISOString(),
      };
      this.chats.unshift(chat);
      this.activeChatId = chat.id;
      this.activeView = null;
    },

    newChatInProject(projectId) {
      this.activeProjectId = projectId;
      this.newChat();
    },

    selectChat(id) {
      if (this.isStreaming) return;
      this.activeChatId = id;
      this.activeView = null;
      this.$nextTick(() => this.scrollToBottom());
    },

    deleteChat(id) {
      this.chats = this.chats.filter(c => c.id !== id);
      if (this.activeChatId === id) {
        if (this.chats.length > 0) this.activeChatId = this.chats[0].id;
        else this.newChat();
      }
    },

    /* ================================================================
       PROJECTS
       ================================================================ */
    async loadProjects() {
      try {
        const resp = await fetch('/api/projects');
        if (resp.ok) {
          const data = await resp.json();
          this.projects = Array.isArray(data) ? data : (data.projects || []);
        }
      } catch (_) {}
    },

    async createProject() {
      const name = prompt('Project name:');
      if (!name?.trim()) return;
      try {
        const resp = await fetch('/api/projects', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: name.trim() }),
        });
        if (resp.ok) {
          const data = await resp.json();
          const proj = data.project || data;
          this.projects.unshift(proj);
          this.expandedProjects = new Set([...this.expandedProjects, proj.project_id]);
          this.activeProjectId = proj.project_id;
          this.newChat();
        }
      } catch (_) {}
    },

    async deleteProject(projectId) {
      if (!confirm('Delete this project?')) return;
      try {
        const resp = await fetch(`/api/projects/${projectId}`, { method: 'DELETE' });
        if (resp.ok) {
          this.projects = this.projects.filter(p => p.project_id !== projectId);
          if (this.activeProjectId === projectId) {
            this.activeProjectId = null;
            this.newChat();
          }
        }
      } catch (_) {}
    },

    toggleProject(projectId) {
      const s = new Set(this.expandedProjects);
      if (s.has(projectId)) {
        s.delete(projectId);
      } else {
        s.add(projectId);
        this.activeProjectId = projectId;
      }
      this.expandedProjects = s;
    },

    startEditProject(id, name) {
      this.editingProjectId = id;
      this.editingProjectName = name;
      this.$nextTick(() => {
        const inp = document.querySelector('.proj-name-input');
        if (inp) { inp.focus(); inp.select(); }
      });
    },

    async saveProjectName(projectId) {
      const name = this.editingProjectName.trim();
      this.editingProjectId = null;
      if (!name) return;
      try {
        const resp = await fetch(`/api/projects/${projectId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name }),
        });
        if (resp.ok) {
          const data = await resp.json();
          const updated = data.project || data;
          const idx = this.projects.findIndex(p => p.project_id === projectId);
          if (idx >= 0) this.projects[idx] = { ...this.projects[idx], ...updated };
        }
      } catch (_) {}
    },

    /* ================================================================
       PROJECT MEMORY VIEW
       ================================================================ */
    async showProjectMemory(projectId) {
      this.activeView = `project-memory-${projectId}`;
      this.activeProjectId = projectId;
      // Load the project's memory file
      const proj = this.projects.find(p => p.project_id === projectId);
      const name = proj?.project_id || projectId;
      try {
        const resp = await fetch(`/api/memory/file?path=projects/${name}.md`);
        if (resp.ok) {
          const data = await resp.json();
          this.projectMemoryContent = data.content || '';
        } else {
          this.projectMemoryContent = `# Project: ${proj?.name || name}\n\n## Notes\n\n`;
        }
      } catch (_) {
        this.projectMemoryContent = `# Project: ${proj?.name || name}\n\n## Notes\n\n`;
      }
    },

    async saveProjectMemoryFile() {
      const projectId = this.activeView?.replace('project-memory-', '');
      if (!projectId) return;
      try {
        const resp = await fetch('/api/memory/file', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: `projects/${projectId}.md`, content: this.projectMemoryContent }),
        });
        if (resp.ok) alert('Project memory saved!');
      } catch (_) {}
    },

    /* ================================================================
       MEMORY PANEL
       ================================================================ */
    async toggleMemory() {
      this.memoryOpen = !this.memoryOpen;
      if (this.memoryOpen) {
        await this.loadRecentMemory();
        await this.loadMemoryStats();
      }
    },

    async loadMemoryStats() {
      try {
        const resp = await fetch('/api/memory/stats');
        if (resp.ok) this.memoryStats = await resp.json();
      } catch (_) {}
    },

    async loadRecentMemory() {
      try {
        const url = '/api/memory/recent?limit=30' + (this.activeProjectId ? `&project_id=${this.activeProjectId}` : '');
        const resp = await fetch(url);
        if (resp.ok) {
          const data = await resp.json();
          this.memoryChunks = Array.isArray(data) ? data : (data.chunks || []);
        }
      } catch (_) {}
    },

    async searchMemory() {
      const q = this.memorySearch.trim();
      if (!q) return this.loadRecentMemory();
      try {
        const resp = await fetch('/api/memory/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ query: q, project_id: this.activeProjectId || null, top_k: 20 }),
        });
        if (resp.ok) {
          const data = await resp.json();
          this.memoryChunks = Array.isArray(data) ? data : (data.chunks || data.results || []);
        }
      } catch (_) {}
    },

    async deleteChunk(chunkId) {
      if (!chunkId) return;
      if (!confirm('Delete this memory chunk?')) return;
      try {
        const resp = await fetch(`/api/memory/chunk/${chunkId}`, { method: 'DELETE' });
        if (resp.ok) {
          this.memoryChunks = this.memoryChunks.filter(c => (c.chunk_id || c.id) !== chunkId);
          await this.loadMemoryStats();
        }
      } catch (_) {}
    },

    async loadMemoryFiles() {
      try {
        const resp = await fetch('/api/memory/files');
        if (resp.ok) {
          const data = await resp.json();
          this.memoryFiles = data.files || [];
        }
      } catch (_) {}
    },

    async openMemoryFile(path) {
      try {
        const resp = await fetch(`/api/memory/file?path=${encodeURIComponent(path)}`);
        if (resp.ok) {
          const data = await resp.json();
          this.editingFile = path;
          this.editingFileContent = data.content || '';
        }
      } catch (_) {}
    },

    async saveMemoryFile() {
      if (!this.editingFile) return;
      try {
        const resp = await fetch('/api/memory/file', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: this.editingFile, content: this.editingFileContent }),
        });
        if (resp.ok) {
          this.editingFile = null;
          await this.loadMemoryFiles();
        }
      } catch (_) {}
    },

    /* ================================================================
       SETTINGS
       ================================================================ */
    async openSettings() {
      this.settingsOpen = true;
      await this.loadSettingsData();
    },

    async loadSettingsData() {
      try {
        const resp = await fetch('/api/settings');
        if (resp.ok) this.settingsData = await resp.json();
      } catch (_) {}
    },

    async saveApiKey(name, value) {
      if (!value?.trim()) return;
      try {
        const resp = await fetch('/api/settings/api-key', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name, value: value.trim() }),
        });
        if (resp.ok) await this.loadSettingsData();
      } catch (_) {}
    },

    async loadModels() {
      try {
        const resp = await fetch('/api/llm/providers');
        if (resp.ok) {
          const data = await resp.json();
          this.availableModels = data.models || [];
        }
      } catch (_) {}
    },

    async setDefaultModel(key) {
      try {
        const resp = await fetch('/api/settings/model', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ model_key: key }),
        });
        if (resp.ok) {
          this.settingsData = { ...this.settingsData, default_model: key };
        }
      } catch (_) {}
    },

    async loadConnectors() {
      await this.loadSettingsData();
      this.connectorForm.whatsapp = this.settingsData.connectors?.whatsapp_sidecar_url || '';
    },

    async saveConnectors() {
      try {
        const body = {};
        if (this.connectorForm.telegram) body.telegram_bot_token = this.connectorForm.telegram;
        if (this.connectorForm.whatsapp) body.whatsapp_sidecar_url = this.connectorForm.whatsapp;
        const resp = await fetch('/api/settings/connectors', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (resp.ok) await this.loadSettingsData();
      } catch (_) {}
    },

    async openSettingsMemoryFile(path) {
      try {
        const resp = await fetch(`/api/memory/file?path=${encodeURIComponent(path)}`);
        if (resp.ok) {
          const data = await resp.json();
          this.settingsEditingFile = path;
          this.settingsEditingContent = data.content || '';
        }
      } catch (_) {}
    },

    async saveSettingsMemoryFile() {
      if (!this.settingsEditingFile) return;
      try {
        const resp = await fetch('/api/memory/file', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: this.settingsEditingFile, content: this.settingsEditingContent }),
        });
        if (resp.ok) {
          alert('Saved!');
          this.settingsEditingFile = null;
          await this.loadMemoryFiles();
        }
      } catch (_) {}
    },

    async loadUsage() {
      try {
        const resp = await fetch('/api/llm/usage');
        if (resp.ok) this.usageSummary = await resp.json();
      } catch (_) {}
    },

    /* ================================================================
       WIZARD
       ================================================================ */
    async startWizard() {
      // Load current MEMORY.md content
      try {
        const resp = await fetch('/api/memory/file?path=MEMORY.md');
        if (resp.ok) {
          const data = await resp.json();
          this.wizardMemoryContent = data.content || '';
        }
      } catch (_) {}
      this.wizardStep = 1;
      this.wizardOpen = true;
    },

    async wizardNext() {
      this.wizardSaving = true;
      try {
        if (this.wizardStep === 1) {
          // Save MEMORY.md
          await fetch('/api/memory/file', {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: 'MEMORY.md', content: this.wizardMemoryContent }),
          });
        } else if (this.wizardStep === 2) {
          // Save user identity to MEMORY.md
          if (this.wizardUser.name) {
            let profile = `\n## [identity] User Profile\n\n`;
            if (this.wizardUser.name) profile += `- **Name**: ${this.wizardUser.name}\n`;
            if (this.wizardUser.location) profile += `- **Location**: ${this.wizardUser.location}\n`;
            if (this.wizardUser.prefs) profile += `- **Preferences**: ${this.wizardUser.prefs}\n`;
            const existing = this.wizardMemoryContent || '';
            await fetch('/api/memory/file', {
              method: 'PUT',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ path: 'MEMORY.md', content: existing + profile }),
            });
            // Also record as a preference chunk
            await fetch('/api/memory/record', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                text: `User profile: ${JSON.stringify(this.wizardUser)}`,
                chunk_type: 'preference',
                source: 'wizard',
              }),
            });
          }
        } else if (this.wizardStep === 3) {
          // Save API keys
          const keyMap = {
            openrouter: 'OPENROUTER_API_KEY',
            anthropic: 'ANTHROPIC_API_KEY',
            openai: 'OPENAI_API_KEY',
          };
          for (const [k, envName] of Object.entries(keyMap)) {
            if (this.wizardKeys[k]?.trim()) {
              await fetch('/api/settings/api-key', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ name: envName, value: this.wizardKeys[k].trim() }),
              });
            }
          }
        } else if (this.wizardStep === 4) {
          // Save connectors
          const body = {};
          if (this.wizardConnectors.telegram) body.telegram_bot_token = this.wizardConnectors.telegram;
          if (this.wizardConnectors.whatsapp) body.whatsapp_sidecar_url = this.wizardConnectors.whatsapp;
          if (Object.keys(body).length > 0) {
            await fetch('/api/settings/connectors', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(body),
            });
          }
        }
      } catch (_) {}

      this.wizardSaving = false;

      if (this.wizardStep < this.wizardTotal) {
        this.wizardStep++;
      } else {
        this.wizardFinish();
      }
    },

    wizardBack() {
      if (this.wizardStep > 1) this.wizardStep--;
    },

    wizardSkip() {
      if (this.wizardStep < this.wizardTotal) {
        this.wizardStep++;
      } else {
        this.wizardFinish();
      }
    },

    wizardFinish() {
      localStorage.setItem(LS_WIZARD, '1');
      this.wizardOpen = false;
    },

    /* ================================================================
       UI HELPERS
       ================================================================ */
    scrollToBottom() {
      this.$nextTick(() => {
        const el = document.getElementById('chatArea');
        if (el) el.scrollTop = el.scrollHeight;
      });
    },

    autoResize() {
      this.$nextTick(() => {
        const el = document.getElementById('msgInput');
        if (el) {
          el.style.height = 'auto';
          el.style.height = Math.min(el.scrollHeight, 150) + 'px';
        }
      });
    },

    handleKeydown(e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        if (this.isStreaming) this.stop();
        else this.send();
      }
      if (e.key === 'Escape' && this.isStreaming) this.stop();
    },

    badgeCls(cls) { return `badge ${cls}`; },
    hasBadges(msg) { return msg.parts && msg.parts.some(p => p.type === 'badge'); },
    finalMdParts(msg) { return (msg.parts || []).filter(p => p.type === 'markdown'); },
    badgeParts(msg) { return (msg.parts || []).filter(p => p.type === 'badge'); },

    get statusLabel() {
      if (this.isStreaming) return this.currentActivity || 'Thinking…';
      return { connected: 'Connected', degraded: 'Reconnecting…', disconnected: 'Offline' }[this.wsStatus] || 'Offline';
    },
    get statusCls() {
      if (this.isStreaming) return 'streaming';
      return this.wsStatus;
    },

    formatScore(s) { return parseFloat(s || 0).toFixed(1); },
    chunkLabel(chunk) {
      const text = chunk.text_excerpt || chunk.text || '';
      return text.length > 120 ? text.slice(0, 120) + '…' : text;
    },
    usagePercent(used, max) {
      if (!max || !used) return 0;
      return Math.min(100, Math.round((used / max) * 100));
    },
    formatBytes(b) {
      if (!b) return '0 B';
      if (b < 1024) return b + ' B';
      return (b / 1024).toFixed(1) + ' KB';
    },
  }));
});
