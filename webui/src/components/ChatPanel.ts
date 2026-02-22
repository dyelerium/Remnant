import { LitElement, html, css } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

@customElement('remnant-chat-panel')
export class ChatPanel extends LitElement {
  static styles = css`
    :host {
      display: flex;
      flex-direction: column;
      height: 100%;
      background: var(--bg);
    }
    .messages {
      flex: 1;
      overflow-y: auto;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .message { display: flex; gap: 10px; align-items: flex-start; }
    .message.user { flex-direction: row-reverse; }
    .bubble {
      max-width: 70%;
      padding: 10px 14px;
      border-radius: var(--radius);
      font-size: 14px;
      line-height: 1.5;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .bubble.user { background: var(--accent); color: white; }
    .bubble.assistant { background: var(--bg-card); border: 1px solid var(--border); }
    .badge {
      font-size: 10px;
      padding: 2px 5px;
      border-radius: 4px;
      font-weight: 600;
      letter-spacing: 0.05em;
    }
    .badge-gen { background: var(--gen); color: white; }
    .badge-use { background: var(--use); color: white; }
    .badge-exe { background: var(--exe); color: black; }
    .badge-mcp { background: var(--mcp); color: white; }
    .input-row {
      display: flex;
      padding: 12px 16px;
      gap: 8px;
      border-top: 1px solid var(--border);
      background: var(--bg-card);
    }
    textarea {
      flex: 1;
      background: var(--bg-input);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      color: var(--text);
      font-family: var(--font);
      font-size: 14px;
      padding: 10px 12px;
      resize: none;
      height: 40px;
      max-height: 200px;
      outline: none;
    }
    textarea:focus { border-color: var(--accent); }
    button {
      background: var(--accent);
      color: white;
      border: none;
      border-radius: var(--radius);
      padding: 0 20px;
      cursor: pointer;
      font-weight: 600;
      font-size: 14px;
      transition: background 0.15s;
    }
    button:hover { background: var(--hover); }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .project-select {
      padding: 8px 16px;
      border-bottom: 1px solid var(--border);
      display: flex;
      align-items: center;
      gap: 8px;
      font-size: 13px;
      color: var(--text-muted);
    }
    select {
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 4px 8px;
      border-radius: 4px;
      font-size: 13px;
    }
  `;

  @property({ type: Object }) ws: WebSocket | null = null;
  @state() private _messages: Array<{role: string, content: string, badges?: string[]}> = [];
  @state() private _inputValue = '';
  @state() private _generating = false;
  @state() private _projectId = '';

  private _currentAssistantMsg = '';

  connectedCallback() {
    super.connectedCallback();
    this._attachWS();
    this._loadProjects();
  }

  private _attachWS() {
    if (!this.ws) return;
    this.ws.onmessage = (ev) => {
      try {
        const data = JSON.parse(ev.data);
        if (data.type === 'start') {
          this._currentAssistantMsg = '';
          this._messages = [...this._messages, { role: 'assistant', content: '' }];
        } else if (data.type === 'chunk') {
          this._currentAssistantMsg += data.content;
          const msgs = [...this._messages];
          msgs[msgs.length - 1] = { role: 'assistant', content: this._currentAssistantMsg };
          this._messages = msgs;
        } else if (data.type === 'done') {
          this._generating = false;
        }
      } catch {}
    };
  }

  private async _loadProjects() {
    // Projects loaded on demand
  }

  private _sendMessage() {
    if (!this._inputValue.trim() || this._generating || !this.ws) return;

    const msg = this._inputValue.trim();
    this._messages = [...this._messages, { role: 'user', content: msg }];
    this._inputValue = '';
    this._generating = true;

    this.ws.send(JSON.stringify({
      message: msg,
      project_id: this._projectId || null,
      session_id: 'web-session',
    }));
  }

  private _onKeydown(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      this._sendMessage();
    }
  }

  private _parseBadges(content: string): string[] {
    const badges = [];
    if (content.includes('[GEN]')) badges.push('GEN');
    if (content.includes('[USE]')) badges.push('USE');
    if (content.includes('[EXE]')) badges.push('EXE');
    if (content.includes('[MCP]')) badges.push('MCP');
    return badges;
  }

  render() {
    return html`
      <div class="project-select">
        Project:
        <select @change=${(e: Event) => this._projectId = (e.target as HTMLSelectElement).value}>
          <option value="">Global</option>
        </select>
      </div>
      <div class="messages">
        ${this._messages.map(m => html`
          <div class="message ${m.role}">
            <div class="bubble ${m.role}">
              ${this._parseBadges(m.content).map(b => html`
                <span class="badge badge-${b.toLowerCase()}">[${b}]</span>
              `)}
              ${m.content.replace(/\[(GEN|USE|EXE|MCP)\]/g, '')}
            </div>
          </div>
        `)}
      </div>
      <div class="input-row">
        <textarea
          .value=${this._inputValue}
          @input=${(e: Event) => this._inputValue = (e.target as HTMLTextAreaElement).value}
          @keydown=${this._onKeydown}
          placeholder="Message Remnant…"
          ?disabled=${this._generating}
        ></textarea>
        <button @click=${this._sendMessage} ?disabled=${this._generating || !this._inputValue.trim()}>
          ${this._generating ? '…' : 'Send'}
        </button>
      </div>
    `;
  }
}
