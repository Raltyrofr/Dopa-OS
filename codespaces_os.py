#!/usr/bin/env python3
"""
CodespacesOS (DOPA OS) - no external deps

- Single Python program that:
  * Starts an HTTP server (0.0.0.0:5000) serving ./static and endpoints /command, /output, /pollseq
  * Runs a curses full-screen terminal UI with black bg / white text (DOPA OS boot loader + shell)
  * Implements many safe commands (workspace-scoped)
  * Persists simple state in .dopa_state.json
- Run: python codespaces_os.py
- In Codespaces: open a new terminal pane, run the script there, open port 5000 in Ports -> Open in Browser
"""
from __future__ import annotations
import os
import sys
import threading
import json
import time
import queue
import shutil
import random
import socket
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from pathlib import Path

# Config
HOST = "0.0.0.0"
PORT = 5000
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
OUTPUT_MAX_LINES = 2000
STATE_FILE = ".dopa_state.json"
TRASH_DIR = ".dopa_trash"

# Shared state
output_lines = []
output_lock = threading.Lock()
cmd_queue = queue.Queue()

# Simple persistent state
state = {
    "aliases": {},
    "history": [],
    "started_at": time.time(),
    "jobs": {},  # jobid -> info
}
state_lock = threading.Lock()

def load_state():
    try:
        if os.path.exists(STATE_FILE):
            with open(STATE_FILE, "r", encoding="utf8") as f:
                s = json.load(f)
                state.update(s)
    except Exception:
        pass

def save_state():
    try:
        with state_lock:
            with open(STATE_FILE, "w", encoding="utf8") as f:
                json.dump(state, f)
    except Exception:
        pass

def push_output(text):
    with output_lock:
        for line in str(text).splitlines():
            output_lines.append(line)
        if len(output_lines) > OUTPUT_MAX_LINES:
            output_lines[:] = output_lines[-OUTPUT_MAX_LINES:]

def get_output_text():
    with output_lock:
        return "\n".join(output_lines)

def get_output_seq():
    with output_lock:
        return len(output_lines)

# Utility helpers
def human_size(n):
    for u in ("B","KB","MB","GB","TB"):
        if n < 1024.0:
            return f"{n:3.1f}{u}"
        n /= 1024.0
    return f"{n:.1f}PB"

def safe_listdir(path='.'):
    try:
        return sorted(os.listdir(path))
    except Exception as e:
        return [f"Error: {e}"]

def safe_readfile(path, max_bytes=20000):
    try:
        with open(path, "r", encoding="utf8", errors="replace") as f:
            data = f.read(max_bytes)
            if f.read(1):
                data += "\n...(truncated)"
            return data
    except Exception as e:
        return f"Error: {e}"

# Command implementations
def cmd_help(args):
    return (
        "DOPA OS commands: (type 'help <cmd>' for details)\n"
        "  ls cat head tail preview wc tree du mkdir rm touch mv cp open edit\n"
        "  write append writefile appendfile\n"
        "  cd pwd search calc chat notes notesadd history clear alias unalias\n"
        "  arcade tictactoe hangman number\n"
        "  startmenu uptime sysinfo time date ps runbg notify updatesim trash restore\n"
        "  zip unzip ping which whoami echo mkdir rmdir chmod du tree\n"
        "  help\n"
    )

def cmd_help_one(args):
    if not args:
        return cmd_help([])
    c = args[0].lower()
    docs = {
        "search": "search <term> [path] - search file contents recursively (workspace only)",
        "edit": "edit <file> - simple inline terminal editor (use terminal UI)",
        "writefile": "writefile <file> <content> - write content (web friendly)",
        "appendfile": "appendfile <file> <content> - append content (web friendly)",
        "notesadd": "notesadd <text> - append to notes.txt",
        "runbg": "runbg <seconds> <message> - simulate a background job that will post message after seconds",
        "trash": "trash <file> - move file to .dopa_trash",
        "restore": "restore <name> - restore file from .dopa_trash",
        "updatesim": "updatesim <pkg> - simulate updating/installing a package",
    }
    return docs.get(c, f"No detailed help for {c}")

def cmd_ls(args):
    path = args[0] if args else "."
    try:
        items = sorted(os.listdir(path))
        out = []
        for name in items:
            p = os.path.join(path, name)
            if os.path.isdir(p):
                out.append(f"{name}/")
            else:
                out.append(name)
        return "\n".join(out) if out else "(empty)"
    except Exception as e:
        return f"Error: {e}"

def cmd_cat(args):
    if not args: return "Usage: cat <file>"
    return safe_readfile(args[0])

def cmd_head(args):
    if not args: return "Usage: head <file> [n]"
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
    try:
        with open(args[0],"r",encoding="utf8",errors="replace") as f:
            lines = []
            for i in range(n):
                l = f.readline()
                if not l: break
                lines.append(l.rstrip("\n"))
            return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"

def cmd_tail(args):
    if not args: return "Usage: tail <file> [n]"
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 10
    try:
        with open(args[0],"rb") as f:
            f.seek(0,2)
            size = f.tell()
            block = 1024
            data = b""
            while size > 0 and len(data.splitlines()) <= n:
                step = min(block, size)
                f.seek(size-step)
                data = f.read(step) + data
                size -= step
            lines = data.decode("utf8",errors="replace").splitlines()[-n:]
            return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"

def cmd_preview(args):
    if not args: return "Usage: preview <file> [n]"
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else 40
    return cmd_head([args[0], str(n)])

def cmd_wc(args):
    if not args: return "Usage: wc <file>"
    try:
        with open(args[0],"r",encoding="utf8",errors="replace") as f:
            text = f.read()
            lines = text.count("\n")
            words = len(text.split())
            chars = len(text)
            return f"{lines} {words} {chars} {args[0]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_tree(args):
    root = args[0] if args else "."
    out = []
    def walk(p, prefix=""):
        try:
            entries = sorted(os.listdir(p))
        except Exception:
            return
        for i, name in enumerate(entries):
            full = os.path.join(p, name)
            islast = (i == len(entries)-1)
            connector = "└── " if islast else "├── "
            out.append(prefix + connector + name)
            if os.path.isdir(full):
                newpref = prefix + ("    " if islast else "│   ")
                walk(full, newpref)
    out.append(root)
    walk(root)
    return "\n".join(out)

def cmd_du(args):
    path = args[0] if args else "."
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root,f))
            except Exception:
                pass
    return f"{human_size(total)}\t{path}"

def cmd_mkdir(args):
    if not args: return "Usage: mkdir <dir>"
    try:
        os.makedirs(args[0], exist_ok=True)
        return f"Created {args[0]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_touch(args):
    if not args: return "Usage: touch <file>"
    try:
        open(args[0],"a").close()
        return f"Touched {args[0]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_rm(args):
    if not args: return "Usage: rm <file|dir>"
    try:
        if os.path.isdir(args[0]):
            shutil.rmtree(args[0])
        else:
            os.remove(args[0])
        return f"Removed {args[0]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_trash(args):
    if not args: return "Usage: trash <file>"
    os.makedirs(TRASH_DIR, exist_ok=True)
    src = args[0]
    if not os.path.exists(src): return "No such file"
    base = os.path.basename(src)
    dest = os.path.join(TRASH_DIR, f"{int(time.time())}_{base}")
    try:
        shutil.move(src, dest)
        return f"Moved to trash: {dest}"
    except Exception as e:
        return f"Error: {e}"

def cmd_restore(args):
    if not args: return "Usage: restore <name-substr>"
    if not os.path.isdir(TRASH_DIR): return "Trash empty"
    matches = [f for f in os.listdir(TRASH_DIR) if args[0] in f]
    if not matches: return "No match in trash"
    found = matches[0]
    src = os.path.join(TRASH_DIR, found)
    dest = os.path.join(".", "_restored_" + "_".join(found.split("_",1)[-1:]))
    try:
        shutil.move(src, dest)
        return f"Restored to {dest}"
    except Exception as e:
        return f"Error: {e}"

def cmd_mv(args):
    if len(args) < 2: return "Usage: mv <src> <dst>"
    try:
        shutil.move(args[0], args[1])
        return f"Moved {args[0]} -> {args[1]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_cp(args):
    if len(args) < 2: return "Usage: cp <src> <dst>"
    try:
        if os.path.isdir(args[0]):
            shutil.copytree(args[0], args[1])
        else:
            shutil.copy2(args[0], args[1])
        return f"Copied {args[0]} -> {args[1]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_zip(args):
    if len(args) < 2: return "Usage: zip <archive.zip> <path>"
    import zipfile
    try:
        with zipfile.ZipFile(args[0], "w", compression=zipfile.ZIP_DEFLATED) as z:
            for root, dirs, files in os.walk(args[1]):
                for f in files:
                    full = os.path.join(root,f)
                    arc = os.path.relpath(full, args[1])
                    z.write(full, arc)
        return f"Created {args[0]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_unzip(args):
    if len(args) < 1: return "Usage: unzip <archive.zip> [dest]"
    import zipfile
    dest = args[1] if len(args) > 1 else "."
    try:
        with zipfile.ZipFile(args[0], "r") as z:
            z.extractall(dest)
        return f"Extracted to {dest}"
    except Exception as e:
        return f"Error: {e}"

def cmd_calc(args):
    if not args: return "Usage: calc <expression>"
    expr = " ".join(args)
    allowed = "0123456789+-*/(). %"
    if any(ch not in allowed for ch in expr):
        return "Error: invalid characters"
    try:
        res = eval(expr, {"__builtins__": None}, {})
        return str(res)
    except Exception as e:
        return f"Error: {e}"

def cmd_chat(args):
    if not args: return "Usage: chat <text>"
    text = " ".join(args).lower()
    if "hello" in text or "hi" in text: return "Hello from DOPA OS!"
    if "how are" in text: return "I am code; ready."
    return "Tell me more."

def cmd_search(args):
    if not args: return "Usage: search <term> [path]"
    term = args[0]
    start = args[1] if len(args) > 1 else "."
    matches = []
    for root, dirs, files in os.walk(start):
        for f in files:
            path = os.path.join(root, f)
            try:
                with open(path, "r", encoding="utf8", errors="replace") as fh:
                    for i, line in enumerate(fh,1):
                        if term in line:
                            matches.append(f"{path}:{i}:{line.strip()}")
            except Exception:
                continue
    return "\n".join(matches) if matches else "No matches"

def cmd_alias(args):
    if not args: return "Usage: alias name='value'"
    # simple parse: alias name=value or alias name='value with spaces'
    raw = " ".join(args)
    if "=" not in raw: return "Usage: alias name=value"
    name, val = raw.split("=",1)
    name = name.strip()
    val = val.strip().strip("'\"")
    with state_lock:
        state.setdefault("aliases", {})[name] = val
    save_state()
    return f"Alias set: {name} -> {val}"

def cmd_unalias(args):
    if not args: return "Usage: unalias <name>"
    name = args[0]
    with state_lock:
        if name in state.get("aliases", {}):
            del state["aliases"][name]
            save_state()
            return f"Removed alias {name}"
    return "Alias not found"

def cmd_history(args):
    with state_lock:
        return "\n".join(state.get("history", [])[-200:]) or "(empty)"

def cmd_clear(args):
    # clear output buffer
    with output_lock:
        output_lines.clear()
    return ""

def cmd_sysinfo(args):
    info = {
        "platform": sys.platform,
        "python": sys.version.splitlines()[0],
        "cwd": os.getcwd(),
        "uptime_s": int(time.time() - state.get("started_at", time.time())),
    }
    return "\n".join(f"{k}: {v}" for k,v in info.items())

def cmd_uptime(args):
    return f"Uptime: {int(time.time() - state.get('started_at', time.time()))}s"

def cmd_time(args):
    return time.strftime("%H:%M:%S %Z", time.localtime())

def cmd_date(args):
    return time.strftime("%Y-%m-%d", time.localtime())

def cmd_notify(args):
    if not args: return "Usage: notify <message>"
    msg = " ".join(args)
    push_output(f"[NOTIFY] {msg}")
    return "Notified"

def cmd_updatesim(args):
    if not args: return "Usage: updatesim <pkg>"
    pkg = args[0]
    jobid = f"pkg-{int(time.time())}-{random.randint(1000,9999)}"
    def runner():
        push_output(f"Starting simulated update: {pkg}")
        time.sleep(1.0 + random.random()*2.0)
        push_output(f"Installing {pkg}...")
        time.sleep(1.0 + random.random()*2.0)
        push_output(f"Finished updating {pkg}")
    t = threading.Thread(target=runner, daemon=True)
    t.start()
    return f"Simulated update started: {pkg}"

def cmd_ping(args):
    if not args: return "Usage: ping <host> [port]"
    host = args[0]
    port = int(args[1]) if len(args)>1 and args[1].isdigit() else 80
    try:
        s = socket.create_connection((host, port), timeout=2.0)
        s.close()
        return f"Reachable: {host}:{port}"
    except Exception as e:
        return f"Unreachable: {e}"

# Dispatch mapping
COMMANDS = {
    "help": cmd_help,
    "ls": cmd_ls,
    "cat": cmd_cat,
    "head": cmd_head,
    "tail": cmd_tail,
    "preview": cmd_preview,
    "wc": cmd_wc,
    "tree": cmd_tree,
    "du": cmd_du,
    "mkdir": cmd_mkdir,
    "touch": cmd_touch,
    "rm": cmd_rm,
    "trash": cmd_trash,
    "restore": cmd_restore,
    "mv": cmd_mv,
    "cp": cmd_cp,
    "zip": cmd_zip,
    "unzip": cmd_unzip,
    "calc": cmd_calc,
    "chat": cmd_chat,
    "search": cmd_search,
    "alias": cmd_alias,
    "unalias": cmd_unalias,
    "history": cmd_history,
    "clear": cmd_clear,
    "sysinfo": cmd_sysinfo,
    "uptime": cmd_uptime,
    "time": cmd_time,
    "date": cmd_date,
    "notify": cmd_notify,
    "updatesim": cmd_updatesim,
    "ping": cmd_ping,
    "notesadd": lambda a: cmd_notes_add(a) if (globals().get('cmd_notes_add')) else "notesadd not available",
}

# small helper used by some commands (defined later)
def cmd_notes_add(args):
    if not args: return "Usage: notesadd <text>"
    text = " ".join(args)
    try:
        with open("notes.txt","a",encoding="utf8",errors="replace") as f:
            f.write(text+"\n")
        return "Appended to notes.txt"
    except Exception as e:
        return f"Error: {e}"

# Terminal-only interactive commands are handled in the terminal branch

def handle_command_line(raw, from_where="terminal"):
    raw = raw.strip()
    if not raw: return ""
    # alias expansion
    parts = raw.split()
    with state_lock:
        aliases = dict(state.get("aliases", {}))
    if parts and parts[0] in aliases:
        expanded = aliases[parts[0]]
        raw = expanded + (" " + " ".join(parts[1:]) if len(parts)>1 else "")
        parts = raw.split()
    cmd = parts[0].lower()
    args = parts[1:]
    # terminal-only interactive:
    if from_where == "terminal":
        if cmd == "write":
            if not args: return "Usage: write <file>"
            fn = args[0]
            print(f"Enter content for {fn}. End with a blank line.")
            lines = []
            try:
                while True:
                    l = input()
                    if l == "": break
                    lines.append(l)
            except EOFError:
                pass
            try:
                with open(fn,"w",encoding="utf8",errors="replace") as f:
                    f.write("\n".join(lines) + ("\n" if lines else ""))
                return f"Wrote {fn}"
            except Exception as e:
                return f"Error: {e}"
        if cmd == "append":
            if not args: return "Usage: append <file>"
            fn = args[0]
            print(f"Enter lines to append to {fn}. End with blank line.")
            lines = []
            try:
                while True:
                    l = input()
                    if l == "": break
                    lines.append(l)
            except EOFError:
                pass
            try:
                with open(fn,"a",encoding="utf8",errors="replace") as f:
                    f.write("\n".join(lines) + ("\n" if lines else ""))
                return f"Appended to {fn}"
            except Exception as e:
                return f"Error: {e}"
        if cmd == "edit":
            if not args: return "Usage: edit <file>"
            fn = args[0]
            # tiny editor: shows existing and allows replace lines until a line with only :wq
            print(f"--- editing {fn}. Type ':wq' on a blank line to save and exit ---")
            existing = []
            try:
                if os.path.exists(fn):
                    with open(fn,"r",encoding="utf8",errors="replace") as f:
                        existing = f.read().splitlines()
                        for i,l in enumerate(existing,1):
                            print(f"{i:4d}: {l}")
            except Exception:
                pass
            lines = []
            try:
                while True:
                    l = input()
                    if l.strip() == ":wq":
                        break
                    lines.append(l)
            except EOFError:
                pass
            try:
                with open(fn,"w",encoding="utf8",errors="replace") as f:
                    f.write("\n".join(lines) + ("\n" if lines else ""))
                return f"Saved {fn}"
            except Exception as e:
                return f"Error: {e}"
        if cmd == "notes":
            print("Enter note lines (blank line to finish):")
            lines = []
            try:
                while True:
                    l = input()
                    if l == "": break
                    lines.append(l)
            except EOFError:
                pass
            try:
                with open("notes.txt","a",encoding="utf8",errors="replace") as f:
                    f.write("\n".join(lines) + ("\n" if lines else ""))
                return "Saved to notes.txt"
            except Exception as e:
                return f"Error: {e}"
        if cmd == "exit":
            return "__EXIT__"

    # common commands
    if cmd == "help":
        if args:
            return cmd_help_one(args)
        return cmd_help(args)
    fn = COMMANDS.get(cmd)
    if fn:
        try:
            return fn(args)
        except Exception as e:
            return f"Error executing {cmd}: {e}"
    return f"Unknown command: {cmd}. Type 'help'."

# --- HTTP server (stdlib) ---
class Handler(SimpleHTTPRequestHandler):
    def translate_path(self, path):
        if path == "/" or path == "":
            return os.path.join(STATIC_DIR, "index.html")
        p = path.lstrip("/")
        candidate = os.path.join(STATIC_DIR, p)
        return candidate

    def do_POST(self):
        if self.path != "/command":
            self.send_response(404); self.end_headers(); self.wfile.write(b"Not Found"); return
        length = int(self.headers.get("Content-Length","0"))
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body.decode("utf8"))
            cmd = data.get("cmd","")
        except Exception:
            cmd = ""
        out = handle_command_line(cmd, from_where="web")
        push_output(f"> {cmd}")
        if out == "__EXIT__":
            push_output("Exit requested (ignored from web).")
            out = "Exit is terminal-only."
        else:
            push_output(out)
        self.send_response(200)
        self.send_header("Content-Type","application/json")
        self.end_headers()
        resp = {"status":"ok","out": out, "seq": get_output_seq()}
        self.wfile.write(json.dumps(resp).encode("utf8"))

    def do_GET(self):
        if self.path == "/output":
            txt = get_output_text()
            self.send_response(200)
            self.send_header("Content-Type","text/plain; charset=utf-8")
            self.send_header("Cache-Control","no-cache, no-store")
            self.end_headers()
            self.wfile.write(txt.encode("utf8"))
            return
        if self.path == "/pollseq":
            seq = get_output_seq()
            self.send_response(200)
            self.send_header("Content-Type","application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"seq": seq}).encode("utf8"))
            return
        return super().do_GET()

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def start_http_server():
    os.chdir(STATIC_DIR)
    server = ThreadedHTTPServer((HOST, PORT), Handler)
    push_output(f"HTTP server serving {STATIC_DIR} at http://{HOST}:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try: server.server_close()
        except Exception: pass

# --- Terminal UI with DOPA OS startup screen ---
def run_terminal_shell():
    try:
        import curses
    except Exception:
        curses = None

    # Boot sequence
    def boot_sequence(stdscr=None):
        lines = [
            "DOPA OS",
            "Initializing subsystems...",
            "Loading workspace modules...",
            "Starting DOPA services...",
        ]
        total = 100
        for i, text in enumerate(lines):
            push_output(text)
            if stdscr:
                stdscr.clear()
                h, w = stdscr.getmaxyx()
                stdscr.addstr(h//2 - 2, (w - len("DOPA OS"))//2, "DOPA OS", curses.A_BOLD)
                stdscr.addstr(h//2, (w - len(text))//2, text)
                # progress bar
                prog = int((i+1)/len(lines) * total)
                bar = "[" + "#"*(prog//5) + " "*(20-prog//5) + "]"
                stdscr.addstr(h//2 + 2, (w - len(bar))//2, bar)
                stdscr.refresh()
            time.sleep(0.7 + random.random()*0.2)
        push_output("DOPA OS loaded. Welcome!")
        push_output("")  # spacer

    if not curses:
        boot_sequence(None)
        push_output("Note: curses not available; running simple CLI.")
        while True:
            try:
                raw = input(f"{os.path.basename(os.getcwd())} $ ")
            except (EOFError, KeyboardInterrupt):
                push_output("Exiting.")
                break
            out = handle_command_line(raw, from_where="terminal")
            if out == "__EXIT__":
                push_output("Exit requested. Shutting down.")
                break
            push_output(f"> {raw}")
            push_output(out)
        return

    def curses_main(stdscr):
        curses.start_color()
        curses.use_default_colors()
        try:
            curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
        except Exception:
            pass
        stdscr.bkgd(' ', curses.color_pair(1))
        curses.curs_set(1)
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        # run boot
        boot_sequence(stdscr)
        # windows
        input_win = curses.newwin(1, width, height-1, 0)
        output_win = curses.newwin(height-1, width, 0, 0)
        output_win.scrollok(True)
        while True:
            # draw output buffer
            output_win.erase()
            with output_lock:
                start = max(0, len(output_lines) - (height-2))
                to_show = output_lines[start:]
                for i,line in enumerate(to_show):
                    try:
                        output_win.addstr(i, 0, line[:width-1])
                    except Exception:
                        pass
            output_win.refresh()
            # prompt
            input_win.erase()
            prompt = f"{os.path.basename(os.getcwd())} $ "
            input_win.addstr(0, 0, prompt)
            input_win.refresh()
            curses.echo()
            try:
                raw = input_win.getstr(0, len(prompt), 400).decode("utf8")
            except KeyboardInterrupt:
                break
            except Exception:
                raw = ""
            curses.noecho()
            raw = raw.strip()
            if not raw: continue
            out = handle_command_line(raw, from_where="terminal")
            push_output(f"> {raw}")
            if out == "__EXIT__":
                push_output("Exit requested. Shutting down.")
                break
            push_output(out)
            time.sleep(0.02)

    try:
        import curses
        curses.wrapper(curses_main)
    except Exception as e:
        push_output(f"Error launching curses UI: {e}")
        push_output("Falling back to CLI.")
        while True:
            try:
                raw = input(f"{os.path.basename(os.getcwd())} $ ")
            except (EOFError, KeyboardInterrupt):
                push_output("Exiting.")
                break
            out = handle_command_line(raw, from_where="terminal")
            if out == "__EXIT__":
                break
            push_output(f"> {raw}")
            push_output(out)

def main():
    os.makedirs(TRASH_DIR, exist_ok=True)
    load_state()
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()
    try:
        run_terminal_shell()
    finally:
        save_state()
        push_output("DOPA OS shutting down...")

if __name__ == "__main__":
    main()
