#!/usr/bin/env python3
"""
GELLO Simulation Control Panel
------------------------------
Modern dark-themed Tkinter control panel with three columns:

  LEFT   -> Robot arm connection    (Start / Stop / Restart)
  MIDDLE -> GELLO node + gripper    (Start / Stop)
  RIGHT  -> Dual GELLO connections  (Start / Stop)

Each column has its OWN log strip showing only the latest line for
that column's commands. All bash commands are exposed as plain
variables at the top of the file so you can plug in your real setup.

Run:
    python3 gello_gui.py
"""

import os
import signal
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import ttk

# =====================================================================
# 1) EDIT THESE COMMANDS TO MATCH YOUR ENVIRONMENT
# =====================================================================

# ---- LEFT: Robot arm connection -------------------------------------
CMD_ARM_START = "echo 'Starting robot arm connection...'"
CMD_ARM_STOP = "echo 'Stopping robot arm connection...'"
# Restart = stop -> wait -> start (see RESTART_DELAY below)

# ---- MIDDLE: GELLO node with gripper ---------------------------------
CMD_GELLO_START = "echo 'Starting GELLO node with gripper...'"
CMD_GELLO_STOP = "echo 'Stopping GELLO node with gripper...'"

# ---- RIGHT: Dual GELLO connections -----------------------------------
CMD_GELLO_DUAL_START = "echo 'Starting dual GELLO connections...'"
CMD_GELLO_DUAL_STOP = "echo 'Stopping dual GELLO connections...'"

# Delay (seconds) between stop and start when restarting the arm
RESTART_DELAY = 2.0

# =====================================================================
# 2) THEME
# =====================================================================

BG = "#0f1420"          # app background
CARD_BG = "#161c2c"     # column card background
CARD_BORDER = "#232b40"
TEXT_MAIN = "#e7ecf7"
TEXT_DIM = "#7b869c"

ACCENT_ARM = "#4f8dfd"      # blue
ACCENT_GELLO = "#33d69f"    # green
ACCENT_DUAL = "#b285f7"     # purple
DANGER = "#ff5d73"          # red for stop
WARN = "#ffb454"            # amber for restart

FONT_TITLE = ("Segoe UI Semibold", 15)
FONT_BTN = ("Segoe UI", 11)
FONT_LOG = ("Consolas", 9)
FONT_LOG_LABEL = ("Segoe UI Semibold", 9)

# =====================================================================
# 3) PROCESS MANAGER
# =====================================================================


class ProcessRunner:
    """Runs bash commands in the background and routes their output
    into a per-channel queue, so each column can show its own latest
    log line independently."""

    def __init__(self):
        self.processes = {}                 # name -> subprocess.Popen
        self.queues = {"arm": queue.Queue(), "gello": queue.Queue(), "dual": queue.Queue()}

    def _stream_output(self, channel, name, proc):
        q = self.queues[channel]
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                if line:
                    q.put(f"[{name}] {line}")
            proc.wait()
            q.put(f"[{name}] finished (exit code {proc.returncode})")
        except Exception as exc:  # noqa: BLE001
            q.put(f"[{name}] error reading output: {exc}")
        finally:
            self.processes.pop(name, None)

    def start(self, channel, name, bash_cmd):
        q = self.queues[channel]
        if name in self.processes and self.processes[name].poll() is None:
            q.put(f"[{name}] already running, ignoring start request")
            return
        q.put(f"[{name}] launching: {bash_cmd}")
        proc = subprocess.Popen(
            bash_cmd,
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            preexec_fn=os.setsid,
        )
        self.processes[name] = proc
        threading.Thread(target=self._stream_output, args=(channel, name, proc), daemon=True).start()

    def stop(self, channel, name, bash_cmd=None):
        q = self.queues[channel]
        proc = self.processes.get(name)
        if proc and proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                q.put(f"[{name}] stop signal sent")
            except Exception as exc:  # noqa: BLE001
                q.put(f"[{name}] failed to signal process: {exc}")
        if bash_cmd:
            self.start(channel, f"{name}-stopcmd", bash_cmd)

    def restart(self, channel, name, stop_cmd, start_cmd, delay=RESTART_DELAY):
        q = self.queues[channel]

        def _do_restart():
            q.put(f"[{name}] restarting (stop -> wait {delay}s -> start)...")
            self.stop(channel, name, stop_cmd)
            threading.Event().wait(delay)
            self.start(channel, name, start_cmd)

        threading.Thread(target=_do_restart, daemon=True).start()


# =====================================================================
# 4) UI HELPERS  (modern flat buttons with hover states)
# =====================================================================


class ModernButton(tk.Button):
    """A flat button with a hover-highlight, built on plain tk.Button
    so we have full color control (ttk themes are limited here)."""

    def __init__(self, master, text, base_color, command, icon="", **kwargs):
        self.base_color = base_color
        self.hover_color = self._shade(base_color, 1.15)
        super().__init__(
            master,
            text=f"  {icon}  {text}" if icon else text,
            command=command,
            font=FONT_BTN,
            fg="#0b0e14",
            bg=base_color,
            activebackground=self.hover_color,
            activeforeground="#0b0e14",
            bd=0,
            relief="flat",
            cursor="hand2",
            padx=14,
            pady=10,
            anchor="w",
            justify="left",
            **kwargs,
        )
        self.bind("<Enter>", lambda e: self.config(bg=self.hover_color))
        self.bind("<Leave>", lambda e: self.config(bg=self.base_color))

    @staticmethod
    def _shade(hex_color, factor):
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
        r, g, b = (min(255, int(c * factor)) for c in (r, g, b))
        return f"#{r:02x}{g:02x}{b:02x}"


def make_card(parent):
    outer = tk.Frame(parent, bg=CARD_BORDER, highlightthickness=0)
    inner = tk.Frame(outer, bg=CARD_BG)
    inner.pack(fill="both", expand=True, padx=1, pady=1)
    return outer, inner


def make_column_log(parent, accent):
    """A small per-column log strip pinned at the bottom of a card."""
    wrap = tk.Frame(parent, bg="#0c101b", highlightbackground=accent,
                     highlightthickness=1)
    tk.Label(wrap, text="LOG", font=FONT_LOG_LABEL, fg=accent, bg="#0c101b",
             anchor="w").pack(fill="x", padx=10, pady=(6, 0))
    var = tk.StringVar(value="idle...")
    lbl = tk.Label(wrap, textvariable=var, font=FONT_LOG, fg=TEXT_DIM,
                   bg="#0c101b", anchor="w", justify="left", wraplength=210)
    lbl.pack(fill="x", padx=10, pady=(2, 8))
    return wrap, var


# =====================================================================
# 5) MAIN APP
# =====================================================================


class GelloControlGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GELLO Simulation Control Panel")
        self.geometry("980x560")
        self.minsize(900, 520)
        self.configure(bg=BG)

        self.runner = ProcessRunner()
        self.log_vars = {}   # channel -> StringVar

        self._build_header()
        self._build_columns()

        self.after(150, self._poll_logs)

    # ------------------------------------------------------------------
    def _build_header(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=28, pady=(24, 6))

        tk.Label(header, text="GELLO Simulation Control", font=("Segoe UI Semibold", 20),
                 fg=TEXT_MAIN, bg=BG).pack(side="left")

        status = tk.Frame(header, bg=BG)
        status.pack(side="right")
        dot = tk.Canvas(status, width=10, height=10, bg=BG, highlightthickness=0)
        dot.create_oval(1, 1, 9, 9, fill="#33d69f", outline="")
        dot.pack(side="left", padx=(0, 6))
        tk.Label(status, text="Ready", font=("Segoe UI", 10), fg=TEXT_DIM, bg=BG).pack(side="left")

        tk.Frame(self, bg=CARD_BORDER, height=1).pack(fill="x", padx=28, pady=(10, 0))

    # ------------------------------------------------------------------
    def _build_columns(self):
        board = tk.Frame(self, bg=BG)
        board.pack(fill="both", expand=True, padx=28, pady=20)
        for i in range(3):
            board.grid_columnconfigure(i, weight=1, uniform="col")
        board.grid_rowconfigure(0, weight=1)

        self._build_arm_column(board, 0)
        self._build_gello_column(board, 1)
        self._build_dual_column(board, 2)

    # ---------------- LEFT: Robot arm ----------------------------------
    def _build_arm_column(self, board, col):
        outer, inner = make_card(board)
        outer.grid(row=0, column=col, sticky="nsew", padx=10)
        inner.pack_propagate(False)

        self._column_header(inner, "🦾", "Robot Arm", ACCENT_ARM)

        body = tk.Frame(inner, bg=CARD_BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        ModernButton(body, "Start Arm", ACCENT_ARM, icon="▶",
                     command=lambda: self.runner.start("arm", "arm", CMD_ARM_START)
                     ).pack(fill="x", pady=6)
        ModernButton(body, "Stop Arm", DANGER, icon="■",
                     command=lambda: self.runner.stop("arm", "arm", CMD_ARM_STOP)
                     ).pack(fill="x", pady=6)
        ModernButton(body, "Restart (after collision)", WARN, icon="⟳",
                     command=lambda: self.runner.restart("arm", "arm", CMD_ARM_STOP, CMD_ARM_START)
                     ).pack(fill="x", pady=6)

        log_wrap, var = make_column_log(inner, ACCENT_ARM)
        log_wrap.pack(fill="x", padx=16, pady=(4, 16))
        self.log_vars["arm"] = var

    # ---------------- MIDDLE: GELLO + gripper ---------------------------
    def _build_gello_column(self, board, col):
        outer, inner = make_card(board)
        outer.grid(row=0, column=col, sticky="nsew", padx=10)
        inner.pack_propagate(False)

        self._column_header(inner, "🕹️", "GELLO + Gripper", ACCENT_GELLO)

        body = tk.Frame(inner, bg=CARD_BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        ModernButton(body, "Start GELLO", ACCENT_GELLO, icon="▶",
                     command=lambda: self.runner.start("gello", "gello", CMD_GELLO_START)
                     ).pack(fill="x", pady=6)
        ModernButton(body, "Stop GELLO", DANGER, icon="■",
                     command=lambda: self.runner.stop("gello", "gello", CMD_GELLO_STOP)
                     ).pack(fill="x", pady=6)

        log_wrap, var = make_column_log(inner, ACCENT_GELLO)
        log_wrap.pack(fill="x", padx=16, pady=(4, 16))
        self.log_vars["gello"] = var

    # ---------------- RIGHT: Dual GELLO ----------------------------------
    def _build_dual_column(self, board, col):
        outer, inner = make_card(board)
        outer.grid(row=0, column=col, sticky="nsew", padx=10)
        inner.pack_propagate(False)

        self._column_header(inner, "🔗", "Dual GELLO", ACCENT_DUAL)

        body = tk.Frame(inner, bg=CARD_BG)
        body.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        ModernButton(body, "Start Dual GELLO", ACCENT_DUAL, icon="▶",
                     command=lambda: self.runner.start("dual", "dual", CMD_GELLO_DUAL_START)
                     ).pack(fill="x", pady=6)
        ModernButton(body, "Stop Dual GELLO", DANGER, icon="■",
                     command=lambda: self.runner.stop("dual", "dual", CMD_GELLO_DUAL_STOP)
                     ).pack(fill="x", pady=6)

        log_wrap, var = make_column_log(inner, ACCENT_DUAL)
        log_wrap.pack(fill="x", padx=16, pady=(4, 16))
        self.log_vars["dual"] = var

    # ------------------------------------------------------------------
    @staticmethod
    def _column_header(parent, icon, title, accent):
        head = tk.Frame(parent, bg=CARD_BG)
        head.pack(fill="x", padx=16, pady=(16, 10))
        tk.Label(head, text=icon, font=("Segoe UI Emoji", 16), bg=CARD_BG, fg=accent
                 ).pack(side="left", padx=(0, 8))
        tk.Label(head, text=title, font=FONT_TITLE, bg=CARD_BG, fg=TEXT_MAIN
                 ).pack(side="left")
        tk.Frame(parent, bg=accent, height=2).pack(fill="x", padx=16)

    # ------------------------------------------------------------------
    def _poll_logs(self):
        """Each column shows only its own latest log line."""
        for channel, q in self.runner.queues.items():
            last_line = None
            try:
                while True:
                    last_line = q.get_nowait()
            except queue.Empty:
                pass
            if last_line is not None:
                self.log_vars[channel].set(last_line)
        self.after(150, self._poll_logs)

    # ------------------------------------------------------------------
    def on_close(self):
        for name in list(self.runner.processes.keys()):
            for channel in self.runner.queues:
                self.runner.stop(channel, name)
        self.destroy()


if __name__ == "__main__":
    app = GelloControlGUI()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
