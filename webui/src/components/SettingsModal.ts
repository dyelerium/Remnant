import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';

@customElement('remnant-settings-modal')
export class SettingsModal extends LitElement {
  static styles = css`
    :host {
      position: fixed; inset: 0;
      display: flex; align-items: center; justify-content: center;
      background: rgba(0,0,0,0.7);
      z-index: 100;
    }
    .modal {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: 12px;
      width: 560px;
      max-height: 80vh;
      overflow: hidden;
      display: flex;
      flex-direction: column;
    }
    .modal-header {
      padding: 16px 20px;
      border-bottom: 1px solid var(--border);
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-weight: 600;
    }
    .close-btn { background: none; border: none; color: var(--text-muted); font-size: 20px; cursor: pointer; }
    .tabs { display: flex; border-bottom: 1px solid var(--border); }
    .tab {
      padding: 10px 16px;
      font-size: 13px;
      cursor: pointer;
      border-bottom: 2px solid transparent;
      color: var(--text-muted);
      background: none;
      border-top: none;
      border-left: none;
      border-right: none;
    }
    .tab.active { border-bottom-color: var(--accent); color: var(--text); }
    .tab-content { flex: 1; overflow-y: auto; padding: 20px; }
    .field { margin-bottom: 16px; }
    label { display: block; font-size: 12px; color: var(--text-muted); margin-bottom: 4px; }
    input, select {
      width: 100%;
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 8px 10px;
      border-radius: var(--radius);
      font-size: 13px;
    }
    input:focus { outline: none; border-color: var(--accent); }
    .save-btn {
      width: 100%;
      padding: 10px;
      background: var(--accent);
      color: white;
      border: none;
      border-radius: var(--radius);
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
      margin-top: 8px;
    }
  `;

  @state() private _tab = 'llm';

  private _close() {
    this.dispatchEvent(new CustomEvent('close', { bubbles: true, composed: true }));
  }

  render() {
    return html`
      <div class="modal">
        <div class="modal-header">
          Settings
          <button class="close-btn" @click=${this._close}>×</button>
        </div>
        <div class="tabs">
          ${['llm', 'budget', 'security', 'channels'].map(t => html`
            <button class="tab ${this._tab === t ? 'active' : ''}" @click=${() => this._tab = t}>${t.charAt(0).toUpperCase()+t.slice(1)}</button>
          `)}
        </div>
        <div class="tab-content">
          ${this._tab === 'llm' ? html`
            <div class="field">
              <label>Primary LLM</label>
              <select>
                <option>claude-sonnet-4-6</option>
                <option>claude-haiku-4-5-20251001</option>
                <option>gpt-4o-mini</option>
              </select>
            </div>
            <div class="field">
              <label>Anthropic API Key</label>
              <input type="password" placeholder="sk-ant-…" />
            </div>
            <div class="field">
              <label>OpenAI API Key</label>
              <input type="password" placeholder="sk-…" />
            </div>
            <button class="save-btn">Save LLM Settings</button>
          ` : ''}
          ${this._tab === 'budget' ? html`
            <div class="field">
              <label>Daily Token Cap</label>
              <input type="number" value="5000000" />
            </div>
            <div class="field">
              <label>Daily Cost Cap (USD)</label>
              <input type="number" step="0.01" value="20.00" />
            </div>
            <button class="save-btn">Save Budget</button>
          ` : ''}
          ${this._tab === 'security' ? html`
            <div class="field">
              <label>Injection Detection</label>
              <select><option>Enabled</option><option>Disabled</option></select>
            </div>
            <div class="field">
              <label>Prompt Redaction</label>
              <select><option>Enabled</option><option>Disabled</option></select>
            </div>
            <button class="save-btn">Save Security</button>
          ` : ''}
          ${this._tab === 'channels' ? html`
            <p style="font-size:13px;color:var(--text-muted)">Configure channels in Channels tab</p>
          ` : ''}
        </div>
      </div>
    `;
  }
}
