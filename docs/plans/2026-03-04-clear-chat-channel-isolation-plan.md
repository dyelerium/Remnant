# Clear Chat, Channel Isolation, Telegram Project Mode, UI Fixes — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add clear-chat (archive) UI, fix 4 web UI issues, isolate Telegram ↔ Web UI channels, and add Telegram project mode with dual-mirror to Web UI.

**Architecture:** Purely additive changes — new Alpine state, CSS rules, and Telegram bot command pre-processing. No new backend services. Telegram project state stored in Redis (`remnant:tg:{chat_id}:project`). Web UI mirroring removed from WS handler; Telegram messages already arrive via broadcast.

**Tech Stack:** Alpine.js (web UI), `aiogram` (Telegram bot), Redis, FastAPI, `marked.js` (markdown)

---

## Task 1: Remove header project pill + rename "Default" label

**Files:**
- Modify: `webui/public/index.html:156`
- Modify: `webui/public/index.html:243`

**Step 1: Delete the project pill line**

In `index.html` find and remove this exact line (line ~156):
```html
    <!-- Active project pill -->
    <div class="project-pill" x-text="activeProjectName"></div>
```

**Step 2: Rename "Default" to "Chats"**

Find (line ~243):
```html
        <div class="sidebar-section-title">Default</div>
```
Replace with:
```html
        <div class="sidebar-section-title">Chats</div>
```

**Step 3: Visual check**

Restart Docker, open `http://192.168.1.147:8000`, verify:
- No "Researcher" / "Default" pill in the header bar next to REMNANT logo
- Sidebar section below the projects says "Chats" not "DEFAULT"

**Step 4: Commit**
```bash
git add webui/public/index.html
git commit -m "ui: remove header project pill; rename Default sidebar label to Chats"
```

---

## Task 2: Fix textarea vertical alignment (#3 in screenshot)

The input pill uses `align-items: center` but `.model-chip` and `.budget-chip` have `align-self: flex-end; margin-bottom: 4px`, which drags the visual baseline of `input-right` downward. Fix: change the entire pill to `align-items: flex-end` so all elements align to the bottom consistently.

**Files:**
- Modify: `webui/public/css/style.css` — `.input-pill`, `.input-pill textarea`, `.input-clip-btn`

**Step 1: Update `.input-pill` alignment**

Find:
```css
.input-pill {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 8px 12px 8px 8px;
  transition: box-shadow 0.2s;
}
```
Replace with:
```css
.input-pill {
  display: flex;
  align-items: flex-end;
  gap: 8px;
  background: var(--bg3);
  border: 1px solid var(--border);
  border-radius: 24px;
  padding: 8px 12px 10px 8px;
  transition: box-shadow 0.2s;
}
```

**Step 2: Give clip button a bottom margin so it sits flush**

Find:
```css
.input-clip-btn {
  background: none;
  border: none;
  font-size: 16px;
  cursor: pointer;
  color: var(--text-muted);
  padding: 4px;
  line-height: 1;
  transition: color var(--transition);
}
```
Replace with:
```css
.input-clip-btn {
  background: none;
  border: none;
  font-size: 16px;
  cursor: pointer;
  color: var(--text-muted);
  padding: 4px;
  line-height: 1;
  margin-bottom: 2px;
  transition: color var(--transition);
}
```

**Step 3: Remove bottom alignment overrides from chips** (they'll naturally bottom-align via the flex-end parent)

Find:
```css
.model-chip {
  font-size: 10px;
  color: var(--text-muted);
  background: var(--bg4);
  padding: 2px 8px;
  border-radius: 10px;
  align-self: flex-end;
  margin-bottom: 4px;
  white-space: nowrap;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}
```
Replace with:
```css
.model-chip {
  font-size: 10px;
  color: var(--text-muted);
  background: var(--bg4);
  padding: 2px 8px;
  border-radius: 10px;
  margin-bottom: 6px;
  white-space: nowrap;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
}
```

Find:
```css
.budget-chip {
  font-size: 14px;
  background: var(--bg4);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1px 6px;
  align-self: flex-end;
  margin-bottom: 4px;
```
Replace with:
```css
.budget-chip {
  font-size: 14px;
  background: var(--bg4);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1px 6px;
  margin-bottom: 6px;
```

**Step 4: Visual check**

Open UI, verify the text cursor, clip 📎, model chip, budget 💰, and send ➤ buttons all align along the same horizontal baseline at the bottom of the pill.

**Step 5: Commit**
```bash
git add webui/public/css/style.css
git commit -m "ui: fix input pill vertical alignment — flex-end baseline for all elements"
```

---

## Task 3: Constrain inline markdown images to bubble width (#4 in screenshot)

**Files:**
- Modify: `webui/public/css/style.css` — add `.msg-bubble img` rule
- Modify: `webui/public/js/app.js` — update `renderMdWithCopy()`, add `openImageLightbox()` global

**Step 1: Add CSS to constrain images**

In `style.css`, find the `.msg-bubble` block (around line 340). After the block ending `}`, add:
```css
/* Inline markdown images — constrained to bubble width, click to open */
.msg-bubble img {
  max-width: 100%;
  height: auto;
  border-radius: 6px;
  cursor: zoom-in;
  display: block;
  margin: 6px 0;
  transition: opacity 0.15s;
}
.msg-bubble img:hover { opacity: 0.9; }
```

**Step 2: Inject click handler into markdown images**

In `app.js`, find `renderMdWithCopy`:
```js
function renderMdWithCopy(text) {
  if (typeof marked === 'undefined') return `<p>${(text || '').replace(/\n/g, '<br>')}</p>`;
  const html = marked.parse(text || '');
  return html.replace(
    /<pre><code/g,
    '<pre class="code-block"><button class="copy-code-btn" onclick="copyCode(this)">Copy</button><code'
  );
}
```
Replace with:
```js
function renderMdWithCopy(text) {
  if (typeof marked === 'undefined') return `<p>${(text || '').replace(/\n/g, '<br>')}</p>`;
  const html = marked.parse(text || '');
  return html
    .replace(
      /<pre><code/g,
      '<pre class="code-block"><button class="copy-code-btn" onclick="copyCode(this)">Copy</button><code'
    )
    .replace(
      /<img /g,
      '<img onclick="openImageLightbox(this.src)" '
    );
}
```

**Step 3: Add the global `openImageLightbox` function**

In `app.js`, right after the `copyCode` function (around line 100), add:
```js
function openImageLightbox(src) {
  // Find the Alpine component and set state
  const el = document.querySelector('[x-data]');
  if (el && el._x_dataStack) {
    const data = el._x_dataStack[0];
    data.imageModalSrc = src;
    data.imageModalOpen = true;
    data.imageZoom = 1;
  }
}
```

**Step 4: Visual check**

Send a message that returns an image URL in markdown (e.g. ask for a photo). Verify:
- Image fits within chat bubble width
- Clicking image opens the existing lightbox
- Large images don't overflow horizontally

**Step 5: Commit**
```bash
git add webui/public/css/style.css webui/public/js/app.js
git commit -m "ui: constrain inline markdown images to bubble width; click-to-lightbox"
```

---

## Task 4: Improve image lightbox (zoom, X button, Escape key)

**Files:**
- Modify: `webui/public/index.html` — lightbox HTML
- Modify: `webui/public/css/style.css` — lightbox styles
- Modify: `webui/public/js/app.js` — add `imageZoom` state, keyboard handler

**Step 1: Add `imageZoom` state to Alpine**

In `app.js`, find:
```js
    /* --- Image modal --- */
    imageModalOpen: false,
    imageModalSrc: null,
```
Replace with:
```js
    /* --- Image modal --- */
    imageModalOpen: false,
    imageModalSrc: null,
    imageZoom: 1,
```

**Step 2: Add Escape key handler**

In `app.js`, find the `init()` method. After `this.autoResize();` at the bottom of `init()`, add:
```js
      // Escape key closes image lightbox
      document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && this.imageModalOpen) {
          this.imageModalOpen = false;
          this.imageModalSrc = null;
          this.imageZoom = 1;
        }
      });
```

**Step 3: Update lightbox HTML**

Find the existing image modal block (near end of `index.html`):
```html
<!-- ================================================================
     IMAGE MODAL
     ================================================================ -->
<div class="modal-backdrop" x-show="imageModalOpen" @click="imageModalOpen=false;imageModalSrc=null" style="display:none" x-transition>
  <div @click.stop style="max-width:90vw;max-height:90vh">
    <img :src="imageModalSrc" style="max-width:90vw;max-height:90vh;border-radius:8px;box-shadow:0 24px 60px rgba(0,0,0,0.7)" />
  </div>
</div>
```
Replace with:
```html
<!-- ================================================================
     IMAGE MODAL
     ================================================================ -->
<div class="modal-backdrop" x-show="imageModalOpen"
     @click="imageModalOpen=false;imageModalSrc=null;imageZoom=1"
     style="display:none" x-transition>
  <div class="lightbox-frame" @click.stop>
    <!-- X close button -->
    <button class="lightbox-close" @click="imageModalOpen=false;imageModalSrc=null;imageZoom=1">✕</button>
    <!-- Zoom hint -->
    <div class="lightbox-hint">Scroll to zoom · Click outside to close</div>
    <!-- Image with zoom -->
    <div class="lightbox-img-wrap"
         @wheel.prevent="imageZoom = Math.min(4, Math.max(0.25, imageZoom - $event.deltaY * 0.001))">
      <img :src="imageModalSrc"
           :style="`transform: scale(${imageZoom}); transform-origin: center center;`"
           class="lightbox-img"
           @click.stop />
    </div>
  </div>
</div>
```

**Step 4: Add lightbox CSS**

In `style.css`, find existing lightbox styles if any (search for `.modal-backdrop`). Add these rules after the existing `.modal-backdrop` rules:
```css
.lightbox-frame {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: center;
  max-width: 92vw;
  max-height: 92vh;
}
.lightbox-close {
  position: absolute;
  top: -16px;
  right: -16px;
  width: 32px;
  height: 32px;
  border-radius: 50%;
  background: var(--bg3);
  border: 1px solid var(--border);
  color: var(--text);
  font-size: 14px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 10;
  transition: background var(--transition);
}
.lightbox-close:hover { background: var(--bg4); }
.lightbox-hint {
  font-size: 11px;
  color: rgba(255,255,255,0.4);
  margin-bottom: 8px;
  pointer-events: none;
}
.lightbox-img-wrap {
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
  max-width: 90vw;
  max-height: 85vh;
}
.lightbox-img {
  max-width: 90vw;
  max-height: 85vh;
  border-radius: 8px;
  box-shadow: 0 24px 60px rgba(0,0,0,0.7);
  transition: transform 0.1s ease;
  cursor: zoom-in;
  display: block;
}
```

**Step 5: Visual check**

Open a chat with an image, click it. Verify:
- X button visible top-right
- Mouse wheel zooms in/out
- Click outside closes
- Pressing Escape closes
- Hint text visible

**Step 6: Commit**
```bash
git add webui/public/index.html webui/public/css/style.css webui/public/js/app.js
git commit -m "ui: image lightbox — zoom (scroll wheel), X button, Escape key"
```

---

## Task 5: Clear chat broom button (archive, no Redis delete)

**Files:**
- Modify: `webui/public/index.html` — add broom button to `input-right`
- Modify: `webui/public/css/style.css` — add `.clear-chip` style
- Modify: `webui/public/js/app.js` — add `clearChat()` method

**Step 1: Add `clearChat()` method**

In `app.js`, find the `newChat()` method (around line 711). Just before it, add:
```js
    clearChat() {
      const chat = this.activeChat;
      if (!chat || chat.messages.length === 0) return;
      // Archive current messages, start fresh (Redis history untouched)
      chat.archivedMessages = [...(chat.archivedMessages || []), ...chat.messages];
      chat.messages = [];
      chat.clearedAt = new Date().toISOString();
    },
```

**Step 2: Add broom button to `input-right` in HTML**

Find the `input-right` div containing the budget chip (around line 386):
```html
          <button class="budget-chip" :class="{ active: budgetModeEnabled }"
                  @click="budgetModeEnabled = !budgetModeEnabled"
                  title="Budget mode: auto-select cheapest capable model">💰</button>
```
Add the broom button **before** the budget chip:
```html
          <button class="clear-chip" @click="clearChat()"
                  :disabled="isStreaming || !activeChat || activeMessages.length === 0"
                  title="Clear chat (archives messages, keeps AI memory)">🧹</button>
          <button class="budget-chip" :class="{ active: budgetModeEnabled }"
                  @click="budgetModeEnabled = !budgetModeEnabled"
                  title="Budget mode: auto-select cheapest capable model">💰</button>
```

**Step 3: Add `.clear-chip` CSS**

In `style.css`, right after the `.budget-chip.active` block, add:
```css
.clear-chip {
  font-size: 14px;
  background: var(--bg4);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 1px 6px;
  margin-bottom: 6px;
  cursor: pointer;
  opacity: 0.45;
  transition: opacity var(--transition), background var(--transition);
}
.clear-chip:hover:not(:disabled) { opacity: 0.8; background: rgba(239,68,68,0.12); border-color: rgba(239,68,68,0.4); }
.clear-chip:disabled { opacity: 0.2; cursor: not-allowed; }
```

**Step 4: Visual check**

Open a chat with messages. Click 🧹. Verify messages disappear from view. Switch to another chat and back — messages remain gone. Refresh page — messages still gone (localStorage updated). Check that sending a new message still works (session continues).

**Step 5: Commit**
```bash
git add webui/public/index.html webui/public/css/style.css webui/public/js/app.js
git commit -m "feat: clear chat broom button — archives messages, preserves Redis session history"
```

---

## Task 6: Archive section in sidebar footer

**Files:**
- Modify: `webui/public/index.html` — sidebar footer + archived section
- Modify: `webui/public/css/style.css` — archive button style
- Modify: `webui/public/js/app.js` — `showArchived` state, computed `archivedChats`

**Step 1: Add `showArchived` state**

In `app.js`, in the state block, find:
```js
    /* --- Budget mode (persists across send; toggled locally or saved globally) --- */
    budgetModeEnabled: false,
```
Add before it:
```js
    /* --- Archive view --- */
    showArchived: false,
```

**Step 2: Add `archivedChats` computed getter**

In `app.js`, find the `get sidebarProjects()` computed getter. After it, add:
```js
    get archivedChats() {
      return this.chats.filter(c => c.archivedMessages && c.archivedMessages.length > 0);
    },
```

**Step 3: Add archived section + archive button to sidebar HTML**

In `index.html`, find the sidebar footer:
```html
    <!-- Sidebar footer -->
    <div class="sidebar-footer">
      <button class="btn-new" @click="newChat()">✚ New chat</button>
    </div>
```
Replace with:
```html
    <!-- Archived chats section (shown when toggled) -->
    <div x-show="showArchived && archivedChats.length > 0" style="display:none">
      <div class="sidebar-divider"></div>
      <div class="sidebar-section">
        <div class="sidebar-section-title" style="color:var(--text-muted)">Archived</div>
        <template x-for="chat in archivedChats" :key="'arch-' + chat.id">
          <div class="sidebar-item" :class="{ active: activeChatId === chat.id }"
               @click="selectChat(chat.id)" style="position:relative;opacity:0.7">
            <span class="item-icon">🗃️</span>
            <span class="item-label" x-text="chat.title"></span>
          </div>
        </template>
      </div>
    </div>

    <!-- Sidebar footer -->
    <div class="sidebar-footer" style="display:flex;gap:6px">
      <button class="btn-new" style="flex:1" @click="newChat()">✚ New chat</button>
      <button class="btn-archive" :class="{ active: showArchived }"
              @click="showArchived = !showArchived"
              :title="showArchived ? 'Hide archived' : 'Show archived chats'">🗃️</button>
    </div>
```

**Step 4: Add `.btn-archive` CSS**

In `style.css`, find `.sidebar-footer` styles. After them, add:
```css
.btn-archive {
  width: 38px;
  height: 38px;
  border-radius: 10px;
  background: var(--bg3);
  border: 1px solid var(--border);
  font-size: 16px;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0.5;
  transition: opacity var(--transition), background var(--transition);
  flex-shrink: 0;
}
.btn-archive:hover { opacity: 0.85; }
.btn-archive.active { opacity: 1; background: rgba(124,58,237,0.15); border-color: rgba(124,58,237,0.4); }
```

**Step 5: Visual check**

Clear a chat (broom). Click 🗃️ in sidebar footer. Verify archived section appears with that chat. Click a chat in the archived section to view it. The archived messages should NOT be shown (they're in `archivedMessages`, not `messages`).

**Step 6: Commit**
```bash
git add webui/public/index.html webui/public/css/style.css webui/public/js/app.js
git commit -m "feat: archive section in sidebar footer — toggle with glyph button"
```

---

## Task 7: Fix page-load scroll (jump to bottom, no top-to-bottom animation)

The issue: on refresh, the chat area renders at the top and images load asynchronously, so `scrollToBottom()` fires before images inflate the `scrollHeight`.

**Files:**
- Modify: `webui/public/js/app.js` — `init()` method

**Step 1: Call scrollToBottom with a delayed retry in `init()`**

In `app.js`, find `init()`:
```js
    async init() {
      const saved = loadChats();
      if (saved.length > 0) {
        this.chats = saved;
        this.activeChatId = saved[0].id;
      } else {
        this.newChat();
      }

      this.connectWS();
      await this.loadProjects();
      await this.loadMemoryStats();

      // Auto-save chats whenever they change
      this.$watch('chats', (v) => saveChats(v), { deep: true });

      // Show wizard on first launch
      if (!localStorage.getItem(LS_WIZARD)) {
        await this.startWizard();
      }

      // Ensure textarea renders with correct initial height
      this.autoResize();
    },
```

Add two lines at the very end of `init()` (before the closing `},`):
```js
      // Scroll to bottom immediately and again after images load
      this.scrollToBottom();
      setTimeout(() => this.scrollToBottom(), 200);
```

**Step 2: Verify the `scrollToBottom()` uses instant scroll (no smooth)**

Check that `chatArea` CSS does NOT have `scroll-behavior: smooth`. Search style.css for `scroll-behavior`. If found on `#chatArea`, remove it.

**Step 3: Refresh test**

Open a chat with many messages and at least one image. Hard-refresh (Ctrl+Shift+R). Verify the view starts at the bottom, no visible top-to-bottom scrolling animation.

**Step 4: Commit**
```bash
git add webui/public/js/app.js
git commit -m "fix: scroll to bottom on page load — delayed retry after images inflate scrollHeight"
```

---

## Task 8: Remove Web UI → Telegram mirroring

**Files:**
- Modify: `api/routes/chat.py` — remove `_mirror_to_telegram()` call from WS handler

**Step 1: Remove the mirror call**

In `api/routes/chat.py`, find (around line 315):
```python
            # Mirror to Telegram so web UI and Telegram stay in sync
            try:
                await _mirror_to_telegram(message, response_parts, ws.app.state.redis)
            except Exception:
                pass
```
Delete these 5 lines entirely.

The `_mirror_to_telegram` function itself stays (it's used conceptually, could be re-used by Telegram project mode later). Only the call site is removed.

**Step 2: Verify no other call sites**
```bash
grep -n "_mirror_to_telegram" api/routes/chat.py
```
Expected: only the function definition remains, no call sites.

**Step 3: Test**

Send a message from Web UI. Verify it does NOT appear in Telegram. Send a message from Telegram. Verify it still appears in Web UI "Telegram: {sender}" window.

**Step 4: Commit**
```bash
git add api/routes/chat.py
git commit -m "feat: channel isolation — remove web UI→Telegram mirror; Telegram→web UI stays"
```

---

## Task 9: Telegram `/clear` command

**Files:**
- Modify: `channels/telegram_bot.py` — detect `/clear` before LLM routing

**Step 1: Add `/clear` handling in `handle_message`**

In `telegram_bot.py`, find the `handle_message` handler. Right after `text = message.text or ""`, add:

```python
            # --- /clear command: wipe Redis session history ---
            if text.strip().lower() in ("/clear", "/clear@" + (self._bot.username or "").lower()):
                if self._redis:
                    import asyncio as _asyncio
                    key = f"remnant:session:history:tg-{chat_id}"
                    await _asyncio.get_event_loop().run_in_executor(
                        None, self._redis.r.delete, key
                    )
                await message.answer("✓ Chat cleared — conversation history reset.")
                return
```

**Step 2: Test**

In Telegram, send a few messages, then send `/clear`. Verify:
- Bot replies "✓ Chat cleared — conversation history reset."
- Next message has no memory of previous conversation (LLM starts fresh)

**Step 3: Commit**
```bash
git add channels/telegram_bot.py
git commit -m "feat: Telegram /clear command — deletes Redis session history for that chat"
```

---

## Task 10: Telegram project mode — command detection + listing

**Files:**
- Modify: `channels/telegram_bot.py` — add project command pre-processor + `_get_active_project()` helper

**Step 1: Add project state helpers**

In `telegram_bot.py`, below the `__init__` method (around line 28), add these two helper methods:

```python
    async def _get_active_project(self, chat_id: str) -> Optional[str]:
        """Return the active project_id for this Telegram chat, or None."""
        if not self._redis:
            return None
        try:
            import asyncio as _asyncio
            key = f"remnant:tg:{chat_id}:project"
            val = await _asyncio.get_event_loop().run_in_executor(
                None, self._redis.r.get, key
            )
            if val:
                return val.decode() if isinstance(val, bytes) else val
        except Exception:
            pass
        return None

    async def _set_active_project(self, chat_id: str, project_id: Optional[str]) -> None:
        """Set or clear the active project for this Telegram chat."""
        if not self._redis:
            return
        try:
            import asyncio as _asyncio
            key = f"remnant:tg:{chat_id}:project"
            if project_id:
                await _asyncio.get_event_loop().run_in_executor(
                    None, lambda: self._redis.r.set(key, project_id)
                )
            else:
                await _asyncio.get_event_loop().run_in_executor(
                    None, self._redis.r.delete, key
                )
        except Exception as _e:
            logger.warning("[TELEGRAM] Failed to set active project: %s", _e)
```

**Step 2: Add project command pre-processor**

In `handle_message`, after the `/clear` block, add:

```python
            import re as _re2

            # --- list projects ---
            if _re2.search(r"\b(list|show|what|my)\b.{0,20}\bprojects?\b", text.lower()):
                try:
                    pm = self._orchestrator.runtime._project_manager if hasattr(
                        self._orchestrator.runtime, '_project_manager') else None
                    # Fallback: load via orchestrator config
                    projects = []
                    if pm:
                        projects = pm.list_all()
                    if not projects:
                        await message.answer("No projects found. Create one in the Web UI first.")
                    else:
                        lines = ["📂 *Available projects:*\n"]
                        for i, p in enumerate(projects, 1):
                            name = p.get("name") or p.get("project_id", "?")
                            pid = p.get("project_id", "")
                            lines.append(f"{i}. *{name}* (`{pid}`)")
                        lines.append('\nSay _"open project <name>"_ to switch context.')
                        await message.answer("\n".join(lines), parse_mode="Markdown")
                except Exception as _exc:
                    logger.warning("[TELEGRAM] list projects failed: %s", _exc)
                    await message.answer("Could not load project list.")
                return

            # --- open project <name> ---
            _open_m = _re2.search(
                r"\b(?:open|switch\s+to|use|start|enter)\s+project\s+(.+)",
                text.lower()
            )
            if _open_m:
                target = _open_m.group(1).strip().rstrip(".")
                try:
                    pm = self._orchestrator.runtime._project_manager if hasattr(
                        self._orchestrator.runtime, '_project_manager') else None
                    matched_id = None
                    matched_name = None
                    if pm:
                        for p in pm.list_all():
                            pname = (p.get("name") or p.get("project_id", "")).lower()
                            pid = p.get("project_id", "")
                            if target in pname or pname in target or target == pid.lower():
                                matched_id = pid
                                matched_name = p.get("name") or pid
                                break
                    if matched_id:
                        await self._set_active_project(chat_id, matched_id)
                        await message.answer(
                            f"✅ Switched to project *{matched_name}*\n\n"
                            f"Responses will be prefixed with `[{matched_name}]`. "
                            f'Say _"switch to telegram chat"_ to return to normal mode.',
                            parse_mode="Markdown"
                        )
                    else:
                        await message.answer(
                            f'Project "{target}" not found. Say _"list projects"_ to see available ones.',
                            parse_mode="Markdown"
                        )
                except Exception as _exc:
                    logger.warning("[TELEGRAM] open project failed: %s", _exc)
                    await message.answer("Could not switch project.")
                return

            # --- close / exit project ---
            if _re2.search(
                r"\b(?:switch\s+to\s+telegram|exit\s+project|close\s+project|"
                r"back\s+to\s+telegram|leave\s+project|no\s+project)\b",
                text.lower()
            ):
                current = await self._get_active_project(chat_id)
                await self._set_active_project(chat_id, None)
                if current:
                    await message.answer("✅ Switched back to Telegram chat mode.")
                else:
                    await message.answer("Already in Telegram chat mode.")
                return
```

**Step 3: Wire project_manager into runtime (check if accessible)**

Check if `self._orchestrator.runtime._project_manager` is available:
```bash
grep -n "_project_manager\|project_manager" core/runtime.py
```
If not present, the `pm` fallback branch will hit `None` and show "No projects found". We need to pass `project_manager` to the Telegram bot or expose it via the orchestrator. Check `api/main.py` to see how the bot is instantiated.
```bash
grep -n "TelegramBot\|project_manager" api/main.py | head -20
```

**Step 4: If `project_manager` not on runtime, pass it to the bot**

In `api/main.py`, find the TelegramBot instantiation. Add `project_manager=app.state.project_manager` parameter, and update `TelegramBot.__init__` to accept and store it as `self._project_manager`.

Then in the command handler, replace `self._orchestrator.runtime._project_manager` with `self._project_manager`.

**Step 5: Test**

In Telegram: "list projects" → should show list of projects.
"open project researcher" → should reply "✅ Switched to project Researcher".
"switch to telegram" → should reply "✅ Switched back to Telegram chat mode".

**Step 6: Commit**
```bash
git add channels/telegram_bot.py api/main.py
git commit -m "feat: Telegram project mode — list/open/close project commands"
```

---

## Task 11: Telegram project mode — active project routing + response prefix + Web UI dual-mirror

**Files:**
- Modify: `channels/telegram_bot.py` — use active_project in orchestrator.handle(), prefix response, include project_id in push_payload

**Step 1: Load active project at start of every message**

In `handle_message`, after the command blocks (after the `return` of the "close project" block), the normal LLM path begins. Add this just before `response_parts = []`:

```python
            # Load active project for this chat
            active_project_id = await self._get_active_project(chat_id)
            project_display_name = ""
            if active_project_id and self._project_manager:
                proj = self._project_manager.get(active_project_id)
                if proj:
                    project_display_name = proj.get("name") or active_project_id
                else:
                    # Project was deleted — clear stale state
                    await self._set_active_project(chat_id, None)
                    active_project_id = None
```

**Step 2: Pass `project_id` to orchestrator**

Find the `orchestrator.handle(...)` call:
```python
            async for chunk in self._orchestrator.handle(
                message=text,
                session_id=f"tg-{chat_id}",
                channel="telegram",
                memory_context=memory_context,
            ):
```
Replace with:
```python
            async for chunk in self._orchestrator.handle(
                message=text,
                session_id=f"tg-{chat_id}",
                channel="telegram",
                memory_context=memory_context,
                project_id=active_project_id or None,
            ):
```

**Step 3: Prefix response with project name in Telegram**

Find where `response_text` is assembled:
```python
            response_text = "".join(response_parts)
            response_text = _re.sub(r"^\[GEN\] ", "", response_text)
            response_text = _re.sub(r"\[EXE\] \d+ tool\(s\) executed\n?", "", response_text)
            response_text = response_text.strip()
```
After the `.strip()` line, add:
```python
            # Prefix with project name when in project mode
            if active_project_id and project_display_name and response_text:
                response_text = f"[{project_display_name}]\n\n{response_text}"
```

**Step 4: Add `project_id` to push_payload so Web UI dual-mirrors**

Find where `push_payload` is assembled:
```python
            push_payload = {
                "session_id": f"tg-{chat_id}",
                "sender": username,
                "chat_id": chat_id,
                "user_message": text,
                "response": response_text,
            }
```
Replace with:
```python
            push_payload = {
                "session_id": f"tg-{chat_id}",
                "sender": username,
                "chat_id": chat_id,
                "user_message": text,
                "response": response_text,
                "project_id": active_project_id or None,
            }
```

**Step 5: Update Web UI `handleTelegramPush` to dual-mirror**

In `app.js`, find `handleTelegramPush(msg)`. After the existing block that creates/updates the Telegram chat, add:

```js
      // If project mode active: also append to project chat in sidebar
      if (msg.project_id) {
        const projSessionId = `tg-proj-${msg.project_id}`;
        const projExisting = this.chats.findIndex(c => c.sessionId === projSessionId);
        if (projExisting === -1) {
          this.chats.unshift({
            id: uid(),
            title: `📱 ${msg.project_id}`,
            projectId: msg.project_id,
            sessionId: projSessionId,
            messages: [...newMessages],
            ts: new Date().toISOString(),
            channel: 'telegram',
            telegramSource: true,
          });
        } else {
          for (const m of newMessages) {
            this.chats[projExisting].messages.push(m);
          }
        }
      }
```

**Step 6: Test end-to-end**

1. From Telegram, say "open project researcher"
2. Ask "what have we discussed about Romanian history?" → should reply with `[Researcher]` prefix and use researcher project memory
3. Open Web UI — verify the conversation appears in BOTH the "Telegram: {sender}" chat AND a chat under the Researcher project in the sidebar
4. From Web UI, send a message in the Researcher project → should NOT appear in Telegram
5. From Telegram, say "switch to telegram" → next message should have no prefix, no project memory

**Step 7: Commit**
```bash
git add channels/telegram_bot.py webui/public/js/app.js
git commit -m "feat: Telegram project mode — route through project, prefix response, dual-mirror to web UI"
```

---

## Task 12: Push and run tests

**Step 1: Run test suite**
```bash
sudo docker exec remnant python3 -m pytest tests/ -v 2>&1 | tail -30
```
Expected: all tests pass (51+). Fix any failures before pushing.

**Step 2: Push**
```bash
git push origin main
```

**Step 3: Rebuild Docker**
```bash
sudo docker compose -f /home/code/Remnant/docker-compose.yml up -d --build
```

**Step 4: Final smoke test checklist**
- [ ] Page refresh → starts at bottom of chat
- [ ] Broom clears chat, 🗃️ shows archived section
- [ ] Header has no project pill
- [ ] Sidebar "Chats" label correct
- [ ] Input textarea and icons horizontally aligned
- [ ] Inline images constrained to bubble width
- [ ] Click image → lightbox with zoom (scroll wheel) + X + Escape
- [ ] Web UI message NOT sent to Telegram
- [ ] Telegram message still appears in Web UI
- [ ] `/clear` in Telegram resets session
- [ ] "list projects" → numbered project list
- [ ] "open project X" → project mode, response prefixed
- [ ] Messages from project mode appear in BOTH Telegram window AND project chat in Web UI
- [ ] "switch to telegram" → back to normal mode
