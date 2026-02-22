import { LitElement, html, css } from 'lit';
import { customElement, state } from 'lit/decorators.js';

@customElement('remnant-planning-wizard')
export class PlanningWizard extends LitElement {
  static styles = css`
    :host { display: flex; flex-direction: column; height: 100%; }
    .container { max-width: 600px; margin: 0 auto; padding: 32px 16px; }
    h2 { color: var(--accent-light); font-size: 20px; margin-bottom: 24px; }
    .step { margin-bottom: 20px; }
    label { display: block; font-size: 13px; color: var(--text-muted); margin-bottom: 6px; }
    input, select, textarea {
      width: 100%;
      background: var(--bg-input);
      border: 1px solid var(--border);
      color: var(--text);
      padding: 10px 12px;
      border-radius: var(--radius);
      font-size: 14px;
      font-family: var(--font);
    }
    input:focus, select:focus, textarea:focus { outline: none; border-color: var(--accent); }
    .btn-row { display: flex; gap: 10px; margin-top: 24px; }
    button {
      padding: 10px 20px;
      border: none;
      border-radius: var(--radius);
      cursor: pointer;
      font-size: 14px;
      font-weight: 600;
    }
    .btn-primary { background: var(--accent); color: white; }
    .btn-secondary { background: var(--bg-input); color: var(--text); border: 1px solid var(--border); }
    .success { background: var(--bg-card); border: 1px solid var(--success); border-radius: var(--radius); padding: 16px; color: var(--success); }
  `;

  @state() private _step = 0;
  @state() private _answers: Record<string, any> = {};
  @state() private _created: any = null;

  private _questions = [
    { id: 'name', label: 'Project Name', type: 'text', placeholder: 'My Awesome Project' },
    { id: 'description', label: 'Description', type: 'textarea', placeholder: 'What is this project about?' },
    { id: 'template', label: 'Template', type: 'select', options: ['default', 'dev', 'research'] },
    { id: 'budget_usd_daily', label: 'Daily Budget (USD)', type: 'number', placeholder: '2.00' },
    { id: 'enable_mcp', label: 'Enable Claude Code MCP', type: 'checkbox' },
  ];

  private _answer(id: string, value: any) {
    this._answers = { ...this._answers, [id]: value };
  }

  private async _submit() {
    const r = await fetch('/api/projects', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: this._answers.name || 'New Project',
        description: this._answers.description || '',
        template: this._answers.template || 'default',
        budget_usd_daily: parseFloat(this._answers.budget_usd_daily || '2.0'),
        enable_mcp: this._answers.enable_mcp || false,
      }),
    });
    const d = await r.json();
    this._created = d.project;
  }

  private _renderQuestion(q: any) {
    if (q.type === 'textarea') {
      return html`<textarea rows="3" placeholder=${q.placeholder || ''} @input=${(e: Event) => this._answer(q.id, (e.target as HTMLTextAreaElement).value)}></textarea>`;
    }
    if (q.type === 'select') {
      return html`<select @change=${(e: Event) => this._answer(q.id, (e.target as HTMLSelectElement).value)}>
        ${q.options.map((o: string) => html`<option value=${o}>${o}</option>`)}
      </select>`;
    }
    if (q.type === 'checkbox') {
      return html`<input type="checkbox" @change=${(e: Event) => this._answer(q.id, (e.target as HTMLInputElement).checked)} /> Enable`;
    }
    return html`<input type=${q.type || 'text'} placeholder=${q.placeholder || ''} @input=${(e: Event) => this._answer(q.id, (e.target as HTMLInputElement).value)} />`;
  }

  render() {
    if (this._created) {
      return html`
        <div class="container">
          <div class="success">
            <h3>✓ Project created!</h3>
            <p>ID: <strong>${this._created.project_id}</strong></p>
            <p>Switch to it in the chat panel to start working.</p>
          </div>
        </div>
      `;
    }

    const q = this._questions[this._step];
    const isLast = this._step === this._questions.length - 1;

    return html`
      <div class="container">
        <h2>New Project Wizard (${this._step + 1}/${this._questions.length})</h2>
        <div class="step">
          <label>${q.label}</label>
          ${this._renderQuestion(q)}
        </div>
        <div class="btn-row">
          ${this._step > 0 ? html`<button class="btn-secondary" @click=${() => this._step--}>Back</button>` : ''}
          ${!isLast ? html`<button class="btn-primary" @click=${() => this._step++}>Next</button>` : ''}
          ${isLast ? html`<button class="btn-primary" @click=${this._submit}>Create Project</button>` : ''}
        </div>
      </div>
    `;
  }
}
