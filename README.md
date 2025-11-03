DOPA OS (no external deps) - quick run

1) Add files to repo:
   - codespaces_os.py
   - static/index.html
   - static/main.css
   - static/main.js

2) In Codespaces:
   - Open a new terminal pane to simulate a separate window.
   - Run:
       python codespaces_os.py

   It will boot DOPA OS (text load screen) and start an HTTP server on 0.0.0.0:5000.

3) Open Ports panel -> find port 5000 -> Open in Browser.
   - The browser tab is your black/white fullscreen terminal-like window.

4) Use commands in either the terminal pane (full interactive) or the web UI (single-line).
   - Example: help, ls, cat README_short.txt, calc 2+2, search TODO, edits via edit <file> in terminal.
   - Use "updatesim <pkg>" to simulate installs, "trash <file>" and "restore <name>" to safely move/restore files.

Security:
- Use workspace/private port visibility. The server runs with access to repository files.

Notes:
- I kept interactive features (edit, write, append, arcade full play) designed for terminal curses UI for better UX.
- The web UI mirrors output and accepts commands via POST /command (no websocket libs).

Enjoy DOPA OS!
