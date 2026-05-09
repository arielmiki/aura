const vocabEl  = document.getElementById('vocab');
const countEl  = document.getElementById('count');
const statusEl = document.getElementById('status');
const frameEl  = document.getElementById('frame');

let known = new Set();

function setStatus(s) {
  statusEl.textContent = s;
  statusEl.className = 'status-' + s;
}

function render(entries, justAdded) {
  vocabEl.innerHTML = '';
  for (const e of entries) {
    const div = document.createElement('div');
    div.className = 'word' + (e.word === justAdded ? ' new' : '');
    div.textContent = e.word;
    div.title = e.description || '';
    vocabEl.appendChild(div);
  }
  countEl.textContent = entries.length + ' words';
}

function refreshFrame() {
  frameEl.src = '/frame.jpg?t=' + Date.now();
}
setInterval(refreshFrame, 1000);
refreshFrame();

const ws = new WebSocket((location.protocol === 'https:' ? 'wss' : 'ws')
                       + '://' + location.host + '/ws');

ws.onmessage = (msg) => {
  const data = JSON.parse(msg.data);
  if (data.type === 'snapshot') {
    render(data.entries);
    setStatus(data.status || 'idle');
  } else if (data.type === 'word_learned') {
    fetch('/api/vocab').then(r => r.json()).then(j => {
      render(j.entries, data.word);
      setStatus(j.status || 'idle');
    });
  } else if (data.type === 'status') {
    setStatus(data.status);
  }
};

ws.onclose = () => setStatus('idle');
