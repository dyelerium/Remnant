# Design: Clear Chat, Channel Isolation, Telegram Project Mode, UI Fixes

Date: 2026-03-04
Status: Approved

---

## Overview

Four areas of work:
1. **Clear Chat** — broom button (UI-only archive) + archive section in sidebar + Telegram `/clear`
2. **Channel Isolation** — web UI chats no longer mirror to Telegram; Telegram → Web UI still works
3. **Telegram Project Mode** — detect "open project X" commands, route through project context, mirror to web UI project chat
4. **UI Fixes** — remove header project pill, rename sidebar label, fix textarea alignment, fix inline image sizing + lightbox

---

## Feature 1 — Clear Chat

### Web UI (broom button)
- **Button**: 🧹 emoji in a rounded-square chip, placed in `input-right` div next to the 💰 budget chip
- **Action**: moves `chat.messages` → `chat.archivedMessages[]`, clears `chat.messages` to `[]`
- **Redis session history**: untouched (LLM conversation context preserved)
- **localStorage**: archived messages persist (not lost, just hidden from main view)

### Archive section in sidebar
- **Button**: square 🗃️ icon button in sidebar footer, inline with "✚ New chat"
- **Action**: toggles `showArchived` flag
- When `showArchived = true`: show an "Archived" section above the footer listing chats that have `archivedMessages.length > 0`

### Scroll fix
- On `init()`, after restoring chats from localStorage, call `scrollToBottom()` with a 150ms delay to allow images to load before measuring `scrollHeight`
- No CSS `scroll-behavior: smooth` — keep instant scroll

### Telegram `/clear`
- Detected as first-class command in `telegram_bot.py` before hitting LLM
- Deletes `remnant:session:history:tg-{chat_id}` from Redis directly
- Replies: "✓ Chat cleared"

---

## Feature 2 — Channel Isolation

### Remove Web UI → Telegram mirror
- Delete the `await _mirror_to_telegram(...)` call from the WebSocket handler in `api/routes/chat.py`
- The `_mirror_to_telegram` function itself can stay (used by Telegram project mode broadcast)

### Telegram → Web UI stays
- Existing `tg_message` broadcast continues unchanged
- Telegram conversations still appear in the Web UI "Telegram: {sender}" chat window

---

## Feature 3 — Telegram Project Mode

### Redis state
- Key: `remnant:tg:{chat_id}:project` → stores `project_id` string (absent = plain mode)

### Command detection in `telegram_bot.py`
Commands are matched via regex **before** the message reaches the LLM:

| Pattern | Action |
|---|---|
| `list projects` / `show projects` / `show my projects` | Load projects from config YAML, reply with numbered list |
| `open project (.+)` / `switch to project (.+)` / `use project (.+)` | Fuzzy match project name, set Redis key, reply confirmation |
| `switch to telegram` / `exit project` / `close project` / `back to telegram` | Clear Redis key, reply confirmation |
| `/clear` | Delete session history Redis key, reply "✓ Chat cleared" |

### While project is active
- Load `active_project_id` from Redis at start of each message handler
- Pass `project_id=active_project_id` to `orchestrator.handle()` → memory uses project namespace
- Session ID stays `tg-{chat_id}` (no change to conversation history structure)
- Prefix Telegram response: `[{ProjectName}]\n\n{response}`

### Web UI mirroring (project mode only)
- `push_payload` gains `project_id` field when project is active
- `handleTelegramPush` in `app.js` checks for `msg.project_id`:
  - Always updates/creates the `Telegram: {sender}` chat (existing)
  - If `msg.project_id` set: also update/create a chat with `projectId = msg.project_id` and `telegramSource: true` flag so it appears in the project's chat list in the sidebar

---

## Feature 4 — UI Fixes

### Fix #1 — Remove header project pill
- Delete `<div class="project-pill" x-text="activeProjectName"></div>` from `<header>`
- The project name is already shown in the footer bar of the chat area

### Fix #2 — Rename "Default" → "Chats"
- Change `<div class="sidebar-section-title">Default</div>` to `Chats`

### Fix #3 — Textarea vertical alignment
- The `.model-chip` and `.budget-chip` use `align-self: flex-end; margin-bottom: 4px` which pulls the `input-right` div's baseline down
- Fix: change `.input-pill` from `align-items: center` to `align-items: flex-end`, add `padding-bottom: 8px` to the pill, adjust clip button to `align-self: flex-end; margin-bottom: 6px`
- This gives ChatGPT-style bottom-aligned input where everything lines up at the text baseline

### Fix #4 — Inline image sizing + lightbox
**CSS** (`.msg-bubble img`):
```css
.msg-bubble img {
  max-width: 100%;
  height: auto;
  border-radius: 6px;
  cursor: zoom-in;
  display: block;
  margin: 6px 0;
}
```

**Click handler**: modify `renderMdWithCopy()` to inject `onclick="openImageLightbox(this.src)"` into all `<img>` tags in markdown output.

**Global function** `openImageLightbox(src)`: sets Alpine state `imageModalSrc = src; imageModalOpen = true; imageZoom = 1`

**Lightbox improvements** (existing `#imageModal` div):
- Add X close button (top-right corner, absolute positioned)
- Add `imageZoom: 1` Alpine state
- Add `@wheel.prevent` on image: zoom in/out with mouse wheel (clamped 0.5–4×)
- Add `@keydown.escape.window` handler to close modal and reset zoom
- Prevent image click from closing backdrop (`@click.stop` on image)

---

## Files Changed

| File | Changes |
|---|---|
| `webui/public/index.html` | Header pill removal, "Chats" rename, broom button, archive button, lightbox X + escape + zoom |
| `webui/public/css/style.css` | Input alignment, img sizing, broom/archive button styles, lightbox zoom |
| `webui/public/js/app.js` | `clearChat()`, archive toggle, `openImageLightbox()`, scroll fix, zoom state |
| `channels/telegram_bot.py` | `/clear` command, project mode commands, project_id in push_payload |
| `api/routes/chat.py` | Remove `_mirror_to_telegram()` call from WebSocket handler |
| `config/projects.yaml` | Read for project list in Telegram |
