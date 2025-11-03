// Simple client for CodespacesOS web console
(() => {
  const $ = sel => document.querySelector(sel);
  const consoleEl = $('#console');
  const inputEl = $('#cmd-input');
  const sendBtn = $('#send-btn');
  const clearBtn = $('#clear-btn');
  const downloadBtn = $('#download-btn');
  const loading = $('#loading');
  const app = $('#app');
  const connBadge = $('#connection');
  const pollSelect = $('#poll-interval');

  let lastSeq = 0;
  let pollTimer = null;
  let pollInterval = parseInt(pollSelect.value, 10) || 1000;
  let history = [];
  let histIndex = -1;
  let isConnected = false;

  function setConnected(state) {
    isConnected = state;
    connBadge.textContent = state ? 'Connected' : 'Disconnected';
    connBadge.classList.toggle('connected', state);
    connBadge.classList.toggle('disconnected', !state);
  }

  async function checkStatusOnce(timeout = 3000) {
    try {
      const resp = await fetch('/status', {cache: 'no-store'});
      if (!resp.ok) throw new Error('not ok');
      const j = await resp.json();
      lastSeq = j.seq || 0;
      setConnected(true);
      return true;
    } catch (e) {
      setConnected(false);
      return false;
    }
  }

  async function fetchSeq() {
    try {
      const r = await fetch('/pollseq', {cache: 'no-store'});
      if (!r.ok) throw new Error('pollseq failed');
      const j = await r.json();
      return j.seq || 0;
    } catch (e) {
      setConnected(false);
      return null;
    }
  }

  async function fetchOutput() {
    try {
      const r = await fetch('/output', {cache: 'no-store'});
      if (!r.ok) throw new Error('output failed');
      const txt = await r.text();
      setConnected(true);
      return txt.split(/\r?\n/).filter(Boolean);
    } catch (e) {
      setConnected(false);
      return null;
    }
  }

  function appendLines(lines) {
    if (!lines || !lines.length) return;
    // Strip off any BOM/empty first
    const frag = document.createDocumentFragment();
    lines.forEach(line => {
      const div = document.createElement('div');
      // try to heuristically split timestamp prefix "[HH:MM:SS] "
      const tsMatch = line.match(/^\[(\d{2}:\d{2}:\d{2})\]\s*(.*)$/);
      if (tsMatch) {
        const tsSpan = document.createElement('span');
        tsSpan.className = 'ts';
        tsSpan.textContent = `[${tsMatch[1]}] `;
        div.appendChild(tsSpan);
        const rest = tsMatch[2];
        if (rest.startsWith('> ')) {
          const cmdSpan = document.createElement('span');
          cmdSpan.className = 'cmd';
          cmdSpan.textContent = rest;
          div.appendChild(cmdSpan);
        } else if (/error/i.test(rest)) {
          const err = document.createElement('span');
          err.className = 'err';
          err.textContent = rest;
          div.appendChild(err);
        } else {
          div.appendChild(document.createTextNode(rest));
        }
      } else {
        div.textContent = line;
      }
      frag.appendChild(div);
    });
    consoleEl.appendChild(frag);
    // auto-scroll
    consoleEl.parentElement.scrollTop = consoleEl.parentElement.scrollHeight;
  }

  async function pollOnce() {
    const seq = await fetchSeq();
    if (seq === null) return;
    if (seq > lastSeq) {
      const lines = await fetchOutput();
      if (!lines) return;
      // take only the new ones
      const newLines = lines.slice(lastSeq);
      lastSeq = seq;
      appendLines(newLines);
    }
  }

  function startPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = setInterval(pollOnce, pollInterval);
  }

  async function sendCommand(cmd) {
    if (!cmd) return;
    // optimistic echo
    appendLines([`[${new Date().toLocaleTimeString()}] > ${cmd}`]);
    history.unshift(cmd);
    histIndex = -1;
    inputEl.value = '';
    try {
      sendBtn.disabled = true;
      const r = await fetch('/command', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({cmd})
      });
      sendBtn.disabled = false;
      if (!r.ok) throw new Error('command failed');
      const j = await r.json();
      // the response includes the whole out string; we'll append it
      const outLines = (j.out || '').split(/\r?\n/).filter(Boolean);
      appendLines(outLines.map(l => `[${j.ts || new Date().toLocaleTimeString()}] ${l}`));
      lastSeq = j.seq || lastSeq;
    } catch (e) {
      sendBtn.disabled = false;
      appendLines([`[${new Date().toLocaleTimeString()}] Error sending command: ${e.message}`]);
      setConnected(false);
    }
  }

  // UI actions
  sendBtn.addEventListener('click', () => sendCommand(inputEl.value.trim()));
  inputEl.addEventListener('keydown', (ev) => {
    if (ev.key === 'Enter') {
      ev.preventDefault();
      sendCommand(inputEl.value.trim());
      return;
    }
    if (ev.key === 'ArrowUp') {
      ev.preventDefault();
      if (history.length === 0) return;
      histIndex = Math.min(history.length - 1, (histIndex === -1 ? 0 : histIndex + 1));
      inputEl.value = history[histIndex] || '';
      return;
    }
    if (ev.key === 'ArrowDown') {
      ev.preventDefault();
      if (history.length === 0) return;
      if (histIndex <= 0) {
        histIndex = -1;
        inputEl.value = '';
        return;
      }
      histIndex -= 1;
      inputEl.value = history[histIndex] || '';
      return;
    }
  });

  clearBtn.addEventListener('click', () => {
    consoleEl.textContent = '';
  });

  downloadBtn.addEventListener('click', () => {
    const blob = new Blob([consoleEl.textContent], {type: 'text/plain;charset=utf-8'});
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'codespacesos.log';
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  });

  pollSelect.addEventListener('change', () => {
    pollInterval = parseInt(pollSelect.value, 10);
    startPolling();
  });

  // init: wait for server status; show loading until server is reachable
  (async function init() {
    let attempts = 0;
    while (attempts < 12) {
      const ok = await checkStatusOnce();
      if (ok) break;
      attempts++;
      $('#loading-sub').textContent = `Retrying... (${attempts})`;
      await new Promise(r => setTimeout(r, 500));
    }
    // hide loading and show app
    loading.classList.add('hidden');
    app.classList.remove('hidden');
    // fetch full output once and populate
    const lines = await fetchOutput();
    if (lines) {
      appendLines(lines);
      lastSeq = lines.length;
    }
    startPolling();
  })();
})();
