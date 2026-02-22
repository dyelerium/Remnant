import { LitElement, html, css } from 'lit';
import { customElement, property, state } from 'lit/decorators.js';

interface LogLine {
  timestamp?: string;
  level?: string;
  event?: string;
  lane?: string;
  [key: string]: any;
}

@customElement('remnant-live-log')
export class LiveLog extends LitElement {
  static styles = css`
    :host { display: flex; flex-direction: column; height: 100%; background: var(--bg); }
    .toolbar {
      padding: 6px 12px;
      border-bottom: 1px solid var(--border);
      display: flex;
      gap: 8px;
      align-items: center;
      font-size: 12px;
    }
    select, input {
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 4px 8px;
      border-radius: 4px;
      font-size: 12px;
    }
    .log-area { flex: 1; overflow-y: auto; font-family: var(--font-mono); font-size: 11px; padding: 8px; }
    .line { display: flex; gap: 8px; padding: 1px 0; border-bottom: 1px solid rgba(255,255,255,0.03); }
    .ts { color: var(--text-muted); white-space: nowrap; }
    .lvl { width: 40px; text-align: center; font-weight: 700; border-radius: 3px; font-size: 10px; }
    .DEBUG { color: var(--text-muted); }
    .INFO { color: var(--use); }
    .WARNING { color: var(--warning); }
    .ERROR { color: var(--error); }
    .msg { flex: 1; word-break: break-all; }
    .lane-tag { color: var(--accent-light); font-size: 10px; }
  `;

  @property({ type: Boolean }) compact = false;
  @state() private _lines: LogLine[] = [];
  @state() private _levelFilter = 'ALL';
  @state() private _search = '';
  @state() private _paused = false;

  private _maxLines = 500;

  connectedCallback() {
    super.connectedCallback();
    // Connect to SSE log stream
    // In production, connect to /api/logs/stream (SSE endpoint)
    // For now, use polling as a simple fallback
    this._poll();
  }

  private async _poll() {
    if (!this._paused) {
      // Placeholder: in production, SSE stream from /api/logs/stream
      // this._addLine({ timestamp: new Date().toISOString(), level: 'DEBUG', event: 'poll' });
    }
    setTimeout(() => this._poll(), 2000);
  }

  private _addLine(line: LogLine) {
    this._lines = [...this._lines.slice(-this._maxLines + 1), line];
    // Auto-scroll
    this.updateComplete.then(() => {
      const el = this.shadowRoot?.querySelector('.log-area');
      if (el) el.scrollTop = el.scrollHeight;
    });
  }

  private _filteredLines() {
    return this._lines.filter(l => {
      if (this._levelFilter !== 'ALL' && l.level !== this._levelFilter) return false;
      if (this._search && !JSON.stringify(l).toLowerCase().includes(this._search.toLowerCase())) return false;
      return true;
    });
  }

  render() {
    const lines = this._filteredLines();
    return html`
      ${!this.compact ? html`
        <div class="toolbar">
          <span style="font-weight:600;color:var(--text-muted)">Live Log</span>
          <select @change=${(e: Event) => this._levelFilter = (e.target as HTMLSelectElement).value}>
            <option>ALL</option>
            <option>DEBUG</option>
            <option>INFO</option>
            <option>WARNING</option>
            <option>ERROR</option>
          </select>
          <input
            type="text"
            placeholder="Filter…"
            .value=${this._search}
            @input=${(e: Event) => this._search = (e.target as HTMLInputElement).value}
          />
          <button @click=${() => this._paused = !this._paused}
            style="padding:4px 8px;background:${this._paused ? 'var(--warning)' : 'var(--bg-input)'};border:1px solid var(--border);border-radius:4px;color:var(--text);cursor:pointer;font-size:11px">
            ${this._paused ? '▶ Resume' : '⏸ Pause'}
          </button>
          <button @click=${() => this._lines = []}
            style="padding:4px 8px;background:var(--bg-input);border:1px solid var(--border);border-radius:4px;color:var(--text-muted);cursor:pointer;font-size:11px">
            Clear
          </button>
        </div>
      ` : ''}
      <div class="log-area">
        ${lines.length === 0 ? html`<div style="color:var(--text-muted);padding:8px">No log entries yet</div>` : ''}
        ${lines.map(l => html`
          <div class="line">
            <span class="ts">${(l.timestamp || '').substring(11, 19)}</span>
            <span class="lvl ${l.level}">${l.level?.substring(0, 3) || '---'}</span>
            ${l.lane ? html`<span class="lane-tag">[${l.lane.substring(0, 8)}]</span>` : ''}
            <span class="msg">${l.event || JSON.stringify(l)}</span>
          </div>
        `)}
      </div>
    `;
  }
}
