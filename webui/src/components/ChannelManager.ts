import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';

@customElement('remnant-channel-manager')
export class ChannelManager extends LitElement {
  static styles = css`
    :host { display: flex; flex-direction: column; height: 100%; }
    .header { padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 14px; }
    .content { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 16px; }
    .channel-card {
      background: var(--bg-card);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 16px;
    }
    .channel-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
    .channel-name { font-weight: 600; font-size: 14px; }
    .status-badge {
      padding: 3px 8px;
      border-radius: 4px;
      font-size: 11px;
      font-weight: 600;
    }
    .connected { background: var(--success); color: white; }
    .disconnected { background: var(--bg-input); color: var(--text-muted); border: 1px solid var(--border); }
    .qr-img { width: 200px; height: 200px; border-radius: var(--radius); background: white; padding: 8px; }
    button {
      padding: 8px 14px;
      background: var(--accent);
      color: white;
      border: none;
      border-radius: var(--radius);
      cursor: pointer;
      font-size: 12px;
    }
  `;

  @state() private _whatsappStatus = { ready: false, qr: null as string | null };

  connectedCallback() {
    super.connectedCallback();
    this._pollWhatsApp();
  }

  private async _pollWhatsApp() {
    try {
      const r = await fetch('/api/admin/whatsapp/qr');
      if (r.ok) {
        const d = await r.json();
        this._whatsappStatus = { ready: d.status === 'authenticated', qr: d.qr };
      }
    } catch {}
    setTimeout(() => this._pollWhatsApp(), 5000);
  }

  render() {
    return html`
      <div class="header">Channel Manager</div>
      <div class="content">
        <div class="channel-card">
          <div class="channel-header">
            <span class="channel-name">🌐 Web Chat (WebSocket)</span>
            <span class="status-badge connected">Connected</span>
          </div>
          <p style="font-size:13px;color:var(--text-muted)">Always active — serves the chat panel above.</p>
        </div>

        <div class="channel-card">
          <div class="channel-header">
            <span class="channel-name">✈ Telegram</span>
            <span class="status-badge disconnected">Not configured</span>
          </div>
          <p style="font-size:13px;color:var(--text-muted);margin-bottom:8px">
            Set TELEGRAM_BOT_TOKEN in .env and restart to enable.
          </p>
        </div>

        <div class="channel-card">
          <div class="channel-header">
            <span class="channel-name">💬 WhatsApp</span>
            <span class="status-badge ${this._whatsappStatus.ready ? 'connected' : 'disconnected'}">
              ${this._whatsappStatus.ready ? 'Authenticated' : 'Scan QR to connect'}
            </span>
          </div>
          ${!this._whatsappStatus.ready && this._whatsappStatus.qr ? html`
            <img class="qr-img" src="${this._whatsappStatus.qr}" alt="WhatsApp QR Code" />
            <p style="font-size:12px;color:var(--text-muted);margin-top:8px">
              Scan with WhatsApp mobile → Linked Devices → Link a Device
            </p>
          ` : ''}
          ${!this._whatsappStatus.ready && !this._whatsappStatus.qr ? html`
            <p style="font-size:13px;color:var(--text-muted)">Start with: docker compose --profile whatsapp up</p>
          ` : ''}
          ${this._whatsappStatus.ready ? html`
            <p style="font-size:13px;color:var(--success)">WhatsApp ready — messages forwarded to Remnant</p>
          ` : ''}
        </div>
      </div>
    `;
  }
}
