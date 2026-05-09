// app.js — page state + WebSocket subscription.
// recorder.js owns mic/camera/POST.

const memoriesEl   = document.getElementById('memories');
const memCountEl   = document.getElementById('memory-count');
const transcriptEl = document.getElementById('transcript');

let isFirstTurn = true;

function renderMemories(entries, justAddedId) {
  memCountEl.textContent = entries.length;
  if (entries.length === 0) {
    memoriesEl.innerHTML = '<div class="empty-mem">no memories yet</div>';
    return;
  }
  memoriesEl.innerHTML = '';
  // Most recent first
  for (const e of [...entries].reverse()) {
    const div = document.createElement('div');
    div.className = 'memory' + (e.id === justAddedId ? ' new' : '');
    div.textContent = e.fact;
    memoriesEl.appendChild(div);
  }
}

function appendTurn(youText, rockyText) {
  if (isFirstTurn) {
    transcriptEl.innerHTML = '';
    isFirstTurn = false;
  }
  const turn = document.createElement('div');
  turn.className = 'turn';
  if (youText) {
    const a = document.createElement('div');
    a.className = 'you';
    a.innerHTML = '<span class="label">YOU</span>' + escapeHtml(youText);
    turn.appendChild(a);
  }
  if (rockyText) {
    const b = document.createElement('div');
    b.className = 'rocky';
    b.innerHTML = '<span class="label">ROCKY</span>' + escapeHtml(rockyText);
    turn.appendChild(b);
  }
  transcriptEl.appendChild(turn);
  transcriptEl.scrollTop = transcriptEl.scrollHeight;
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

// Mirror the status pill's class onto the orb so its animation tracks state.
// (recorder.js already owns `statusEl`; we re-fetch by id to avoid duplicate
// const declarations colliding in the shared global scope.)
const _orbEl = document.getElementById('orb');
const _statusElForOrb = document.getElementById('status');
const _syncOrb = () => {
  const m = _statusElForOrb.className.match(/status-([a-z]+)/);
  _orbEl.className = 'orb' + (m ? ' ' + m[1] : '');
};
new MutationObserver(_syncOrb).observe(_statusElForOrb, { attributes: true, attributeFilter: ['class'] });
_syncOrb();

// Expose for recorder.js (keeps the existing call signature)
window.rocky = {
  setTranscript: appendTurn,
};

const ws = new WebSocket(
  (location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host + '/ws'
);

ws.onmessage = (msg) => {
  const data = JSON.parse(msg.data);
  if (data.type === 'snapshot') {
    renderMemories(data.entries);
  } else if (data.type === 'memory_added') {
    fetch('/api/memories').then(r => r.json()).then(j => {
      renderMemories(j.entries, data.entry.id);
    });
  } else if (data.type === 'memory_compacted') {
    fetch('/api/memories').then(r => r.json()).then(j => renderMemories(j.entries));
  }
};
