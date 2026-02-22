import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';

@customElement('remnant-memory-dashboard')
export class MemoryDashboard extends LitElement {
  static styles = css`
    :host { display: flex; flex-direction: column; height: 100%; }
    .header { padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 14px; display: flex; gap: 8px; align-items: center; }
    .content { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
    .search-row { display: flex; gap: 8px; }
    input {
      flex: 1;
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 12px;
      border-radius: var(--radius);
      font-size: 13px;
    }
    input:focus { outline: none; border-color: var(--accent); }
    button {
      background: var(--accent);
      color: white;
      border: none;
      border-radius: var(--radius);
      padding: 8px 16px;
      cursor: pointer;
      font-size: 13px;
    }
    .chunk {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 12px;
      font-size: 13px;
    }
    .chunk-meta { color: var(--text-muted); font-size: 11px; margin-bottom: 6px; display: flex; gap: 10px; flex-wrap: wrap; }
    .importance-GLOBAL_HIGH { color: var(--mcp); font-weight: 700; }
    .importance-PROJECT_HIGH { color: var(--accent-light); }
    .importance-EPHEMERAL { color: var(--text-muted); }
    .stat-row { display: flex; gap: 16px; flex-wrap: wrap; }
    .stat-card { background: var(--bg-card); border: 1px solid var(--border); border-radius: var(--radius); padding: 12px 16px; flex: 1; min-width: 120px; }
    .stat-value { font-size: 24px; font-weight: 700; color: var(--accent-light); }
    .stat-label { font-size: 11px; color: var(--text-muted); }
  `;

  @state() private _chunks: any[] = [];
  @state() private _query = '';
  @state() private _stats: any = {};

  connectedCallback() {
    super.connectedCallback();
    this._loadStats();
    this._loadRecent();
  }

  private async _loadStats() {
    const r = await fetch('/api/memory/stats');
    this._stats = await r.json();
  }

  private async _loadRecent() {
    const r = await fetch('/api/memory/recent?limit=20');
    const d = await r.json();
    this._chunks = d.chunks || [];
  }

  private async _search() {
    if (!this._query.trim()) return;
    const r = await fetch('/api/memory/search', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ query: this._query }),
    });
    const d = await r.json();
    this._chunks = d.chunks || [];
  }

  render() {
    return html`
      <div class="header">Memory Dashboard</div>
      <div class="content">
        <div class="stat-row">
          <div class="stat-card">
            <div class="stat-value">${this._stats.markdown_files || 0}</div>
            <div class="stat-label">Markdown files</div>
          </div>
          <div class="stat-card">
            <div class="stat-value">${this._chunks.length}</div>
            <div class="stat-label">Chunks shown</div>
          </div>
        </div>
        <div class="search-row">
          <input
            type="text"
            .value=${this._query}
            @input=${(e: Event) => this._query = (e.target as HTMLInputElement).value}
            @keydown=${(e: KeyboardEvent) => e.key === 'Enter' && this._search()}
            placeholder="Search memory…"
          />
          <button @click=${this._search}>Search</button>
          <button @click=${this._loadRecent} style="background:var(--bg-input);color:var(--text)">Recent</button>
        </div>
        ${this._chunks.map(c => html`
          <div class="chunk">
            <div class="chunk-meta">
              <span>${c.file_path || 'unknown'}</span>
              <span class="importance-${c.importance_label}">${c.importance_label}</span>
              <span>score: ${(c.useful_score || 0).toFixed(1)}</span>
              ${c.similarity != null ? html`<span>sim: ${(c.similarity * 100).toFixed(0)}%</span>` : ''}
              <span>${c.chunk_type}</span>
            </div>
            <div>${c.text_excerpt}</div>
          </div>
        `)}
      </div>
    `;
  }
}
