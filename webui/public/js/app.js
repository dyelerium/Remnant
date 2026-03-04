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
function renderMd(text) {
  if (typeof marked === 'undefined') return `<p>${(text || '').replace(/\n/g, '<br>')}</p>`;
  return marked.parse(text || '');
}
function renderMdWithCopy(text) {
  if (typeof marked === 'undefined') return `<p>${(text || '').replace(/\n/g, '<br>')}</p>`;
  const html = marked.parse(text || '');
  return html
    .replace(
      /<pre><code/g,
      '<pre class="code-block"><button class="copy-code-btn" onclick="copyCode(this)">Copy</button><code'
    )
    .replace(
      /<img /g,
      '<img onclick="openImageLightbox(this.src)" '
    );
}
function copyCode(btn) {
  const code = btn.nextElementSibling?.textContent || '';
  navigator.clipboard.writeText(code).then(() => {
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy'; }, 1500);
  });
}
function openImageLightbox(src) {
  const el = document.querySelector('[x-data]');
  if (el && el._x_dataStack) {
    const data = el._x_dataStack[0];
    data.imageModalSrc = src;
    data.imageModalOpen = true;
    data.imageZoom = 1;
  }
}

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

    /* --- Mobile sidebar --- */
    sidebarOpen: false,

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
    inputFocused: false,
    pendingImage: null,
    pendingImagePreview: null,

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

    /* --- Ollama --- */
    ollamaUrl: '',
    ollamaApiKey: '',
    ollamaTesting: false,
    ollamaTestResult: null,

    /* --- Models tab redesign --- */
    modelProviderTab: 'openrouter',
    modelConfigPanel: null,   // key of model whose config panel is open
    modelConfigForm: {},      // { [model_key]: { field: value, ... } }
    modelRefreshing: false,
    remoteModelDiff: [],      // new/updated models from remote fetch
    costBreakdown: [],

    /* --- WhatsApp QR --- */
    waStatus: null,       // { sidecar_reachable, ready, sidecar_url }
    waQrData: null,       // base64 data URL of QR image
    waQrError: null,
    waQrLoading: false,
    waQrPollTimer: null,  // setInterval handle for auto-refresh
    waStarting: false,
    waStartError: null,

    /* --- Image modal --- */
    imageModalOpen: false,
    imageModalSrc: null,
    imageZoom: 1,

    /* --- Wizard --- */
    wizardOpen: false,
    wizardStep: 1,
    wizardTotal: 4,
    wizardSaving: false,
    wizardMemoryContent: '',
    wizardUser: { name: '', location: '', prefs: '' },
    wizardKeys: { openrouter: '', anthropic: '', openai: '', nvidia: '', moonshot: '' },
    wizardConnectors: { telegram: '', whatsapp: '' },

    /* --- Agents tab --- */
    agentsData: null,   // { agents: {...}, routing: {...} }
    agentsEdits: {},    // local edits buffer keyed by agent name
    allTools: ['filesystem', 'web_search', 'code_exec', 'http_client', 'memory_retrieve', 'memory_record', 'config', 'shell', 'n8n'],

    /* --- Archive view --- */
    showArchived: false,

    /* --- Budget mode (persists across send; toggled locally or saved globally) --- */
    budgetModeEnabled: false,

    /* --- Skills tab --- */
    skillsList: [],
    skillsLoading: false,
    skillTestName: '',
    skillTestArgs: '{}',
    skillTestResult: null,
    skillTestRunning: false,
    skillImportYaml: '',
    skillImportStatus: null,

    /* --- MCP tab --- */
    mcpTools: [],
    mcpTestTool: '',
    mcpTestArgs: '{}',
    mcpTestResult: null,
    mcpTestRunning: false,

    /* --- Admin tab --- */
    adminLoaded: false,
    budgetForm: { max_cost_usd_per_day: null, max_tokens_per_day: null },
    compacting: false, compactionResult: null,
    agentGraph: null, laneStatus: null,
    securityTestInput: '', securityTestResult: null,
    blockedLog: [],

    /* --- Security tab --- */
    securityConfig: null,

    /* --- Snapshots (Backup tab) --- */
    snapshots: [],

    /* --- Scheduler tab --- */
    scheduleJobs: [],
    scheduleLoaded: false,

    /* --- Backup tab --- */
    backupRestoring: false,
    backupRestoreResult: null,

    /* --- Diagnose --- */
    diagnoseResult: null,
    diagnosing: false,

    /* --- Audit Log --- */
    auditEntries: [],
    auditFilter: '',
    auditDate: '',

    /* ================================================================
       COMPUTED
       ================================================================ */
    get sidebarProjects() {
      return this.projects.map(p => ({
        id: p.project_id,
        name: p.name || p.project_id,
      }));
    },

    get archivedChats() {
      return this.chats.filter(c => c.archivedMessages && c.archivedMessages.length > 0);
    },

    get activeProjectName() {
      if (!this.activeProjectId) return 'Default';
      const p = this.projects.find(p => p.project_id === this.activeProjectId);
      return p ? (p.name || p.project_id) : this.activeProjectId;
    },

    chatsForProject(projectId) {
      return this.chats.filter(c => (c.projectId || null) === projectId);
    },

    get modelProviderTabs() {
      const providers = [...new Set(this.availableModels.map(m => m.provider))];
      return providers.length > 0 ? providers : ['openrouter', 'anthropic', 'openai', 'ollama'];
    },

    get modelsForTab() {
      return this.availableModels.filter(m => m.provider === this.modelProviderTab);
    },

    get shortModelName() {
      const m = this.settingsData?.default_model || '';
      const parts = m.split('/');
      return parts.length >= 2 ? parts[parts.length - 1].split(':')[0].slice(0, 20) : m;
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

      // Ensure textarea renders with correct initial height
      this.autoResize();
      // Scroll to bottom immediately and again after images load
      this.scrollToBottom();
      setTimeout(() => this.scrollToBottom(), 200);

      // Escape key closes image lightbox
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.imageModalOpen) {
          this.imageModalOpen = false;
          this.imageModalSrc = null;
          this.imageZoom = 1;
        }
      });
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
      // Keepalive ping — reply with pong
      if (msg.type === 'ping') {
        if (this.ws && this.ws.readyState === WebSocket.OPEN)
          this.ws.send(JSON.stringify({ type: 'pong' }));
        return;
      }
      // Server-pushed WhatsApp conversation — handle independently of activeChat
      if (msg.type === 'wa_message') {
        this.handleWhatsAppPush(msg);
        return;
      }
      // Server-pushed Telegram conversation
      if (msg.type === 'tg_message') {
        this.handleTelegramPush(msg);
        return;
      }

      // Proactive agent message (scheduled task result)
      if (msg.type === 'proactive') {
        const chat = this.activeChat;
        if (chat) {
          const content = msg.content || '';
          chat.messages.push({
            role: 'agent',
            raw: content,
            streaming: false,
            parts: parseAgentOutput(content),
            proactive: true,
          });
          this.scrollToBottom();
        }
        return;
      }

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
        this.$nextTick(() => document.getElementById('msgInput')?.focus());
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

    handleWhatsAppPush(msg) {
      const sessionId = msg.session_id;
      const phone = msg.sender ? msg.sender.split('@')[0] : 'unknown';

      // Build the new messages first (before any Alpine proxy wrapping)
      const newMessages = [];
      if (msg.user_message) {
        newMessages.push({ id: uid(), role: 'user', text: msg.user_message, ts: new Date().toISOString() });
      }
      if (msg.response) {
        newMessages.push({
          id: uid(), role: 'agent', raw: msg.response,
          parts: parseAgentOutput(msg.response),
          streaming: false, stepOpen: false, ts: new Date().toISOString(),
        });
      }

      const existing = this.chats.findIndex(c => c.sessionId === sessionId);
      if (existing === -1) {
        // Create chat with messages already populated so Alpine's proxy sees them from the start
        this.chats.unshift({
          id: uid(),
          title: `WhatsApp: ${phone}`,
          projectId: null,
          sessionId,
          messages: newMessages,
          ts: new Date().toISOString(),
          channel: 'whatsapp',
        });
      } else {
        // Push into the already-proxied chat object in this.chats
        for (const m of newMessages) {
          this.chats[existing].messages.push(m);
        }
      }

      if (!this.isStreaming) {
        const targetChat = existing === -1 ? this.chats[0] : this.chats[existing];
        this.activeChatId = targetChat.id;
        this.activeView = null;
        this.$nextTick(() => this.scrollToBottom());
      }
    },

    handleTelegramPush(msg) {
      const sessionId = msg.session_id;
      const sender = msg.sender || msg.chat_id || 'unknown';

      const newMessages = [];
      if (msg.user_message) {
        newMessages.push({ id: uid(), role: 'user', text: msg.user_message, ts: new Date().toISOString() });
      }
      if (msg.response) {
        newMessages.push({
          id: uid(), role: 'agent', raw: msg.response,
          parts: parseAgentOutput(msg.response),
          streaming: false, stepOpen: false, ts: new Date().toISOString(),
        });
      }

      const existing = this.chats.findIndex(c => c.sessionId === sessionId);
      if (existing === -1) {
        this.chats.unshift({
          id: uid(),
          title: `Telegram: ${sender}`,
          projectId: null,
          sessionId,
          messages: newMessages,
          ts: new Date().toISOString(),
          channel: 'telegram',
        });
      } else {
        for (const m of newMessages) {
          this.chats[existing].messages.push(m);
        }
      }

      const senderIdx = existing === -1 ? 0 : existing;

      // If project mode active: also mirror into the project chat in the sidebar
      if (msg.project_id) {
        const projSessionId = `tg-proj-${msg.project_id}`;
        const projIdx = this.chats.findIndex(c => c.sessionId === projSessionId);
        if (projIdx === -1) {
          this.chats.splice(senderIdx + 1, 0, {
            id: uid(),
            title: `📱 ${msg.project_id}`,
            projectId: msg.project_id,
            sessionId: projSessionId,
            messages: [...newMessages],
            ts: new Date().toISOString(),
            channel: 'telegram',
            telegramSource: true,
          });
        } else {
          for (const m of newMessages) {
            this.chats[projIdx].messages.push(m);
          }
        }
      }

      if (!this.isStreaming) {
        const targetChat = existing === -1 ? this.chats[0] : this.chats[existing];
        this.activeChatId = targetChat.id;
        this.activeView = null;
        this.$nextTick(() => this.scrollToBottom());
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

      // Capture pending image
      const imageFile = this.pendingImage;
      const imagePreview = this.pendingImagePreview;
      this.pendingImage = null;
      this.pendingImagePreview = null;

      // Encode image to base64 if present
      let images = null;
      if (imageFile) {
        try {
          const b64 = await new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result.split(',')[1]);
            reader.onerror = reject;
            reader.readAsDataURL(imageFile);
          });
          images = [{ mime: imageFile.type || 'image/jpeg', data: b64, preview: imagePreview }];
        } catch (_) {}
      }

      chat.messages.push({ id: uid(), role: 'user', text, ts: tsNow(), images: images ? [{ preview: imagePreview }] : null });
      this.inputText = '';
      this.autoResize();
      this.scrollToBottom();

      const agentMsg = {
        id: uid(), role: 'agent', raw: '', parts: [],
        streaming: true, ts: tsNow(), stepOpen: false,
      };
      chat.messages.push(agentMsg);
      this.isStreaming = true;
      this.currentActivity = 'Thinking';
      this.scrollToBottom();

      // Strip preview from images before sending (only send mime+data)
      const sendImages = images ? images.map(({ mime, data }) => ({ mime, data })) : null;

      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({
          message: text,
          project_id: this.activeProjectId || null,
          session_id: chat.sessionId,
          images: sendImages,
          budget_mode: this.budgetModeEnabled,
        }));
      } else {
        await this.sendViaSSE(text, chat, agentMsg, sendImages);
      }
    },

    stop() {
      if (!this.isStreaming) return;
      // Abort SSE connection
      if (this.sseAbort) { this.sseAbort.abort(); this.sseAbort = null; }
      // Send stop signal over WebSocket
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        const sessionId = this.activeChat?.sessionId;
        this.ws.send(JSON.stringify({ type: 'stop', session_id: sessionId }));
      }
      this.isStreaming = false;
      this.currentActivity = '';
      const last = this.activeChat?.messages?.at(-1);
      if (last?.streaming) {
        last.streaming = false;
        last.raw += '\n[SYS] Stopped by user.';
        last.parts = parseAgentOutput(last.raw);
      }
    },

    async sendViaSSE(text, chat, agentMsg, images = null) {
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
            images: images,
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
                this.$nextTick(() => document.getElementById('msgInput')?.focus());
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
        this.$nextTick(() => document.getElementById('msgInput')?.focus());
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
    clearChat() {
      const chat = this.activeChat;
      if (!chat || chat.messages.length === 0) return;
      // Archive current messages, start fresh (Redis history untouched)
      chat.archivedMessages = [...(chat.archivedMessages || []), ...chat.messages];
      chat.messages = [];
      chat.clearedAt = new Date().toISOString();
    },

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
      this.sidebarOpen = false;
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
      await this.loadModels();
    },

    async loadSettingsData() {
      try {
        const resp = await fetch('/api/settings');
        if (resp.ok) {
          this.settingsData = await resp.json();
          // Pre-populate Ollama URL from settings
          if (this.settingsData.ollama?.base_url && !this.ollamaUrl) {
            this.ollamaUrl = this.settingsData.ollama.base_url;
          }
          // Sync budget mode global default
          if (this.settingsData.budget_mode !== undefined) {
            this.budgetModeEnabled = this.settingsData.budget_mode;
          }
        }
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
          // Default to first available provider tab
          if (this.availableModels.length > 0) {
            this.modelProviderTab = this.availableModels[0].provider;
          }
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

    toggleModelConfig(key) {
      this.modelConfigPanel = this.modelConfigPanel === key ? null : key;
    },

    setModelConfigField(modelKey, field, value) {
      if (!this.modelConfigForm[modelKey]) {
        this.modelConfigForm[modelKey] = {};
      }
      this.modelConfigForm[modelKey][field] = value;
    },

    async saveModelConfig(m) {
      const overrides = this.modelConfigForm[m.key] || {};
      const body = {
        provider: m.provider,
        model: m.model,
        context_window: m.context_window,
        cost_per_1k_input: overrides.cost_per_1k_input ?? m.cost_per_1k_input ?? 0,
        cost_per_1k_output: overrides.cost_per_1k_output ?? m.cost_per_1k_output ?? 0,
        has_vision: overrides.has_vision ?? m.has_vision ?? false,
        max_completion_tokens: overrides.max_completion_tokens ?? m.max_completion_tokens ?? 4096,
        history_fraction: overrides.history_fraction ?? m.history_fraction ?? 0.7,
        temperature: overrides.temperature ?? m.temperature ?? 0.7,
        top_p: overrides.top_p ?? m.top_p ?? 1.0,
        stream: overrides.stream ?? m.stream ?? true,
        use_cases: m.use_cases || ['chat'],
        set_as_default_for_chat: overrides.set_as_default ?? false,
      };
      try {
        const resp = await fetch('/api/llm/providers/model-config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        if (resp.ok) {
          this.modelConfigPanel = null;
          await this.loadModels();
          await this.loadSettingsData();
        }
      } catch (_) {}
    },

    async refreshProviderModels() {
      this.modelRefreshing = true;
      this.remoteModelDiff = [];
      try {
        const resp = await fetch(`/api/llm/providers/remote/${this.modelProviderTab}`);
        if (!resp.ok) {
          const err = await resp.json().catch(() => ({}));
          alert('Refresh failed: ' + (err.detail || resp.statusText));
          return;
        }
        const data = await resp.json();
        const remote = data.models || [];
        const existingKeys = new Set(this.availableModels.map(m => m.model));
        this.remoteModelDiff = remote.filter(rm => !existingKeys.has(rm.id));
      } catch (e) {
        alert('Refresh error: ' + e.message);
      } finally {
        this.modelRefreshing = false;
      }
    },

    async mergeRemoteModels() {
      for (const rm of this.remoteModelDiff) {
        const body = {
          provider: this.modelProviderTab,
          model: rm.id,
          context_window: rm.context_length || 128000,
          cost_per_1k_input: rm.cost_per_1k_input || 0,
          cost_per_1k_output: rm.cost_per_1k_output || 0,
          has_vision: rm.has_vision || false,
          max_completion_tokens: rm.max_completion_tokens || 4096,
          history_fraction: 0.7,
          temperature: 0.7,
          use_cases: ['chat'],
          set_as_default_for_chat: false,
        };
        try {
          await fetch('/api/llm/providers/model-config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
        } catch (_) {}
      }
      this.remoteModelDiff = [];
      await this.loadModels();
    },

    async loadCostBreakdown() {
      try {
        const resp = await fetch('/api/llm/costs/breakdown');
        if (resp.ok) {
          const data = await resp.json();
          this.costBreakdown = data.breakdown || [];
        }
      } catch (_) {}
    },

    /* --- Ollama --- */
    async testOllama() {
      this.ollamaTesting = true;
      this.ollamaTestResult = null;
      try {
        // Save URL first so the test uses it
        if (this.ollamaUrl) {
          await fetch('/api/settings/ollama', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url: this.ollamaUrl, api_key: this.ollamaApiKey }),
          });
        }
        const resp = await fetch('/api/settings/ollama/test');
        if (resp.ok) this.ollamaTestResult = await resp.json();
      } catch (e) {
        this.ollamaTestResult = { reachable: false, error: e.message };
      } finally {
        this.ollamaTesting = false;
      }
    },

    async saveOllama() {
      try {
        const resp = await fetch('/api/settings/ollama', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ url: this.ollamaUrl, api_key: this.ollamaApiKey }),
        });
        if (resp.ok) await this.loadSettingsData();
      } catch (_) {}
    },

    async loadConnectors() {
      await this.loadSettingsData();
      this.connectorForm.whatsapp = this.settingsData.connectors?.whatsapp_sidecar_url || '';
      // Auto-check WhatsApp status when tab opens
      await this.waCheckStatus();
    },

    /* ================================================================
       WHATSAPP QR
       ================================================================ */
    get waStatusLabel() {
      if (!this.waStatus) return 'Unknown';
      if (!this.waStatus.sidecar_reachable) return 'Offline';
      if (this.waStatus.ready) return 'Connected';
      return 'Not authenticated';
    },

    get waStatusCls() {
      if (!this.waStatus) return 'wa-badge-unknown';
      if (!this.waStatus.sidecar_reachable) return 'wa-badge-offline';
      if (this.waStatus.ready) return 'wa-badge-connected';
      return 'wa-badge-pending';
    },

    async waCheckStatus() {
      this.waQrLoading = true;
      try {
        const resp = await fetch('/api/whatsapp/status');
        if (resp.ok) {
          this.waStatus = await resp.json();
          // If just connected, stop polling and clear QR
          if (this.waStatus.ready) {
            this.waStopPolling();
            this.waQrData = null;
          }
        }
      } catch (_) {
        this.waStatus = { sidecar_reachable: false, ready: false };
      } finally {
        this.waQrLoading = false;
      }
    },

    async waFetchQr() {
      this.waQrLoading = true;
      this.waQrError = null;
      try {
        const resp = await fetch('/api/whatsapp/qr');
        const data = await resp.json();
        if (!resp.ok) {
          this.waQrError = data.detail || 'Failed to get QR code';
          this.waQrData = null;
        } else if (data.status === 'authenticated') {
          // Already authenticated — refresh status
          this.waQrData = null;
          await this.waCheckStatus();
        } else if (data.status === 'not_ready' || !data.qr) {
          // Sidecar is still initialising — start polling silently until QR appears
          this.waQrData = null;
          this.waQrError = null;
          this.waStartQrInitPolling();
        } else {
          this.waQrData = data.qr;
          // Start polling to auto-refresh QR and detect when authenticated
          this.waStartPolling();
        }
      } catch (e) {
        this.waQrError = 'Error: ' + e.message;
        this.waQrData = null;
      } finally {
        this.waQrLoading = false;
      }
    },

    waStartQrInitPolling() {
      // Poll every 3 s while sidecar is still generating the initial QR code
      this.waStopPolling();
      this.waQrPollTimer = setInterval(async () => {
        try {
          const resp = await fetch('/api/whatsapp/qr');
          const data = await resp.json();
          if (!resp.ok) { this.waStopPolling(); return; }
          if (data.status === 'authenticated') {
            this.waStopPolling();
            this.waQrData = null;
            await this.waCheckStatus();
          } else if (data.qr) {
            // QR is now ready — display it and switch to normal refresh cadence
            this.waStopPolling();
            this.waQrData = data.qr;
            this.waQrError = null;
            this.waStartPolling();
          }
          // else still not_ready — keep polling
        } catch (_) {}
      }, 3000);
    },

    waStartPolling() {
      this.waStopPolling();
      // Poll every 20s: refresh QR (which rotates ~30s) and check auth status
      this.waQrPollTimer = setInterval(async () => {
        // First check if now authenticated
        await this.waCheckStatus();
        if (this.waStatus?.ready) return; // stop handled in waCheckStatus

        // Refresh QR silently
        try {
          const resp = await fetch('/api/whatsapp/qr');
          const data = await resp.json();
          if (resp.ok && data.qr) {
            this.waQrData = data.qr;
          } else if (data.status === 'authenticated') {
            this.waStopPolling();
            this.waQrData = null;
            await this.waCheckStatus();
          }
        } catch (_) {}
      }, 20000);
    },

    waStopPolling() {
      if (this.waQrPollTimer) {
        clearInterval(this.waQrPollTimer);
        this.waQrPollTimer = null;
      }
    },

    async waLogout() {
      if (!confirm('Disconnect WhatsApp? You will need to scan a QR code again to reconnect.')) return;
      try {
        const resp = await fetch('/api/whatsapp/logout', { method: 'POST' });
        if (resp.ok) {
          this.waStatus = { ...this.waStatus, ready: false };
          this.waQrData = null;
          this.waQrError = null;
        }
      } catch (_) {}
    },

    async waStartBridge() {
      this.waStarting = true;
      this.waStartError = null;
      try {
        const resp = await fetch('/api/whatsapp/start', { method: 'POST' });
        const data = await resp.json();
        if (!resp.ok) {
          this.waStartError = data.detail || 'Failed to start bridge';
          return;
        }
        // Poll until sidecar is reachable (up to 60s)
        for (let i = 0; i < 30; i++) {
          await new Promise(r => setTimeout(r, 2000));
          await this.waCheckStatus();
          if (this.waStatus?.sidecar_reachable) {
            // Auto-fetch QR once reachable
            await this.waFetchQr();
            return;
          }
        }
        this.waStartError = 'Bridge started but not reachable yet — click "Check connection" in a moment.';
      } catch (e) {
        this.waStartError = 'Error: ' + e.message;
      } finally {
        this.waStarting = false;
      }
    },

    async waStopBridge() {
      if (!confirm('Stop the WhatsApp bridge container?')) return;
      try {
        await fetch('/api/whatsapp/stop', { method: 'POST' });
        this.waStopPolling();
        this.waQrData = null;
        this.waStatus = null;
        await this.waCheckStatus();
      } catch (_) {}
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
            nvidia: 'NVIDIA_API_KEY',
            moonshot: 'MOONSHOT_API_KEY',
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
       THINKING CONFIG
       ================================================================ */
    async saveThinkingConfig(m, enabled) {
      try {
        const overrides = this.modelConfigForm[m.key] || {};
        await fetch('/api/settings/model-config', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            provider: m.provider,
            model: m.model,
            thinking_enabled: enabled,
            thinking_budget_tokens: overrides.thinking_budget_tokens ?? m.thinking_budget_tokens ?? 8000,
          }),
        });
      } catch (_) {}
    },

    /* ================================================================
       AGENTS TAB
       ================================================================ */
    async loadAgents() {
      try {
        const resp = await fetch('/api/admin/agents');
        if (resp.ok) {
          this.agentsData = await resp.json();
          // Normalize legacy provider names and corrupted model keys
          const providerMap = { claude: 'anthropic', openai_compat: 'openai' };
          for (const agent of Object.values(this.agentsData.agents || {})) {
            // Remap legacy provider aliases (e.g. 'claude' → 'anthropic')
            if (agent.llm && providerMap[agent.llm]) {
              agent.llm = providerMap[agent.llm];
            }
            // Repair corrupted model field that contains a full 'provider/model' key
            if (agent.model && agent.model.includes('/')) {
              const slash = agent.model.indexOf('/');
              agent.llm = agent.model.substring(0, slash);
              agent.model = agent.model.substring(slash + 1);
            }
          }
          // Deep-copy so edits don't mutate original until saved
          this.agentsEdits = JSON.parse(JSON.stringify(this.agentsData.agents || {}));
        }
      } catch (_) {}
    },

    setAgentField(name, field, value) {
      if (!this.agentsEdits[name]) this.agentsEdits[name] = {};
      this.agentsEdits[name][field] = value;
      if (this.agentsData?.agents?.[name]) {
        this.agentsData.agents[name][field] = value;
      }
    },

    setAgentModel(name, key) {
      const slash = key.indexOf('/');
      if (slash === -1) return;
      const llm = key.substring(0, slash);
      const model = key.substring(slash + 1);
      this.setAgentField(name, 'llm', llm);
      this.setAgentField(name, 'model', model);
    },

    toggleAgentTool(name, tool, checked) {
      if (!this.agentsData?.agents?.[name]) return;
      const tools = [...(this.agentsData.agents[name].tools || [])];
      if (checked && !tools.includes(tool)) tools.push(tool);
      if (!checked) {
        const idx = tools.indexOf(tool);
        if (idx >= 0) tools.splice(idx, 1);
      }
      this.setAgentField(name, 'tools', tools);
    },

    async saveAgent(name) {
      const agent = this.agentsData?.agents?.[name];
      if (!agent) return;
      try {
        const resp = await fetch(`/api/admin/agents/${encodeURIComponent(name)}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(agent),
        });
        if (resp.ok) {
          // Flash visual feedback
          const btn = event?.target;
          if (btn) { btn.textContent = '✓ Saved'; setTimeout(() => { btn.textContent = 'Save'; }, 1500); }
        }
      } catch (_) {}
    },

    async deleteAgent(name) {
      if (!confirm(`Delete agent "${name}"?`)) return;
      try {
        const resp = await fetch(`/api/admin/agents/${encodeURIComponent(name)}`, { method: 'DELETE' });
        if (resp.ok) {
          delete this.agentsData.agents[name];
          this.agentsData = { ...this.agentsData };
        }
      } catch (_) {}
    },

    addNewAgent() {
      const name = prompt('Agent type name (e.g. "summarizer"):');
      if (!name?.trim()) return;
      const slug = name.trim().toLowerCase().replace(/\s+/g, '_');
      if (!this.agentsData) this.agentsData = { agents: {}, routing: {} };
      this.agentsData.agents[slug] = {
        name: name.trim(),
        description: '',
        llm: 'ollama',
        model: 'qwen3:4b',
        system_prompt: 'You are a helpful AI assistant.',
        tools: ['filesystem', 'web_search'],
        max_depth: 1,
      };
      this.agentsData = { ...this.agentsData };
    },

    setRoutingChannel(channel, agentName) {
      if (!this.agentsData) return;
      if (!this.agentsData.routing) this.agentsData.routing = {};
      this.agentsData.routing[channel] = agentName;
    },

    async saveRouting() {
      try {
        const resp = await fetch('/api/admin/routing', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.agentsData?.routing || {}),
        });
        if (resp.ok) alert('Routing saved.');
      } catch (_) {}
    },

    /* ================================================================
       ADMIN TAB
       ================================================================ */
    async loadAdmin() {
      this.adminLoaded = false;
      await this.loadSettingsData();
      this.budgetForm.max_cost_usd_per_day = this.settingsData.budget?.max_cost_usd_per_day ?? null;
      this.budgetForm.max_tokens_per_day = this.settingsData.budget?.max_tokens_per_day ?? null;
      this.adminLoaded = true;
    },

    async saveBudget() {
      try {
        const resp = await fetch('/api/settings/budget', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.budgetForm),
        });
        if (resp.ok) { await this.loadSettingsData(); alert('Budget saved.'); }
      } catch (_) {}
    },

    async saveBudgetMode() {
      try {
        await fetch('/api/settings/budget-mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ budget_mode: this.budgetModeEnabled }),
        });
      } catch (_) {}
    },

    async triggerCompaction() {
      this.compacting = true;
      this.compactionResult = null;
      try {
        const resp = await fetch('/api/admin/compact', { method: 'POST' });
        if (resp.ok) {
          const data = await resp.json();
          this.compactionResult = `Compacted ${data.compacted} chunk(s).`;
        }
      } catch (_) {}
      this.compacting = false;
    },

    async loadAgentGraph() {
      try {
        const resp = await fetch('/api/admin/agent-graph');
        if (resp.ok) this.agentGraph = await resp.json();
      } catch (_) {}
    },

    async loadLaneStatus() {
      try {
        const resp = await fetch('/api/admin/lanes');
        if (resp.ok) this.laneStatus = await resp.json();
      } catch (_) {}
    },

    async testSecurity() {
      this.securityTestResult = null;
      try {
        const resp = await fetch('/api/admin/security/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: this.securityTestInput }),
        });
        if (resp.ok) this.securityTestResult = await resp.json();
      } catch (_) {}
    },

    async loadBlockedLog() {
      try {
        const resp = await fetch('/api/admin/security/blocked?limit=30');
        if (resp.ok) {
          const data = await resp.json();
          this.blockedLog = data.blocked || [];
        }
      } catch (_) {}
    },

    /* ================================================================
       SECURITY TAB
       ================================================================ */
    async loadSecurityConfig() {
      try {
        const resp = await fetch('/api/admin/security/config');
        if (resp.ok) this.securityConfig = await resp.json();
      } catch (_) {}
    },

    async saveSecurityConfig() {
      try {
        const resp = await fetch('/api/admin/security/config', {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(this.securityConfig),
        });
        if (resp.ok) {
          const btn = event?.target;
          if (btn) { btn.textContent = '✓ Saved'; setTimeout(() => { btn.textContent = 'Save security config'; }, 1500); }
        }
      } catch (_) {}
    },

    /* ================================================================
       SCHEDULER TAB
       ================================================================ */
    async loadSchedule() {
      try {
        const resp = await fetch('/api/admin/schedule');
        if (resp.ok) {
          const data = await resp.json();
          this.scheduleJobs = data.jobs || [];
          this.scheduleLoaded = true;
        }
      } catch (_) {}
    },

    async runJob(jobId) {
      try {
        const resp = await fetch(`/api/admin/schedule/${encodeURIComponent(jobId)}/run`, { method: 'POST' });
        if (resp.ok) {
          await this.loadSchedule();
          alert('Job triggered.');
        }
      } catch (_) {}
    },

    /* ================================================================
       BACKUP TAB
       ================================================================ */
    downloadBackup() {
      window.open('/api/admin/backup', '_blank');
    },

    async restoreBackup(event) {
      const file = event.target.files[0];
      if (!file) return;
      if (!confirm(`Restore from "${file.name}"? This will overwrite memory and config files.`)) {
        event.target.value = '';
        return;
      }
      this.backupRestoring = true;
      this.backupRestoreResult = null;
      try {
        const fd = new FormData();
        fd.append('file', file);
        const resp = await fetch('/api/admin/restore', { method: 'POST', body: fd });
        const data = await resp.json();
        this.backupRestoreResult = resp.ok
          ? `Restored from ${data.filename}`
          : `Error: ${data.detail || 'Restore failed'}`;
      } catch (e) {
        this.backupRestoreResult = `Error: ${e.message}`;
      } finally {
        this.backupRestoring = false;
        event.target.value = '';
      }
    },

    /* ================================================================
       SNAPSHOTS (Config Backup)
       ================================================================ */
    async loadSnapshots() {
      try {
        const resp = await fetch('/api/admin/snapshots');
        if (resp.ok) {
          const data = await resp.json();
          this.snapshots = data.snapshots || [];
        }
      } catch (_) {}
    },

    async restoreSnapshot(name) {
      if (!confirm(`Restore config snapshot "${name}"? The app will restart automatically.`)) return;
      try {
        const resp = await fetch(`/api/admin/snapshots/${encodeURIComponent(name)}/restore`, { method: 'POST' });
        if (resp.ok) {
          alert('Restore initiated. The app is restarting — reload the page in a few seconds.');
        } else {
          const data = await resp.json();
          alert(`Error: ${data.detail || 'Restore failed'}`);
        }
      } catch (e) {
        alert(`Error: ${e.message}`);
      }
    },

    async deleteSnapshot(name) {
      if (!confirm(`Delete snapshot "${name}"?`)) return;
      try {
        const resp = await fetch(`/api/admin/snapshots/${encodeURIComponent(name)}`, { method: 'DELETE' });
        if (resp.ok) await this.loadSnapshots();
      } catch (_) {}
    },

    /* ================================================================
       DIAGNOSE TAB
       ================================================================ */
    async runDiagnostics() {
      this.diagnosing = true;
      this.diagnoseResult = null;
      try {
        const resp = await fetch('/api/admin/diagnose', { method: 'POST' });
        if (resp.ok) this.diagnoseResult = await resp.json();
      } catch (e) {
        this.diagnoseResult = { error: e.message };
      } finally {
        this.diagnosing = false;
      }
    },

    /* ================================================================
       AUDIT LOG
       ================================================================ */
    async loadAuditLog() {
      try {
        const params = new URLSearchParams({ limit: 200 });
        if (this.auditFilter) params.set('event_type', this.auditFilter);
        if (this.auditDate)   params.set('date', this.auditDate);
        const resp = await fetch(`/api/admin/audit?${params}`);
        if (resp.ok) {
          const data = await resp.json();
          this.auditEntries = data.entries || [];
        }
      } catch (e) {
        this.auditEntries = [];
      }
    },
    auditTs(ts) {
      if (!ts) return '—';
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    },
    auditBadgeClass(type) {
      return {
        chat: 'badge-gen',
        tool: 'badge-exe',
        security_block: 'badge-err',
        memory: 'badge-mem',
      }[type] || 'badge-sys';
    },

    /* ================================================================
       SKILLS TAB
       ================================================================ */
    async loadSkills() {
      this.skillsLoading = true;
      try {
        const resp = await fetch('/api/admin/skills');
        if (resp.ok) {
          const data = await resp.json();
          this.skillsList = data.skills || [];
        }
      } catch (_) {
        this.skillsList = [];
      } finally {
        this.skillsLoading = false;
      }
    },

    async reloadSkills() {
      this.skillsLoading = true;
      try {
        await fetch('/api/admin/skills/reload', { method: 'POST' });
        await this.loadSkills();
      } finally {
        this.skillsLoading = false;
      }
    },

    async runSkillTest(skillName) {
      this.skillTestRunning = true;
      this.skillTestResult = null;
      try {
        let args = {};
        try { args = JSON.parse(this.skillTestArgs || '{}'); } catch (_) {}
        const resp = await fetch('/api/admin/skills/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skill_name: skillName, args }),
        });
        this.skillTestResult = await resp.json();
      } catch (e) {
        this.skillTestResult = { success: false, error: e.message };
      } finally {
        this.skillTestRunning = false;
      }
    },

    async importSkill() {
      this.skillImportStatus = null;
      try {
        const resp = await fetch('/api/admin/skills/import', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ yaml_text: this.skillImportYaml }),
        });
        const data = await resp.json();
        if (resp.ok) {
          this.skillImportStatus = { success: true, message: `✓ Imported "${data.name}"` };
          this.skillImportYaml = '';
          await this.loadSkills();
        } else {
          this.skillImportStatus = { success: false, message: data.detail || 'Import failed' };
        }
      } catch (e) {
        this.skillImportStatus = { success: false, message: e.message };
      }
      setTimeout(() => { this.skillImportStatus = null; }, 4000);
    },

    async deleteSkill(name) {
      if (!confirm(`Delete skill "${name}"? This will remove the YAML file.`)) return;
      try {
        const resp = await fetch(`/api/admin/skills/${encodeURIComponent(name)}`, { method: 'DELETE' });
        if (resp.ok) {
          await this.loadSkills();
        } else {
          const data = await resp.json();
          alert(data.detail || 'Delete failed');
        }
      } catch (e) {
        alert(e.message);
      }
    },

    /* ================================================================
       MCP TAB
       ================================================================ */
    async loadMcpTools() {
      try {
        const resp = await fetch('/api/admin/mcp/tools');
        if (resp.ok) {
          const data = await resp.json();
          this.mcpTools = data.tools || [];
        }
      } catch (_) {
        this.mcpTools = [];
      }
    },

    async runMcpTest(toolName) {
      this.mcpTestRunning = true;
      this.mcpTestResult = null;
      try {
        let args = {};
        try { args = JSON.parse(this.mcpTestArgs || '{}'); } catch (_) {}
        const resp = await fetch('/api/admin/mcp/test', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tool_name: toolName, arguments: args }),
        });
        this.mcpTestResult = await resp.json();
      } catch (e) {
        this.mcpTestResult = { success: false, error: e.message };
      } finally {
        this.mcpTestRunning = false;
      }
    },

    /* ================================================================
       FILE / IMAGE HANDLING
       ================================================================ */
    onFileSelected(event) {
      const file = event.target.files[0];
      if (!file) return;
      const model = this.availableModels.find(m => m.key === this.settingsData?.default_model);
      if (!model?.has_vision) {
        alert('The current default model does not support image input.\nPlease go to Settings → Models and set a vision-capable model as default (e.g. gpt-4o, claude-3-5-sonnet).');
        event.target.value = '';
        return;
      }
      this.pendingImage = file;
      this.pendingImagePreview = URL.createObjectURL(file);
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
