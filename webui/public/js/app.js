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

/* ---- LocalStorage persistence ---- */
const LS_KEY = 'remnant_chats_v2';

function saveChats(chats) {
  try {
    // Only persist last 50 chats, trim messages to last 200 each
    const toSave = chats.slice(0, 50).map(c => ({
      ...c,
      messages: (c.messages || []).slice(-200).map(m => ({
        ...m,
        streaming: false,  // never save in-progress state
      })),
    }));
    localStorage.setItem(LS_KEY, JSON.stringify(toSave));
  } catch (_) {}
}

function loadChats() {
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (!raw) return [];
    return JSON.parse(raw) || [];
  } catch (_) { return []; }
}

/* ---- Helpers ---- */
function tsNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function uid() {
  return Math.random().toString(36).slice(2);
}

function renderMd(text) {
  return marked.parse(text || '');
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

    /* --- Projects --- */
    projects: [],
    activeProjectId: null,

    /* --- Chats --- */
    chats: [],
    activeChatId: null,
    get activeChat() { return this.chats.find(c => c.id === this.activeChatId) || null; },
    get activeMessages() { return this.activeChat?.messages || []; },

    /* --- Input --- */
    inputText: '',
    isStreaming: false,
    currentActivity: '',   // what the agent is currently doing
    sseAbort: null,        // AbortController for SSE cancellation

    /* --- Memory panel --- */
    memoryOpen: false,
    memorySearch: '',
    memoryChunks: [],
    memoryStats: {},

    /* --- Settings modal --- */
    settingsOpen: false,
    settingsTab: 'general',
    usageSummary: {},

    /* ================================================================
       INIT
       ================================================================ */
    async init() {
      // Restore chats from localStorage
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
        // If we were streaming when WS dropped, clean up
        if (this.isStreaming) {
          this.isStreaming = false;
          this.currentActivity = '';
          const chat = this.activeChat;
          if (chat) {
            const last = chat.messages[chat.messages.length - 1];
            if (last?.streaming) {
              last.streaming = false;
              last.raw += '\n[SYS] Connection restored — please resend.';
              last.parts = parseAgentOutput(last.raw);
            }
          }
        }
      };

      this.ws.onclose = (ev) => {
        this.wsStatus = 'disconnected';
        // If mid-stream, mark it failed
        if (this.isStreaming) {
          this.isStreaming = false;
          this.currentActivity = '';
          const chat = this.activeChat;
          if (chat) {
            const last = chat.messages[chat.messages.length - 1];
            if (last?.streaming) {
              last.streaming = false;
              last.raw += '\n[ERR] Connection lost.';
              last.parts = parseAgentOutput(last.raw);
            }
          }
        }
        this.scheduleReconnect();
      };

      this.ws.onerror = () => {
        this.wsStatus = 'degraded';
      };

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
        const content = msg.content || '';
        const last = chat.messages[chat.messages.length - 1];
        if (last?.role === 'agent') {
          last.raw += content;
          last.parts = parseAgentOutput(last.raw);
          // Update activity indicator from latest badge
          const lastBadge = [...last.parts].reverse().find(p => p.type === 'badge');
          if (lastBadge) this.currentActivity = BADGE_LABELS[lastBadge.cls] || lastBadge.badge;
        }
        this.scrollToBottom();
        return;
      }

      if (msg.type === 'done') {
        this.isStreaming = false;
        this.currentActivity = '';
        const last = chat.messages[chat.messages.length - 1];
        if (last?.role === 'agent') {
          last.streaming = false;
          last.parts = parseAgentOutput(last.raw);
        }
        if (chat.title === 'New chat') {
          const firstUser = chat.messages.find(m => m.role === 'user');
          if (firstUser) chat.title = firstUser.text.slice(0, 40) + (firstUser.text.length > 40 ? '…' : '');
        }
        this.scrollToBottom();
        return;
      }

      if (msg.error) {
        this.isStreaming = false;
        this.currentActivity = '';
        const last = chat.messages[chat.messages.length - 1];
        if (last?.role === 'agent') {
          last.raw += `\n[ERR] ${msg.error}`;
          last.parts = parseAgentOutput(last.raw);
          last.streaming = false;
        }
      }
    },

    /* ================================================================
       SEND MESSAGE
       ================================================================ */
    async send() {
      const text = this.inputText.trim();
      if (!text || this.isStreaming) return;

      if (!this.activeChat) this.newChat();
      const chat = this.activeChat;
      const sessionId = chat.sessionId;

      chat.messages.push({ id: uid(), role: 'user', text, ts: tsNow() });
      this.inputText = '';
      this.autoResize();
      this.scrollToBottom();

      const agentMsg = {
        id: uid(),
        role: 'agent',
        raw: '',
        parts: [],
        streaming: true,
        ts: tsNow(),
        stepOpen: true,
      };
      chat.messages.push(agentMsg);
      this.isStreaming = true;
      this.currentActivity = 'Thinking';
      this.scrollToBottom();

      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({
          message: text,
          project_id: this.activeProjectId || null,
          session_id: sessionId,
        }));
      } else {
        await this.sendViaSSE(text, chat, agentMsg, sessionId);
      }
    },

    /* ================================================================
       STOP / CANCEL
       ================================================================ */
    stop() {
      if (!this.isStreaming) return;

      // Abort SSE if active
      if (this.sseAbort) {
        this.sseAbort.abort();
        this.sseAbort = null;
      }

      // For WS, just close and reconnect — server will stop when client disconnects
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        // Don't close the WS permanently — send a cancel signal if supported,
        // otherwise just mark locally as stopped
      }

      this.isStreaming = false;
      this.currentActivity = '';
      const chat = this.activeChat;
      if (chat) {
        const last = chat.messages[chat.messages.length - 1];
        if (last?.streaming) {
          last.streaming = false;
          last.raw += '\n[SYS] Stopped by user.';
          last.parts = parseAgentOutput(last.raw);
        }
      }
    },

    async sendViaSSE(text, chat, agentMsg, sessionId) {
      const controller = new AbortController();
      this.sseAbort = controller;

      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: text,
            project_id: this.activeProjectId || null,
            session_id: sessionId,
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
                if (chat.title === 'New chat') {
                  const firstUser = chat.messages.find(m => m.role === 'user');
                  if (firstUser) chat.title = firstUser.text.slice(0, 40) + (firstUser.text.length > 40 ? '…' : '');
                }
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
    },

    selectChat(id) {
      if (this.isStreaming) return;  // Don't switch mid-stream
      this.activeChatId = id;
      this.$nextTick(() => this.scrollToBottom());
    },

    deleteChat(id) {
      this.chats = this.chats.filter(c => c.id !== id);
      if (this.activeChatId === id) {
        if (this.chats.length > 0) {
          this.activeChatId = this.chats[0].id;
        } else {
          this.newChat();
        }
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

    selectProject(id) {
      this.activeProjectId = id;
      this.newChat();
    },

    /* ================================================================
       MEMORY PANEL
       ================================================================ */
    async toggleMemory() {
      this.memoryOpen = !this.memoryOpen;
      if (this.memoryOpen) await this.loadRecentMemory();
    },

    async loadMemoryStats() {
      try {
        const resp = await fetch('/api/memory/stats');
        if (resp.ok) this.memoryStats = await resp.json();
      } catch (_) {}
    },

    async loadRecentMemory() {
      try {
        const resp = await fetch('/api/memory/recent?limit=30&project_id=' + (this.activeProjectId || ''));
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

    /* ================================================================
       SETTINGS
       ================================================================ */
    async openSettings() {
      this.settingsOpen = true;
      await this.loadUsage();
    },

    async loadUsage() {
      try {
        const resp = await fetch('/api/llm/usage');
        if (resp.ok) this.usageSummary = await resp.json();
      } catch (_) {}
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

    copyCode(el) {
      const code = el.closest('pre')?.querySelector('code')?.textContent || '';
      navigator.clipboard.writeText(code).catch(() => {});
    },

    hasBadges(msg) {
      return msg.parts && msg.parts.some(p => p.type === 'badge');
    },

    finalMdParts(msg) {
      return (msg.parts || []).filter(p => p.type === 'markdown');
    },

    badgeParts(msg) {
      return (msg.parts || []).filter(p => p.type === 'badge');
    },

    get statusLabel() {
      if (this.isStreaming) return this.currentActivity || 'Thinking…';
      return { connected: 'Connected', degraded: 'Reconnecting…', disconnected: 'Offline' }[this.wsStatus] || 'Offline';
    },

    get statusCls() {
      if (this.isStreaming) return 'streaming';
      return this.wsStatus;
    },

    get activeProjectName() {
      if (!this.activeProjectId) return 'Default';
      const p = this.projects.find(p => p.project_id === this.activeProjectId);
      return p ? (p.name || p.project_id) : this.activeProjectId;
    },

    formatScore(s) { return parseFloat(s || 0).toFixed(1); },
    chunkLabel(chunk) { return chunk.text_excerpt || chunk.text || ''; },
    usagePercent(used, max) {
      if (!max || !used) return 0;
      return Math.min(100, Math.round((used / max) * 100));
    },
  }));
});
