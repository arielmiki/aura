// app.js — page state + WebSocket subscription.
// recorder.js owns mic/camera/POST.

const memoriesEl  = document.getElementById('memories');
const transcriptEl = document.getElementById('transcript');

function renderMemories(entries, justAddedId) {
  memoriesEl.innerHTML = '';
  // Most recent first
  for (const e of [...entries].reverse()) {
    const div = document.createElement('div');
    div.className = 'memory' + (e.id === justAddedId ? ' new' : '');
    div.textContent = e.fact;
    memoriesEl.appendChild(div);
  }
}

function setTranscript(youText, rockyText) {
  transcriptEl.innerHTML = '';
  if (youText) {
    const a = document.createElement('div');
    a.className = 'you';
    a.textContent = '> ' + youText;
    transcriptEl.appendChild(a);
  }
  if (rockyText) {
    const b = document.createElement('div');
    b.className = 'rocky';
    b.textContent = rockyText;
    transcriptEl.appendChild(b);
  }
}

// Expose for recorder.js
window.rocky = { setTranscript };

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
