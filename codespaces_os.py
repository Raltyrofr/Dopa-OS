#!/usr/bin/env python3
"""
CodespacesOS (no external deps) - enhanced with web loading screen and improved web UI.

- Single-file Python server + terminal OS.
- Uses only Python stdlib. No Flask.
- Starts an HTTP server (0.0.0.0:5000) serving ./static and endpoints:
    POST /command  -> accepts JSON {"cmd":"..."} from web UI
    GET  /output   -> returns last N lines of output as plain text
    GET  /pollseq  -> returns a numeric sequence token representing latest output index
    GET  /status   -> returns {"status":"ready","seq":...} to indicate server readiness
- Also runs a curses-based full-screen terminal UI (black bg, white text) locally.
- Commands entered in either the terminal UI or the web UI are run by the same handler.
- Web UI includes a loading screen, connection status, command history, auto-scroll and nicer formatting.

Run: python codespaces_os.py
In Codespaces: open a new terminal pane to simulate a separate window, run it there, open port 5000 in Ports panel -> Open in Browser.
"""
from __future__ import annotations
import os
import sys
import threading
import json
import time
import queue
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from datetime import datetime

# Config
HOST = "0.0.0.0"
PORT = 5000
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
OUTPUT_MAX_LINES = 2000

# Server readiness event for the web UI loading screen / terminal loading indicator
server_ready = threading.Event()

# Shared output buffer and queue for incoming commands (thread-safe)
output_lines: list[str] = []
output_lock = threading.Lock()
cmd_queue = queue.Queue()

def _timestamp():
    return datetime.now().strftime("%H:%M:%S")

def push_output(text):
    """Append a line or multiline text to the shared output buffer, prefixing a timestamp."""
    with output_lock:
        for line in str(text).splitlines():
            # avoid double-empty timestamped lines; keep empty lines but give them a timestamp too
            output_lines.append(f"[{_timestamp()}] {line}")
        # trim
        if len(output_lines) > OUTPUT_MAX_LINES:
            output_lines[:] = output_lines[-OUTPUT_MAX_LINES:]

def get_output_text():
    with output_lock:
        return "\n".join(output_lines)

def get_output_seq():
    with output_lock:
        return len(output_lines)

# --- Safe command implementations (no shell calls) ---
def cmd_help(args):
    return (
        "Commands:\n"
        "  ls [dir]\n"
        "  cat <file>\n"
        "  write <file>    (will prompt in terminal; via web use 'writefile <file> <content>')\n"
        "  append <file>   (via web use 'appendfile <file> <content>')\n"
        "  touch <file>\n"
        "  rm <file>\n"
        "  cd <dir>\n"
        "  pwd\n"
        "  calc <expr>\n"
        "  notes (terminal entry) or notesadd <text> (web)\n"
        "  arcade tictactoe|hangman|number\n"
        "  chat <text>\n"
        "  clear\n"
        "  help\n"
        "  exit\n"
    )

def cmd_ls(args):
    path = args[0] if args else "."
    try:
        items = sorted(os.listdir(path))
        return "\n".join(items) if items else "(empty)"
    except Exception as e:
        return f"Error: {e}"

def cmd_cat(args):
    if not args:
        return "Usage: cat <file>"
    try:
        with open(args[0], "r", encoding="utf8", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"Error: {e}"

def cmd_write_terminal(fn):
    # invoked only from terminal interactive mode
    print(f"Enter content to write to {fn}. Finish with a blank line on its own.")
    lines = []
    try:
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
    except EOFError:
        pass
    try:
        with open(fn, "w", encoding="utf8", errors="replace") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        return f"Wrote {fn}"
    except Exception as e:
        return f"Error: {e}"

def cmd_write_file(args):
    # web-friendly write: writefile <file> <content...>
    if len(args) < 2:
        return "Usage: writefile <file> <content>"
    fn = args[0]
    content = " ".join(args[1:])
    try:
        with open(fn, "w", encoding="utf8", errors="replace") as f:
            f.write(content)
        return f"Wrote {fn}"
    except Exception as e:
        return f"Error: {e}"

def cmd_append_file(args):
    if len(args) < 2:
        return "Usage: appendfile <file> <content>"
    fn = args[0]
    content = " ".join(args[1:])
    try:
        with open(fn, "a", encoding="utf8", errors="replace") as f:
            f.write(content)
        return f"Appended to {fn}"
    except Exception as e:
        return f"Error: {e}"

def cmd_touch(args):
    if not args:
        return "Usage: touch <file>"
    try:
        open(args[0], "a").close()
        return f"Touched {args[0]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_rm(args):
    if not args:
        return "Usage: rm <file>"
    try:
        os.remove(args[0])
        return f"Removed {args[0]}"
    except Exception as e:
        return f"Error: {e}"

def cmd_cd(args):
    if not args:
        return "Usage: cd <dir>"
    try:
        os.chdir(args[0])
        return f"cwd: {os.getcwd()}"
    except Exception as e:
        return f"Error: {e}"

def cmd_pwd(args):
    return os.getcwd()

def cmd_calc(args):
    if not args:
        return "Usage: calc <expression>"
    expr = " ".join(args)
    allowed = "0123456789+-*/(). %"
    if any(ch not in allowed for ch in expr):
        return "Error: invalid characters in expression"
    try:
        result = eval(expr, {"__builtins__": None}, {})
        return str(result)
    except Exception as e:
        return f"Error: {e}"

def cmd_notes_terminal():
    print("Enter note lines. Finish with a blank line on its own.")
    lines = []
    try:
        while True:
            line = input()
            if line == "":
                break
            lines.append(line)
    except EOFError:
        pass
    try:
        with open("notes.txt", "a", encoding="utf8", errors="replace") as f:
            f.write("\n".join(lines) + ("\n" if lines else ""))
        return "Saved to notes.txt"
    except Exception as e:
        return f"Error: {e}"

def cmd_notes_add(args):
    if not args:
        return "Usage: notesadd <text>"
    text = " ".join(args)
    try:
        with open("notes.txt", "a", encoding="utf8", errors="replace") as f:
            f.write(text + "\n")
        return "Appended to notes.txt"
    except Exception as e:
        return f"Error: {e}"

def cmd_chat(args):
    if not args:
        return "Usage: chat <text>"
    text = " ".join(args).lower()
    if "hello" in text or "hi" in text:
        return "Hello! I'm CodespacesOS. How can I help?"
    if "how are" in text:
        return "I'm a program — always ready."
    if "help" in text:
        return "Try commands: ls, cat, calc, arcade tictactoe"
    return "Interesting. Tell me more."

# Minimal arcade handlers that return strings (suitable for web)
def cmd_arcade(args):
    if not args:
        return "Usage: arcade tictactoe|hangman|number"
    g = args[0].lower()
    if g == "number":
        return "Number guess started. Server supports guess <n> to play. (Single-process demo)"
    if g == "tictactoe":
        return "TicTacToe started. Play via terminal for full experience."
    if g == "hangman":
        return "Hangman started. Play via terminal for full experience."
    return "Unknown game."

# Dispatch table
COMMANDS = {
    "help": cmd_help,
    "ls": cmd_ls,
    "cat": cmd_cat,
    "writefile": cmd_write_file,
    "appendfile": cmd_append_file,
    "touch": cmd_touch,
    "rm": cmd_rm,
    "cd": cmd_cd,
    "pwd": cmd_pwd,
    "calc": cmd_calc,
    "notesadd": cmd_notes_add,
    "chat": cmd_chat,
    "arcade": cmd_arcade,
}

def handle_command_line(raw, from_where="terminal"):
    """
    Execute a command string and return textual output.
    from_where indicates source (terminal|web) for context.
    """
    raw = raw.strip()
    if not raw:
        return ""
    parts = raw.split()
    cmd = parts[0].lower()
    args = parts[1:]
    # terminal-only interactive commands
    if from_where == "terminal":
        if cmd == "write":
            return cmd_write_terminal(args[0]) if args else "Usage: write <file>"
        if cmd == "append":
            # append via interactive terminal
            if not args:
                return "Usage: append <file>"
            print(f"Enter content to append to {args[0]}. Finish with blank line.")
            lines = []
            try:
                while True:
                    line = input()
                    if line == "":
                        break
                    lines.append(line)
            except EOFError:
                pass
            try:
                with open(args[0], "a", encoding="utf8", errors="replace") as f:
                    f.write("\n".join(lines) + ("\n" if lines else ""))
                return f"Appended to {args[0]}"
            except Exception as e:
                return f"Error: {e}"
        if cmd == "notes":
            return cmd_notes_terminal()
        if cmd == "exit":
            return "__EXIT__"

    # shared commands and web-friendly names
    fn = COMMANDS.get(cmd)
    if fn:
        try:
            return fn(args)
        except Exception as e:
            return f"Error in command {cmd}: {e}"
    # unknown
    return f"Unknown command: {cmd}. Type 'help'."

# --- HTTP server using stdlib ---
class Handler(SimpleHTTPRequestHandler):
    # serve files from STATIC_DIR
    def translate_path(self, path):
        # serve static files from STATIC_DIR; default to index.html for "/"
        if path == "/" or path == "":
            return os.path.join(STATIC_DIR, "index.html")
        # strip leading slash
        p = path.lstrip("/")
        candidate = os.path.join(STATIC_DIR, p)
        return candidate

    def _set_json_headers(self, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.end_headers()

    def do_POST(self):
        # only endpoint we accept: /command
        if self.path != "/command":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not Found")
            return
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        try:
            data = json.loads(body.decode("utf8"))
            cmd = data.get("cmd", "")
        except Exception:
            cmd = ""
        # execute command synchronously in handler thread
        out = handle_command_line(cmd, from_where="web")
        # echo command and output into shared buffer (so terminal and other web clients see it)
        push_output(f"> {cmd}")
        push_output(out)
        # respond
        self._set_json_headers(200)
        resp = {"status":"ok", "out": out, "seq": get_output_seq(), "ts": _timestamp()}
        self.wfile.write(json.dumps(resp).encode("utf8"))

    def do_GET(self):
        if self.path == "/output":
            # return plain text of output buffer
            txt = get_output_text()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(txt.encode("utf8"))
            return
        if self.path == "/pollseq":
            seq = get_output_seq()
            self._set_json_headers(200)
            self.wfile.write(json.dumps({"seq": seq}).encode("utf8"))
            return
        if self.path == "/status":
            seq = get_output_seq()
            # simple status endpoint that web UI can poll during loading
            self._set_json_headers(200)
            self.wfile.write(json.dumps({"status": "ready", "seq": seq}).encode("utf8"))
            return
        # else serve static files via SimpleHTTPRequestHandler
        return super().do_GET()

class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

def start_http_server():
    # ensure static directory exists before chdir
    if not os.path.isdir(STATIC_DIR):
        push_output("Static directory not found; web UI will be disabled.")
        # do not attempt to serve; still set ready so terminal proceeds
        server_ready.set()
        return
    os.chdir(STATIC_DIR)  # ensure relative assets served correctly
    server = ThreadedHTTPServer((HOST, PORT), Handler)
    push_output(f"HTTP server serving {STATIC_DIR} at http://{HOST}:{PORT}")
    # mark server as ready so UI/loading screens can proceed
    server_ready.set()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        try:
            server.server_close()
        except Exception:
            pass

# --- Terminal UI (curses full-screen) ---
def run_terminal_shell():
    # If curses is available and terminal supports it, run curses full-screen prompt; otherwise fallback to simple CLI
    try:
        import curses
    except Exception:
        curses = None

    welcome = "Welcome to CodespacesOS (terminal). Type 'help'."
    push_output(welcome)
    # Simple CLI if curses not present
    if not curses:
        while True:
            try:
                raw = input(f"{os.path.basename(os.getcwd())} $ ")
            except (EOFError, KeyboardInterrupt):
                push_output("Exiting terminal shell.")
                break
            out = handle_command_line(raw, from_where="terminal")
            if out == "__EXIT__":
                push_output("Exiting per user request.")
                break
            push_output(f"> {raw}")
            push_output(out)
        return

    # curses full-screen with a mini loading animation while HTTP server starts
    def curses_main(stdscr):
        # minimal black background, white text
        curses.start_color()
        curses.use_default_colors()
        try:
            curses.init_pair(1, curses.COLOR_WHITE, curses.COLOR_BLACK)
            curses.init_pair(2, curses.COLOR_GREEN, curses.COLOR_BLACK)
            curses.init_pair(3, curses.COLOR_RED, curses.COLOR_BLACK)
            curses.init_pair(4, curses.COLOR_CYAN, curses.COLOR_BLACK)
        except Exception:
            pass
        stdscr.bkgd(' ', curses.color_pair(1))
        curses.curs_set(1)
        stdscr.clear()
        height, width = stdscr.getmaxyx()
        input_win = curses.newwin(1, width, height-1, 0)
        output_win = curses.newwin(height-1, width, 0, 0)
        output_win.scrollok(True)

        # show a loading overlay until server_ready is set (or timeout)
        overlay_shown = False
        start_wait = time.time()
        while not server_ready.is_set() and time.time() - start_wait < 5:
            overlay_shown = True
            stdscr.erase()
            msg = "Starting CodespacesOS..."
            spinner = "|/-\\"
            for i in range(8):
                if server_ready.is_set():
                    break
                stdscr.addstr(height//2, max(0, (width - len(msg))//2), msg, curses.color_pair(4))
                stdscr.addstr(height//2 + 1, width//2, spinner[i % len(spinner)])
                stdscr.refresh()
                time.sleep(0.12)
        if overlay_shown:
            stdscr.erase()
            stdscr.refresh()

        # initial display
        push_output(welcome)

        # simple scrollback pointer for the terminal view
        scroll_offset = 0

        while True:
            # draw output buffer
            output_win.erase()
            with output_lock:
                avail_lines = len(output_lines)
                max_display = height - 2
                start = max(0, avail_lines - max_display - scroll_offset)
                to_show = output_lines[start:start + max_display]
                for i, line in enumerate(to_show):
                    try:
                        # colorization heuristics
                        if line.strip().startswith("> "):
                            output_win.addstr(i, 0, line[:width-1], curses.color_pair(2))
                        elif "error" in line.lower() or "error:" in line.lower():
                            output_win.addstr(i, 0, line[:width-1], curses.color_pair(3))
                        else:
                            output_win.addstr(i, 0, line[:width-1], curses.color_pair(1))
                    except Exception:
                        pass
            output_win.refresh()
            # prompt
            input_win.erase()
            prompt = f"{os.path.basename(os.getcwd())} $ "
            input_win.addstr(0, 0, prompt)
            input_win.refresh()
            # read user input (blocking)
            curses.echo()
            try:
                raw = input_win.getstr(0, len(prompt), 400).decode("utf8")
            except KeyboardInterrupt:
                break
            except Exception:
                raw = ""
            curses.noecho()
            raw = raw.strip()
            if not raw:
                continue
            out = handle_command_line(raw, from_where="terminal")
            push_output(f"> {raw}")
            if out == "__EXIT__":
                push_output("Exiting per user request.")
                break
            push_output(out)
            # small sleep to allow UI update
            time.sleep(0.03)

    try:
        import curses
        curses.wrapper(curses_main)
    except Exception as e:
        push_output(f"Error running curses UI: {e}")
        push_output("Falling back to simple CLI.")
        # fallback to simple CLI
        while True:
            try:
                raw = input(f"{os.path.basename(os.getcwd())} $ ")
            except (EOFError, KeyboardInterrupt):
                push_output("Exiting terminal shell.")
                break
            out = handle_command_line(raw, from_where="terminal")
            if out == "__EXIT__":
                push_output("Exiting per user request.")
                break
            push_output(f"> {raw}")
            push_output(out)

# --- Entrypoint ---
def main():
    # ensure static dir exists (the script will still run without the web UI)
    if not os.path.isdir(STATIC_DIR):
        print("Warning: static directory not found. Web UI disabled. Create a 'static' folder next to this script to enable the web interface.")
    # Start HTTP server thread
    http_thread = threading.Thread(target=start_http_server, daemon=True)
    http_thread.start()

    # run terminal shell (blocking)
    try:
        run_terminal_shell()
    except KeyboardInterrupt:
        push_output("Interrupted by user.")
    except Exception as e:
        push_output(f"Terminal error: {e}\n{e.__traceback__}")
    finally:
        push_output("Server shutting down (press Ctrl-C if necessary).")
        time.sleep(0.2)

if __name__ == "__main__":
    main()
