import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';

@customElement('remnant-agent-graph')
export class AgentGraphView extends LitElement {
  static styles = css`
    :host { display: flex; flex-direction: column; height: 100%; background: var(--bg); }
    .header { padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 14px; }
    .graph-area { flex: 1; overflow: auto; padding: 16px; }
    .node-list { display: flex; flex-direction: column; gap: 8px; }
    .node {
      padding: 10px 14px;
      border-radius: var(--radius);
      border: 1px solid var(--border);
      background: var(--bg-card);
      font-size: 13px;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .status-dot {
      width: 8px; height: 8px; border-radius: 50%;
    }
    .idle { background: var(--text-muted); }
    .running { background: var(--accent); animation: pulse 1s infinite; }
    .done { background: var(--success); }
    .failed { background: var(--error); }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }
    .refresh-btn {
      margin: 8px 16px;
      padding: 6px 12px;
      background: var(--accent);
      color: white;
      border: none;
      border-radius: var(--radius);
      cursor: pointer;
      font-size: 12px;
    }
  `;

  @state() private _nodes: any[] = [];
  @state() private _edges: any[] = [];

  connectedCallback() {
    super.connectedCallback();
    this._fetch();
    setInterval(() => this._fetch(), 3000);
  }

  private async _fetch() {
    try {
      const r = await fetch('/api/admin/agent-graph');
      const data = await r.json();
      this._nodes = data.nodes || [];
      this._edges = data.edges || [];
    } catch {}
  }

  render() {
    return html`
      <div class="header">Agent Graph — Live</div>
      <button class="refresh-btn" @click=${this._fetch}>Refresh</button>
      <div class="graph-area">
        <div class="node-list">
          ${this._nodes.length === 0 ? html`<p style="color:var(--text-muted);font-size:13px">No active agents</p>` : ''}
          ${this._nodes.map(n => html`
            <div class="node">
              <div>
                <strong>${n.name}</strong>
                <span style="color:var(--text-muted);font-size:11px;margin-left:8px">${n.agent_type} · depth ${n.depth}</span>
                ${n.project_id ? html`<span style="color:var(--accent-light);font-size:11px;margin-left:4px">[${n.project_id}]</span>` : ''}
              </div>
              <span class="status-dot ${n.status}"></span>
            </div>
          `)}
        </div>
      </div>
    `;
  }
}
