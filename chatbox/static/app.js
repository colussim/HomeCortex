/* ── State ───────────────────────────────────────────────────────────────── */

const messagesEl = document.getElementById('messages');
const inputEl    = document.getElementById('input');
const sendBtn    = document.getElementById('sendBtn');
const roomSelect = document.getElementById('roomSelect');
const statusDot  = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const welcomeEl  = document.getElementById('welcome');

let hasMessages = false;
let isLoading   = false;

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function now() {
  return new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

function escHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/\n/g, '<br>');
}

function getRoom() {
  return roomSelect.value;
}

// Traductions injectées depuis le template Go
const i18n = window.KIRA_I18N || {
  statusOnline: 'online', statusOffline: 'offline',
  badgeHaOk: 'HA · OK', badgeHaErr: 'HA · ERR', badgeSpeech: 'SPEECH',
  errorBackend: 'Cannot reach Kira backend.',
};

function badgeInfo(category, haAck) {
  if (category === 'HA') {
    return haAck === 'ok'
      ? { cls: 'badge-ha-ok',  label: i18n.badgeHaOk }
      : { cls: 'badge-ha-err', label: i18n.badgeHaErr };
  }
  return { cls: 'badge-speech', label: i18n.badgeSpeech };
}

function scrollBottom() {
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

/* ── DOM builders ────────────────────────────────────────────────────────── */

function addUserMsg(text) {
  if (!hasMessages) {
    welcomeEl.remove();
    hasMessages = true;
  }
  const el = document.createElement('div');
  el.className = 'msg user';
  el.innerHTML = `
    <div class="msg-bubble">${escHtml(text)}</div>
    <div class="msg-meta">
      <span class="msg-time">${now()}</span>
    </div>`;
  messagesEl.appendChild(el);
  scrollBottom();
}

function addTyping() {
  const el = document.createElement('div');
  el.className = 'msg kira';
  el.id = 'typing';
  el.innerHTML = `
    <div class="typing">
      <span></span><span></span><span></span>
    </div>`;
  messagesEl.appendChild(el);
  scrollBottom();
}

function removeTyping() {
  const el = document.getElementById('typing');
  if (el) el.remove();
}

function addKiraMsg(reply, category, haAck, elapsed) {
  const { cls, label } = badgeInfo(category, haAck);
  const el = document.createElement('div');
  el.className = 'msg kira';
  el.innerHTML = `
    <div class="msg-bubble">${escHtml(reply)}</div>
    <div class="msg-meta">
      <span class="badge ${cls}">${label}</span>
      <span class="msg-time">⏱ ${elapsed} · ${now()}</span>
    </div>`;
  messagesEl.appendChild(el);
  scrollBottom();
}

function addErrorMsg(err) {
  const el = document.createElement('div');
  el.className = 'msg kira';
  el.innerHTML = `
    <div class="msg-bubble" style="color:var(--red);border-color:rgba(240,107,107,0.3)">
      ⚠ ${escHtml(err)}
    </div>`;
  messagesEl.appendChild(el);
  scrollBottom();
}

/* ── Send ────────────────────────────────────────────────────────────────── */

async function send(text) {
  if (!text.trim() || isLoading) return;

  isLoading = true;
  sendBtn.disabled = true;

  addUserMsg(text);
  addTyping();

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: text.trim(), room: getRoom() }),
    });

    const data = await res.json();
    removeTyping();

    if (data.error) {
      addErrorMsg(data.error);
    } else {
      addKiraMsg(data.reply, data.category, data.ha_ack, data.elapsed);
    }
  } catch (e) {
    removeTyping();
    addErrorMsg(i18n.errorBackend);
  }

  isLoading = false;
  sendBtn.disabled = false;
  inputEl.focus();
}

function sendSuggestion(el) {
  send(el.textContent);
}

/* ── Health check ────────────────────────────────────────────────────────── */

async function checkHealth() {
  try {
    const res = await fetch('/api/health');
    if (res.ok) {
      statusDot.className  = 'status-dot online';
      statusText.textContent = i18n.statusOnline;
    } else {
      throw new Error('not ok');
    }
  } catch {
    statusDot.className  = 'status-dot offline';
    statusText.textContent = i18n.statusOffline;
  }
}

/* ── Event listeners ─────────────────────────────────────────────────────── */

// Auto-resize textarea
inputEl.addEventListener('input', function () {
  this.style.height = 'auto';
  this.style.height = Math.min(this.scrollHeight, 120) + 'px';
});

// Send on Enter (Shift+Enter = newline)
inputEl.addEventListener('keydown', function (e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    const text = this.value.trim();
    if (text) {
      this.value = '';
      this.style.height = 'auto';
      send(text);
    }
  }
});

// Send button click
sendBtn.addEventListener('click', function () {
  const text = inputEl.value.trim();
  if (text) {
    inputEl.value = '';
    inputEl.style.height = 'auto';
    send(text);
  }
});

/* ── Init ────────────────────────────────────────────────────────────────── */

checkHealth();
setInterval(checkHealth, 60000);
inputEl.focus();
