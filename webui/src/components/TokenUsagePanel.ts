import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';

@customElement('remnant-token-usage')
export class TokenUsagePanel extends LitElement {
  static styles = css`
    :host { display: flex; flex-direction: column; height: 100%; background: var(--bg-card); }
    .header { padding: 12px 16px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 13px; }
    .content { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 10px; }
    .metric { font-size: 13px; }
    .metric-label { color: var(--text-muted); font-size: 11px; }
    .metric-value { font-size: 18px; font-weight: 700; color: var(--accent-light); }
    .bar-track { height: 6px; background: var(--border); border-radius: 3px; margin-top: 4px; }
    .bar-fill { height: 100%; border-radius: 3px; background: var(--accent); transition: width 0.5s; }
  `;

  @state() private _usage: any = {};
  @state() private _maxTokens = 5_000_000;

  connectedCallback() {
    super.connectedCallback();
    this._fetch();
    setInterval(() => this._fetch(), 10000);
  }

  private async _fetch() {
    try {
      const r = await fetch('/api/llm/usage');
      this._usage = await r.json();
    } catch {}
  }

  private _pct(val: number, max: number) {
    return Math.min(100, (val / max) * 100);
  }

  render() {
    const tokens = this._usage.tokens_today || 0;
    const cost = this._usage.cost_usd_today || 0;
    const maxTokens = this._usage.max_tokens_day || this._maxTokens;
    const maxCost = this._usage.max_cost_day || 20;

    return html`
      <div class="header">Token Usage Today</div>
      <div class="content">
        <div class="metric">
          <div class="metric-label">Tokens</div>
          <div class="metric-value">${tokens.toLocaleString()}</div>
          <div class="bar-track">
            <div class="bar-fill" style="width: ${this._pct(tokens, maxTokens)}%"></div>
          </div>
          <div class="metric-label" style="margin-top:2px">${this._pct(tokens, maxTokens).toFixed(1)}% of ${(maxTokens/1e6).toFixed(1)}M cap</div>
        </div>
        <div class="metric">
          <div class="metric-label">Cost (USD)</div>
          <div class="metric-value">$${cost.toFixed(4)}</div>
          <div class="bar-track">
            <div class="bar-fill" style="width: ${this._pct(cost, maxCost)}%"></div>
          </div>
          <div class="metric-label" style="margin-top:2px">${this._pct(cost, maxCost).toFixed(1)}% of $${maxCost} cap</div>
        </div>
      </div>
    `;
  }
}
