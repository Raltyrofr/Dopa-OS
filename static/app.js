// Minimal frontend (no libs). Uses POST /command and GET /output polling.
// Keeps behavior terminal-like and shows DOPA OS boot output mirrored from server.

const term = document.getElementById('term');
const cmdInput = document.getElementById('cmd');
const promptEl = document.getElementById('prompt');

let lastSeq = 0;
const POLL_INTERVAL_MS = 700;

function setOutputText(raw) {
  term.textContent = raw + (raw && !raw.endsWith("\n") ? "\n" : "");
  term.scrollTop = term.scrollHeight;
}

async function fetchOutput() {
  try {
    const r = await fetch('/output', {cache: 'no-store'});
    if (r.ok) {
      const txt = await r.text();
      setOutputText(txt);
      lastSeq = (txt.match(/\n/g) || []).length;
    }
  } catch (e) {
    appendText("[Error fetching output]");
  }
}

function appendText(t) {
  if (!t) return;
  term.textContent += t + "\n";
  term.scrollTop = term.scrollHeight;
}

async function pollLoop() {
  try {
    const r = await fetch('/pollseq', {cache: 'no-store'});
    if (r.ok) {
      const j = await r.json();
      const seq = j.seq || 0;
      if (seq !== lastSeq) {
        const r2 = await fetch('/output', {cache: 'no-store'});
        if (r2.ok) {
          const txt = await r2.text();
          setOutputText(txt);
          lastSeq = seq;
        }
      }
    }
  } catch (e) {
    // ignore
  } finally {
    setTimeout(pollLoop, POLL_INTERVAL_MS);
  }
}

cmdInput.addEventListener('keydown', async (e) => {
  if (e.key === 'Enter') {
    const raw = cmdInput.value.trim();
    cmdInput.value = "";
    if (!raw) return;
    appendText(promptEl.textContent + raw);
    try {
      await fetch('/command', {
        method: 'POST',
        headers: {'Content-Type':'application/json'},
        body: JSON.stringify({cmd: raw})
      });
    } catch (err) {
      appendText("[Error sending command]");
    }
  }
});

term.addEventListener('click', () => cmdInput.focus());
cmdInput.focus();
fetchOutput().then(() => setTimeout(pollLoop, POLL_INTERVAL_MS));
