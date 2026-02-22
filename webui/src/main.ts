import './theme.css';
import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import './components/ChatPanel';
import './components/AgentGraphView';
import './components/MemoryDashboard';
import './components/TokenUsagePanel';
import './components/SettingsModal';
import './components/ChannelManager';
import './components/LiveLog';
import './components/PlanningWizard';

@customElement('remnant-app')
export class RemnantApp extends LitElement {
  static styles = css`
    :host {
      display: grid;
      grid-template-columns: 1fr 320px;
      grid-template-rows: 48px 1fr 240px;
      height: 100vh;
      background: var(--bg);
      gap: 0;
    }
    .topbar {
      grid-column: 1 / -1;
      display: flex;
      align-items: center;
      padding: 0 16px;
      background: var(--bg-card);
      border-bottom: 1px solid var(--border);
      gap: 12px;
    }
    .logo {
      font-weight: 700;
      font-size: 18px;
      color: var(--accent-light);
      letter-spacing: 0.05em;
    }
    .nav-tabs {
      display: flex;
      gap: 4px;
      margin-left: auto;
    }
    .nav-tab {
      padding: 6px 12px;
      border-radius: var(--radius);
      cursor: pointer;
      font-size: 13px;
      color: var(--text-muted);
      background: transparent;
      border: none;
      transition: all 0.15s;
    }
    .nav-tab:hover, .nav-tab.active {
      background: var(--accent);
      color: white;
    }
    .main-panel { grid-row: 2; grid-column: 1; overflow: hidden; }
    .side-panel { grid-row: 2; grid-column: 2; overflow: hidden; border-left: 1px solid var(--border); }
    .bottom-panel {
      grid-column: 1 / -1;
      border-top: 1px solid var(--border);
      overflow: hidden;
    }
  `;

  @state() private _activeTab = 'chat';
  @state() private _showSettings = false;

  private _ws: WebSocket | null = null;

  connectedCallback() {
    super.connectedCallback();
    this._connectWS();
  }

  private _connectWS() {
    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    this._ws = new WebSocket(`${proto}//${location.host}/ws`);
    this._ws.onclose = () => setTimeout(() => this._connectWS(), 3000);
  }

  render() {
    return html`
      <div class="topbar">
        <span class="logo">⬡ REMNANT</span>
        <div class="nav-tabs">
          ${['chat', 'memory', 'graph', 'channels', 'logs'].map(tab => html`
            <button class="nav-tab ${this._activeTab === tab ? 'active' : ''}"
              @click=${() => this._activeTab = tab}>
              ${tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          `)}
          <button class="nav-tab" @click=${() => this._showSettings = true}>⚙</button>
        </div>
      </div>

      <div class="main-panel">
        ${this._activeTab === 'chat' ? html`<remnant-chat-panel .ws=${this._ws}></remnant-chat-panel>` : ''}
        ${this._activeTab === 'memory' ? html`<remnant-memory-dashboard></remnant-memory-dashboard>` : ''}
        ${this._activeTab === 'graph' ? html`<remnant-agent-graph></remnant-agent-graph>` : ''}
        ${this._activeTab === 'channels' ? html`<remnant-channel-manager></remnant-channel-manager>` : ''}
        ${this._activeTab === 'logs' ? html`<remnant-live-log></remnant-live-log>` : ''}
      </div>

      <div class="side-panel">
        <remnant-token-usage></remnant-token-usage>
      </div>

      <div class="bottom-panel">
        ${this._activeTab === 'logs' ? '' : html`<remnant-live-log compact></remnant-live-log>`}
      </div>

      ${this._showSettings ? html`
        <remnant-settings-modal @close=${() => this._showSettings = false}></remnant-settings-modal>
      ` : ''}
    `;
  }
}
