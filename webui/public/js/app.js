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

function parseBadgeLine(text) {
  for (const [tag, cls] of Object.entries(BADGE_MAP)) {
    if (text.startsWith(tag)) {
      return { badge: tag.slice(1, -1), cls, text: text.slice(tag.length).trim() };
    }
  }
  return null;
}

/**
 * Split raw agent output into structured parts:
 * - badge lines (GEN/USE/EXE/…)
 * - markdown text blocks
 */
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
    wsStatus: 'disconnected',  // connected | degraded | disconnected
    wsRetries: 0,
    wsRetryTimer: null,

    /* --- Projects --- */
    projects: [],
    activeProjectId: null,

    /* --- Chats --- */
    chats: [],           // [{ id, title, projectId, messages: [] }]
    activeChatId: null,
    get activeChat() { return this.chats.find(c => c.id === this.activeChatId) || null; },
    get activeMessages() { return this.activeChat?.messages || []; },

    /* --- Input --- */
    inputText: '',
    isStreaming: false,

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
      this.connectWS();
      await this.loadProjects();
      this.newChat();
      await this.loadMemoryStats();
    },

    /* ================================================================
       WEBSOCKET
       ================================================================ */
    wsUrl() {
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      return `${proto}://${location.host}/api/ws`;
    },

    connectWS() {
      if (this.ws) { try { this.ws.close(); } catch (_) {} }

      this.ws = new WebSocket(this.wsUrl());

      this.ws.onopen = () => {
        this.wsStatus = 'connected';
        this.wsRetries = 0;
        clearTimeout(this.wsRetryTimer);
      };

      this.ws.onclose = () => {
        this.wsStatus = 'disconnected';
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
      const delay = Math.min(1000 * 2 ** this.wsRetries, 16000);
      this.wsRetryTimer = setTimeout(() => {
        this.wsRetryTimer = null;
        this.wsRetries++;
        this.wsStatus = 'degraded';
        this.connectWS();
      }, delay);
    },

    handleWsMessage(msg) {
      const chat = this.activeChat;
      if (!chat) return;

      if (msg.type === 'start') {
        // Already added the agent message placeholder when sending
        return;
      }

      if (msg.type === 'chunk') {
        const content = msg.content || '';
        const last = chat.messages[chat.messages.length - 1];
        if (last && last.role === 'agent') {
          last.raw += content;
          last.parts = parseAgentOutput(last.raw);
        }
        this.scrollToBottom();
        return;
      }

      if (msg.type === 'done') {
        this.isStreaming = false;
        const last = chat.messages[chat.messages.length - 1];
        if (last && last.role === 'agent') {
          last.streaming = false;
          // Final render
          last.parts = parseAgentOutput(last.raw);
        }
        // Update chat title from first user message
        if (chat.title === 'New chat' && chat.messages.length >= 1) {
          const firstUser = chat.messages.find(m => m.role === 'user');
          if (firstUser) {
            chat.title = firstUser.text.slice(0, 40) + (firstUser.text.length > 40 ? '…' : '');
          }
        }
        this.scrollToBottom();
        return;
      }

      if (msg.error) {
        this.isStreaming = false;
        const last = chat.messages[chat.messages.length - 1];
        if (last && last.role === 'agent') {
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

      // Add user message
      chat.messages.push({ id: uid(), role: 'user', text, ts: tsNow() });
      this.inputText = '';
      this.autoResize();
      this.scrollToBottom();

      // Add agent placeholder
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
      this.scrollToBottom();

      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({
          message: text,
          project_id: this.activeProjectId || null,
          session_id: sessionId,
        }));
      } else {
        // Fallback: SSE endpoint
        await this.sendViaSSE(text, chat, agentMsg, sessionId);
      }
    },

    async sendViaSSE(text, chat, agentMsg, sessionId) {
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
                this.scrollToBottom();
              } else if (msg.type === 'done') {
                agentMsg.streaming = false;
                this.isStreaming = false;
                this.scrollToBottom();
              }
            } catch (_) {}
          }
        }
      } catch (e) {
        agentMsg.raw += `\n[ERR] Failed to connect: ${e.message}`;
        agentMsg.parts = parseAgentOutput(agentMsg.raw);
        agentMsg.streaming = false;
        this.isStreaming = false;
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
        ts: new Date(),
      };
      this.chats.unshift(chat);
      this.activeChatId = chat.id;
    },

    selectChat(id) {
      this.activeChatId = id;
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
      if (this.memoryOpen) {
        await this.loadRecentMemory();
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
        const resp = await fetch('/api/memory/recent?limit=30&project_id=' + (this.activeProjectId || ''));
        if (resp.ok) {
          const data = await resp.json();
          this.memoryChunks = Array.isArray(data) ? data : (data.chunks || []);
        }
      } catch (_) {}
    },

    async searchMemory() {
      const q = this.memorySearch.trim();
      if (!q) { return this.loadRecentMemory(); }
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
        this.send();
      }
    },

    badgeCls(cls) { return `badge ${cls}`; },

    copyCode(el) {
      const code = el.closest('pre')?.querySelector('code')?.textContent || '';
      navigator.clipboard.writeText(code).catch(() => {});
    },

    /* ================================================================
       RENDER HELPERS (for template)
       ================================================================ */
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
      return { connected: 'CONNECTED', degraded: 'DEGRADED', disconnected: 'OFFLINE' }[this.wsStatus] || 'OFFLINE';
    },

    get activeProjectName() {
      if (!this.activeProjectId) return 'default';
      const p = this.projects.find(p => p.project_id === this.activeProjectId);
      return p ? (p.name || p.project_id) : this.activeProjectId;
    },

    formatScore(s) {
      return parseFloat(s || 0).toFixed(1);
    },

    chunkLabel(chunk) {
      return chunk.text_excerpt || chunk.text || '';
    },

    usagePercent(used, max) {
      if (!max || !used) return 0;
      return Math.min(100, Math.round((used / max) * 100));
    },
  }));
});
