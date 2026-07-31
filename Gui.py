#!/usr/bin/env python3
"""
GELLO Simulation Control Panel
------------------------------
Modern dark-themed Tkinter control panel with three columns:

  LEFT   -> Single robot arm (Left / Right config)  - Start / Stop / Restart
  MIDDLE -> GELLO node with gripper                 - Start / Stop
  RIGHT  -> Bimanual (dual arm) GELLO connections    - Start / Stop

All commands run `source .venv/bin/activate && uv run ...` inside
PROJECT_DIR. Since these are long-running foreground processes with no
separate stop command, Stop simply terminates the running process, and
Restart stops then relaunches the same config.

Each column has its OWN log strip showing only the latest line for
that column's commands.

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
# 1) EDIT THESE TO MATCH YOUR ENVIRONMENT
# =====================================================================

# Absolute path to the repo that contains .venv/, experiments/, configs/
PROJECT_DIR = "/home/pi/gello_software"   # <-- EDIT THIS

# Command prefix to activate the uv virtual environment before every run
ACTIVATE = "source .venv/bin/activate && "

# ---- LEFT: Single arm control (left OR right config, user-selectable) ----
CMD_ARM_LEFT_START = ACTIVATE + "uv run experiments/launch_yaml.py --left-config-path configs/ur_left.yaml"
CMD_ARM_RIGHT_START = ACTIVATE + "uv run experiments/launch_yaml.py --left-config-path configs/ur_right.yaml"
# No separate stop command -> Stop just kills the running process (SIGTERM)
CMD_ARM_STOP = None

# ---- MIDDLE: GELLO node with gripper --------------------------------------
CMD_GELLO_GRIPPER_START = ACTIVATE + "uv run experiments/launch_yaml.py --left-config-path configs/ur_right_gripper.yaml"
CMD_GELLO_GRIPPER_STOP = None

# ---- RIGHT: Bimanual (dual) GELLO control ----------------------------------
CMD_DUAL_START = (
    ACTIVATE
    + "uv run experiments/launch_yaml.py "
      "--left-config-path configs/ur_left.yaml --right-config-path configs/ur_right.yaml"
)
CMD_DUAL_STOP = None

# Delay (seconds) between stop and start when restarting the arm
RESTART_DELAY = 2.0

# =====================================================================
# 2) THEME
# =====================================================================

BG = "#0f1420"
CARD_BG = "#161c2c"
CARD_BORDER = "#232b40"
TEXT_MAIN = "#e7ecf7"
TEXT_DIM = "#7b869c"

ACCENT_ARM = "#4f8dfd"
ACCENT_GELLO = "#33d69f"
ACCENT_DUAL = "#b285f7"
DANGER = "#ff5d73"
WARN = "#ffb454"

FONT_TITLE = ("Segoe UI Semibold", 13)
FONT_BTN = ("Segoe UI", 12, "bold")
FONT_LOG = ("Consolas", 13, "bold")
FONT_LOG_LABEL = ("Segoe UI Semibold", 10)
FONT_SELECTOR = ("Segoe UI", 10)

# =====================================================================
# 3) PROCESS MANAGER
# =====================================================================


class ProcessRunner:
    """Runs bash commands (in PROJECT_DIR) in the background and routes
    their output into a per-channel queue, so each column can show its
    own latest log line independently."""

    def __init__(self, cwd):
        self.cwd = cwd
        self.processes = {}
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
            cwd=self.cwd,
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
        elif not bash_cmd:
            q.put(f"[{name}] nothing running")
        if bash_cmd:
            self.start(channel, f"{name}-stopcmd", bash_cmd)

    def restart(self, channel, name, start_cmd, stop_cmd=None, delay=RESTART_DELAY):
        q = self.queues[channel]

        def _do_restart():
            q.put(f"[{name}] restarting (stop -> wait {delay}s -> start)...")
            self.stop(channel, name, stop_cmd)
            threading.Event().wait(delay)
            self.start(channel, name, start_cmd)

        threading.Thread(target=_do_restart, daemon=True).start()


# =====================================================================
# 4) UI HELPERS
# =====================================================================


class ModernButton(tk.Button):
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
            padx=10,
            pady=7,
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


def make_column_log(parent, accent, wraplength=230):
    wrap = tk.Frame(parent, bg="#0c101b", highlightbackground=accent, highlightthickness=2)
    tk.Label(wrap, text="LOG", font=FONT_LOG_LABEL, fg=accent, bg="#0c101b",
             anchor="w").pack(fill="x", padx=10, pady=(8, 2))
    var = tk.StringVar(value="idle...")
    lbl = tk.Label(wrap, textvariable=var, font=FONT_LOG, fg="#ffffff",
                   bg="#0c101b", anchor="w", justify="left", wraplength=wraplength,
                   height=3)
    lbl.pack(fill="x", padx=10, pady=(2, 10))
    return wrap, var


# =====================================================================
# 5) MAIN APP
# =====================================================================


class GelloControlGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("GELLO Simulation Control Panel")
        self.configure(bg=BG)

        # Auto-fit to the actual screen (7" Pi touchscreens are ~800x480)
        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        self.geometry(f"{screen_w}x{screen_h}+0+0")
        self.minsize(760, 420)
        # Wider text wrap on bigger screens, tighter on small ones
        self.col_wrap = max(160, int(screen_w / 3) - 80)

        self.runner = ProcessRunner(cwd=PROJECT_DIR)
        self.log_vars = {}
        self.arm_side = tk.StringVar(value="left")  # which single-arm config to use

        self._build_header()
        self._build_columns()

        self.after(150, self._poll_logs)

    # ------------------------------------------------------------------
    def _build_header(self):
        header = tk.Frame(self, bg=BG)
        header.pack(fill="x", padx=14, pady=(8, 4))

        tk.Label(header, text="GELLO Simulation Control", font=("Segoe UI Semibold", 15),
                 fg=TEXT_MAIN, bg=BG).pack(side="left")

        status = tk.Frame(header, bg=BG)
        status.pack(side="right")
        dot = tk.Canvas(status, width=10, height=10, bg=BG, highlightthickness=0)
        dot.create_oval(1, 1, 9, 9, fill="#33d69f", outline="")
        dot.pack(side="left", padx=(0, 6))
        tk.Label(status, text=f"Project: {PROJECT_DIR}", font=("Segoe UI", 8),
                 fg=TEXT_DIM, bg=BG).pack(side="left")

        tk.Frame(self, bg=CARD_BORDER, height=1).pack(fill="x", padx=14, pady=(6, 0))

    # ------------------------------------------------------------------
    def _build_columns(self):
        board = tk.Frame(self, bg=BG)
        board.pack(fill="both", expand=True, padx=10, pady=8)
        for i in range(3):
            board.grid_columnconfigure(i, weight=1, uniform="col")
        board.grid_rowconfigure(0, weight=1)

        self._build_arm_column(board, 0)
        self._build_gello_column(board, 1)
        self._build_dual_column(board, 2)

    # ---------------- LEFT: Single robot arm ----------------------------
    def _build_arm_column(self, board, col):
        outer, inner = make_card(board)
        outer.grid(row=0, column=col, sticky="nsew", padx=5)
        inner.pack_propagate(False)

        self._column_header(inner, "🦾", "Robot Arm", ACCENT_ARM)

        body = tk.Frame(inner, bg=CARD_BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        # Left / Right config selector
        sel = tk.Frame(body, bg=CARD_BG)
        sel.pack(fill="x", pady=(0, 10))
        tk.Label(sel, text="Config:", font=FONT_SELECTOR, bg=CARD_BG, fg=TEXT_DIM).pack(side="left")
        for label, value in (("Left", "left"), ("Right", "right")):
            tk.Radiobutton(
                sel, text=label, variable=self.arm_side, value=value,
                font=FONT_SELECTOR, bg=CARD_BG, fg=TEXT_MAIN,
                selectcolor=CARD_BG, activebackground=CARD_BG,
                highlightthickness=0, bd=0,
            ).pack(side="left", padx=6)

        def start_arm():
            cmd = CMD_ARM_LEFT_START if self.arm_side.get() == "left" else CMD_ARM_RIGHT_START
            self.runner.start("arm", "arm", cmd)

        def restart_arm():
            cmd = CMD_ARM_LEFT_START if self.arm_side.get() == "left" else CMD_ARM_RIGHT_START
            self.runner.restart("arm", "arm", cmd, CMD_ARM_STOP)

        ModernButton(body, "Start Arm", ACCENT_ARM, icon="▶", command=start_arm).pack(fill="x", pady=4)
        ModernButton(body, "Stop Arm", DANGER, icon="■",
                     command=lambda: self.runner.stop("arm", "arm", CMD_ARM_STOP)).pack(fill="x", pady=4)
        ModernButton(body, "Restart (after collision)", WARN, icon="⟳",
                     command=restart_arm).pack(fill="x", pady=4)

        log_wrap, var = make_column_log(inner, ACCENT_ARM, self.col_wrap)
        log_wrap.pack(fill="x", padx=10, pady=(4, 10))
        self.log_vars["arm"] = var

    # ---------------- MIDDLE: GELLO + gripper ---------------------------
    def _build_gello_column(self, board, col):
        outer, inner = make_card(board)
        outer.grid(row=0, column=col, sticky="nsew", padx=5)
        inner.pack_propagate(False)

        self._column_header(inner, "🕹️", "GELLO + Gripper", ACCENT_GELLO)

        body = tk.Frame(inner, bg=CARD_BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        ModernButton(body, "Start GELLO", ACCENT_GELLO, icon="▶",
                     command=lambda: self.runner.start("gello", "gello", CMD_GELLO_GRIPPER_START)
                     ).pack(fill="x", pady=4)
        ModernButton(body, "Stop GELLO", DANGER, icon="■",
                     command=lambda: self.runner.stop("gello", "gello", CMD_GELLO_GRIPPER_STOP)
                     ).pack(fill="x", pady=4)

        log_wrap, var = make_column_log(inner, ACCENT_GELLO, self.col_wrap)
        log_wrap.pack(fill="x", padx=10, pady=(4, 10))
        self.log_vars["gello"] = var

    # ---------------- RIGHT: Dual / bimanual GELLO -----------------------
    def _build_dual_column(self, board, col):
        outer, inner = make_card(board)
        outer.grid(row=0, column=col, sticky="nsew", padx=5)
        inner.pack_propagate(False)

        self._column_header(inner, "🔗", "Dual GELLO", ACCENT_DUAL)

        body = tk.Frame(inner, bg=CARD_BG)
        body.pack(fill="both", expand=True, padx=10, pady=(0, 8))

        ModernButton(body, "Start Dual GELLO", ACCENT_DUAL, icon="▶",
                     command=lambda: self.runner.start("dual", "dual", CMD_DUAL_START)
                     ).pack(fill="x", pady=4)
        ModernButton(body, "Stop Dual GELLO", DANGER, icon="■",
                     command=lambda: self.runner.stop("dual", "dual", CMD_DUAL_STOP)
                     ).pack(fill="x", pady=4)

        log_wrap, var = make_column_log(inner, ACCENT_DUAL, self.col_wrap)
        log_wrap.pack(fill="x", padx=10, pady=(4, 10))
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
