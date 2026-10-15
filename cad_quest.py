"""CAD Quest - a game that turns SolidWorks (or any 3D CAD) practice into timed challenges.

Flow:
  1. Choose a level and press START.
  2. Gemini generates a part to model (with all dimensions).
  3. Full-screen countdown 3-2-1-GO, then the timer starts.
  4. Model the part in your CAD program, then press FINISH and upload
     screenshots (PNG/JPG) and optionally an STL export.
  5. Gemini checks your model and you get points.

Settings and history live in %APPDATA%\\CADQuest (outside the project folder).
"""

import base64
import io
import json
import os
import queue
import re
import struct
import threading
import time
import tkinter as tk
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from PIL import Image

# ---------------------------------------------------------------- settings --

API_URL = "https://generativelanguage.googleapis.com/v1beta"
DEFAULT_MODEL = "gemini-2.5-flash"
APP_DIR = Path(os.environ.get("APPDATA", Path.home())) / "CADQuest"
CONFIG_FILE = APP_DIR / "config.json"
HISTORY_FILE = APP_DIR / "history.json"

MAX_IMAGES = 6
MAX_IMAGE_SIDE = 1568  # px - bigger screenshots only waste upload time

# minutes = time limit, multiplier = points multiplier, scope = what Gemini should design
LEVELS = {
    "Beginner": {
        "minutes": 10,
        "multiplier": 1.0,
        "scope": "one simple part with 2-4 features: extrudes, cut-extrudes, a fillet or chamfer.",
    },
    "Intermediate": {
        "minutes": 20,
        "multiplier": 1.5,
        "scope": "one part with 5-8 features: revolve, hole pattern, shell, ribs, fillets, chamfers.",
    },
    "Advanced": {
        "minutes": 35,
        "multiplier": 2.0,
        "scope": "one complex part with 8-12 features, including at least one sweep or loft, draft and patterns.",
    },
    "Expert": {
        "minutes": 50,
        "multiplier": 3.0,
        "scope": "one demanding part with 12+ features: lofts, sweeps along 3D paths, variable fillets, "
        "patterns driven by other features, tight dimensional relations.",
    },
}

# (minimum XP, rank name)
RANKS = [
    (0, "Sketch Rookie"),
    (300, "Extrude Apprentice"),
    (1000, "Feature Engineer"),
    (2500, "Loft Specialist"),
    (5000, "CAD Legend"),
]

# Colors for the dark theme
BG = "#1e1f26"
PANEL = "#2a2c36"
FG = "#e8e8f0"
ACCENT = "#4fc3f7"
GOOD = "#66bb6a"
BAD = "#ef5350"


# ------------------------------------------------- structured Gemini output --
# These schemas describe the JSON shape we want back. Gemini is forced to
# answer with JSON that matches them, so we never parse free text.

TASK_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "description": {"type": "string"},
        "requirements": {"type": "array", "items": {"type": "string"}},
        "bounding_box_mm": {"type": "array", "items": {"type": "number"}},  # [X, Y, Z]
    },
    "required": ["title", "description", "requirements", "bounding_box_mm"],
}

REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "accuracy": {"type": "integer"},  # 0-100
        "passed": {"type": "boolean"},
        "checks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "requirement": {"type": "string"},
                    "status": {"type": "string", "enum": ["ok", "wrong", "not_visible"]},
                    "comment": {"type": "string"},
                },
                "required": ["requirement", "status", "comment"],
            },
        },
        "feedback": {"type": "string"},
    },
    "required": ["accuracy", "passed", "checks", "feedback"],
}

GENERATOR_SYSTEM = """You design modelling exercises for a CAD practice game (SolidWorks or similar).
The player sees ONLY your text - there is no drawing - so every dimension needed to build the part
must be stated explicitly in millimetres, with clear references (which face, which edge, distance from
which origin or edge). Prefer plausible real-world objects (brackets, flanges, knobs, housings, handles...).
Requirements must each be one checkable statement, in the order the player would naturally model them.
bounding_box_mm is the overall size of the finished part along X, Y, Z."""

REVIEWER_SYSTEM = """You grade a player's CAD model against the exercise they were given.
Evidence: screenshots of their model and, if provided, exact measurements computed from an STL export
(STL units are as exported, normally millimetres). Evaluate every requirement:
- "ok" when the evidence supports it,
- "wrong" when the evidence contradicts it,
- "not_visible" when it cannot be checked from the evidence (give partial credit, mention which view would help).
Use the STL bounding box and volume to verify overall dimensions when available.
accuracy is 0-100. passed is true when the part is recognisably the requested part and accuracy >= 60.
If the images show something unrelated to the task, accuracy must be near 0.
feedback: 2-4 sentences, friendly but honest, with one concrete tip to model faster or cleaner."""


# --------------------------------------------------------------- utilities --


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, data):
    APP_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def format_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def rank_for(xp):
    name = RANKS[0][1]
    for min_xp, rank_name in RANKS:
        if xp >= min_xp:
            name = rank_name
    return name


def image_to_part(path):
    """Open an image, shrink it if needed, and return a Gemini 'inlineData' part."""
    with Image.open(path) as img:
        img = img.convert("RGB")
        img.thumbnail((MAX_IMAGE_SIDE, MAX_IMAGE_SIDE))  # keeps aspect ratio, only shrinks
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
    data = base64.standard_b64encode(buffer.getvalue()).decode("ascii")
    return {"inlineData": {"mimeType": "image/png", "data": data}}


def read_stl(path):
    """Measure an STL file (binary or ASCII). Returns a short text report."""
    raw = Path(path).read_bytes()
    triangles = []

    # Binary STL: 80-byte header, uint32 triangle count, then 50 bytes per triangle
    count = struct.unpack_from("<I", raw, 80)[0] if len(raw) >= 84 else -1
    if len(raw) == 84 + 50 * count:
        # "<12fH" = little-endian, 12 floats (normal + 3 vertices) + 2-byte attribute
        for values in struct.iter_unpack("<12fH", raw[84:]):
            v = values[3:12]
            triangles.append((v[0:3], v[3:6], v[6:9]))
    else:
        # ASCII STL: lines like "vertex 1.0 2.0 3.0"
        numbers = re.findall(rb"vertex\s+(\S+)\s+(\S+)\s+(\S+)", raw)
        points = [tuple(float(n) for n in xyz) for xyz in numbers]
        triangles = [tuple(points[i:i + 3]) for i in range(0, len(points) - 2, 3)]

    if not triangles:
        raise ValueError(f"No triangles found in {Path(path).name}")

    xs = [p[0] for t in triangles for p in t]
    ys = [p[1] for t in triangles for p in t]
    zs = [p[2] for t in triangles for p in t]

    volume = 0.0
    area = 0.0
    for a, b, c in triangles:
        # cross product b x c -> signed tetrahedron volume with the origin (divergence theorem)
        cx = b[1] * c[2] - b[2] * c[1]
        cy = b[2] * c[0] - b[0] * c[2]
        cz = b[0] * c[1] - b[1] * c[0]
        volume += (a[0] * cx + a[1] * cy + a[2] * cz) / 6.0
        # triangle area = |(b-a) x (c-a)| / 2
        u = (b[0] - a[0], b[1] - a[1], b[2] - a[2])
        w = (c[0] - a[0], c[1] - a[1], c[2] - a[2])
        n = (u[1] * w[2] - u[2] * w[1], u[2] * w[0] - u[0] * w[2], u[0] * w[1] - u[1] * w[0])
        area += (n[0] ** 2 + n[1] ** 2 + n[2] ** 2) ** 0.5 / 2.0

    return (
        f"STL measurements of {Path(path).name}:\n"
        f"- triangles: {len(triangles)}\n"
        f"- bounding box X x Y x Z: {max(xs) - min(xs):.2f} x {max(ys) - min(ys):.2f} x {max(zs) - min(zs):.2f}\n"
        f"- volume: {abs(volume):.1f} (cubic units)\n"
        f"- surface area: {area:.1f} (square units)"
    )


class ApiError(Exception):
    """An error with a message that is already friendly enough to show to the player."""


def gemini_request(api_key, path, body=None):
    """Send one HTTP request to the Gemini API and return the decoded JSON answer.

    body=None -> GET request, otherwise POST with a JSON body.
    """
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        f"{API_URL}/{path}",
        data=data,
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=180) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        # Gemini explains the problem in a JSON body: {"error": {"message": ...}}
        try:
            message = json.loads(error.read())["error"]["message"]
        except (ValueError, KeyError):
            message = error.reason
        if error.code in (400, 403) and "API key" in message:
            raise ApiError("Your Gemini API key was rejected. Check it in the main window.")
        if error.code == 429:
            raise ApiError("Free quota used up for this model (requests per minute or per day).\n"
                           "Wait a bit, or pick another model in the main window.")
        if error.code == 404:
            raise ApiError("This model doesn't exist for your key. Click 'Refresh' and choose another.")
        raise ApiError(f"Gemini API error {error.code}: {message}")
    except urllib.error.URLError:
        raise ApiError("Cannot reach the Gemini API. Check your internet connection.")


def ask_gemini(api_key, model, system, parts, schema):
    """Ask Gemini for JSON matching `schema` and return it as a Python dict."""
    body = {
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {"responseMimeType": "application/json", "responseSchema": schema},
    }
    answer = gemini_request(api_key, f"models/{model}:generateContent", body)

    candidates = answer.get("candidates", [])
    if not candidates:
        reason = answer.get("promptFeedback", {}).get("blockReason", "unknown")
        raise ApiError(f"Gemini blocked the request (reason: {reason}). Try again.")
    parts = candidates[0].get("content", {}).get("parts", [])
    # Skip "thought" parts (some models return their reasoning separately)
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        reason = candidates[0].get("finishReason", "unknown")
        raise ApiError(f"Gemini returned no usable answer (finish reason: {reason}). Try again.")


def list_models(api_key):
    """Return names of Gemini models this key can use for generateContent."""
    answer = gemini_request(api_key, "models?pageSize=1000")
    names = []
    for model in answer.get("models", []):
        name = model["name"].removeprefix("models/")
        usable = "generateContent" in model.get("supportedGenerationMethods", [])
        special = any(word in name for word in ("tts", "embedding", "image", "audio", "live"))
        if usable and name.startswith("gemini") and not special:
            names.append(name)
    return sorted(names)


# ---------------------------------------------------------------------- app --


class CadQuestApp:
    def __init__(self, root):
        self.root = root
        self.config = load_json(CONFIG_FILE, {})
        self.history = load_json(HISTORY_FILE, [])

        # State of the current game
        self.task = None  # dict matching TASK_SCHEMA
        self.level = None
        self.start_time = 0.0
        self.elapsed = 0.0
        self.timer_running = False
        self.game_window = None

        root.title("CAD Quest")
        root.geometry("820x640")
        root.configure(bg=BG)
        self.setup_styles()
        self.build_main_window()
        self.refresh_stats()
        if self.api_key():
            self.refresh_models()

    # ---------- look ----------

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=PANEL)
        style.configure("TLabel", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("Title.TLabel", font=("Segoe UI", 22, "bold"), foreground=ACCENT)
        style.configure("Stat.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("TLabelframe", background=BG, foreground=ACCENT)
        style.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        style.configure("TRadiobutton", background=BG, foreground=FG, font=("Segoe UI", 10))
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.configure("TButton", background=PANEL, foreground=FG, padding=6)
        style.map("TButton", background=[("active", "#3a3d4a")])
        style.configure("Start.TButton", font=("Segoe UI", 16, "bold"), foreground=BG, background=ACCENT, padding=12)
        style.map("Start.TButton", background=[("active", "#81d4fa"), ("disabled", "#555")])
        style.configure("Treeview", background=PANEL, foreground=FG, fieldbackground=PANEL, rowheight=24)
        style.configure("Treeview.Heading", background="#33364a", foreground=FG)

    # ---------- main window ----------

    def build_main_window(self):
        ttk.Label(self.root, text="CAD QUEST", style="Title.TLabel").pack(pady=(14, 4))
        ttk.Label(self.root, text="Model fast. Model right. Level up.").pack()

        top = tk.Frame(self.root, bg=BG)
        top.pack(fill="x", padx=16, pady=10)

        # Level choice
        level_box = ttk.LabelFrame(top, text="Level", padding=10)
        level_box.pack(side="left", fill="y")
        self.level_var = tk.StringVar(value=self.config.get("level", "Beginner"))
        for name, info in LEVELS.items():
            text = f"{name}  ({info['minutes']} min, x{info['multiplier']})"
            ttk.Radiobutton(level_box, text=text, value=name, variable=self.level_var).pack(anchor="w", pady=2)

        # API key + model
        key_box = ttk.LabelFrame(top, text="Gemini API key (free at aistudio.google.com)", padding=10)
        key_box.pack(side="left", fill="both", expand=True, padx=(12, 0))
        self.key_var = tk.StringVar(value=self.config.get("api_key", ""))
        self.key_entry = ttk.Entry(key_box, textvariable=self.key_var, show="•", width=45)
        self.key_entry.pack(fill="x")
        row = tk.Frame(key_box, bg=BG)
        row.pack(fill="x", pady=6)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(row, text="Show", variable=self.show_key, command=self.toggle_key).pack(side="left")
        ttk.Button(row, text="Save key", command=self.save_key).pack(side="right")

        model_row = tk.Frame(key_box, bg=BG)
        model_row.pack(fill="x", pady=(0, 6))
        ttk.Label(model_row, text="Model:").pack(side="left")
        self.model_var = tk.StringVar(value=self.config.get("model", DEFAULT_MODEL))
        self.model_box = ttk.Combobox(model_row, textvariable=self.model_var, width=32)
        self.model_box.pack(side="left", padx=6)
        ttk.Button(model_row, text="Refresh", command=self.refresh_models).pack(side="right")

        ttk.Label(key_box, text="Key saved as plain text in %APPDATA%\\CADQuest\\config.json.\n"
                                "Free tier: Google may use your inputs to improve its products.",
                  foreground="#999").pack(anchor="w")

        # Stats
        stats_box = ttk.LabelFrame(self.root, text="Your stats", padding=10)
        stats_box.pack(fill="both", expand=True, padx=16)
        self.summary_label = ttk.Label(stats_box, style="Stat.TLabel")
        self.summary_label.pack(anchor="w", pady=(0, 6))

        columns = ("date", "level", "task", "time", "accuracy", "points")
        self.table = ttk.Treeview(stats_box, columns=columns, show="headings", height=8)
        widths = {"date": 120, "level": 90, "task": 260, "time": 90, "accuracy": 80, "points": 70}
        for col in columns:
            self.table.heading(col, text=col.capitalize())
            self.table.column(col, width=widths[col], anchor="w" if col == "task" else "center")
        self.table.pack(fill="both", expand=True)

        # Start
        self.start_button = ttk.Button(self.root, text="START", style="Start.TButton", command=self.start_game)
        self.start_button.pack(pady=12)
        self.status_label = ttk.Label(self.root, text="", foreground="#aaa")
        self.status_label.pack(pady=(0, 8))

    def toggle_key(self):
        self.key_entry.configure(show="" if self.show_key.get() else "•")

    def save_key(self):
        self.config["api_key"] = self.key_var.get().strip()
        save_json(CONFIG_FILE, self.config)
        self.status_label.configure(text="API key saved.")
        self.refresh_models()

    def api_key(self):
        # Key from the GUI first, environment variable as a backup
        return self.key_var.get().strip() or os.environ.get("GEMINI_API_KEY", "")

    def refresh_models(self):
        key = self.api_key()
        if not key:
            messagebox.showwarning("CAD Quest", "Enter your Gemini API key first.")
            return
        self.status_label.configure(text="Loading model list...")

        def on_models(names):
            self.model_box.configure(values=names)
            if names and self.model_var.get() not in names:
                # Prefer a "flash" model: fast and has the most generous free quota
                flash = [n for n in names if "flash" in n and "lite" not in n]
                self.model_var.set(flash[-1] if flash else names[-1])
            self.status_label.configure(text=f"{len(names)} models available.")

        self.run_in_background(lambda: list_models(key), on_models)

    def refresh_stats(self):
        for row in self.table.get_children():
            self.table.delete(row)
        for game in reversed(self.history[-30:]):  # newest first
            accuracy = "gave up" if game["gave_up"] else f"{game['accuracy']}%"
            self.table.insert("", "end", values=(
                game["date"], game["level"], game["task"],
                format_time(game["seconds"]), accuracy, game["points"],
            ))

        xp = sum(g["points"] for g in self.history)
        finished = [g for g in self.history if not g["gave_up"]]
        avg = sum(g["accuracy"] for g in finished) / len(finished) if finished else 0
        best = max((g["points"] for g in self.history), default=0)
        self.summary_label.configure(
            text=f"Rank: {rank_for(xp)}   |   XP: {xp}   |   Games: {len(self.history)}   |   "
                 f"Avg accuracy: {avg:.0f}%   |   Best game: {best} pts"
        )

    # ---------- background work ----------

    def run_in_background(self, work, on_success):
        """Run `work()` in a thread so the window doesn't freeze, then call `on_success(result)`.

        tkinter is not thread-safe, so the thread only puts the result into a queue,
        and the GUI thread checks that queue every 100 ms with root.after (like millis() polling).
        """
        results = queue.Queue()

        def worker():
            try:
                results.put((True, work()))
            except Exception as error:  # report every failure back to the GUI
                results.put((False, error))

        threading.Thread(target=worker, daemon=True).start()

        def poll():
            try:
                ok, value = results.get_nowait()
            except queue.Empty:
                self.root.after(100, poll)
                return
            if ok:
                on_success(value)
            else:
                self.on_error(value)

        poll()

    def on_error(self, error):
        self.start_button.configure(state="normal")
        self.status_label.configure(text="")
        if self.game_window:  # review failed - let the player press FINISH again
            self.game_window.title(f"CAD Quest - {self.task['title']}")
            self.timer_label.configure(text=format_time(self.elapsed), font=("Consolas", 48, "bold"))
        messagebox.showerror("CAD Quest", str(error), parent=self.game_window or self.root)

    # ---------- 1. generate a task ----------

    def start_game(self):
        if not self.api_key():
            messagebox.showwarning("CAD Quest", "Enter your Gemini API key first.")
            return
        self.level = self.level_var.get()
        self.config["level"] = self.level
        self.config["model"] = self.model_var.get().strip()
        save_json(CONFIG_FILE, self.config)

        self.start_button.configure(state="disabled")
        self.status_label.configure(text="Gemini is designing your challenge...")

        recent = [g["task"] for g in self.history[-15:]]
        prompt = (
            f"Level: {self.level}. Scope: {LEVELS[self.level]['scope']}\n"
            f"Time limit for the player: {LEVELS[self.level]['minutes']} minutes.\n"
            f"Do not repeat these recent tasks: {recent or 'none'}."
        )
        key, model = self.api_key(), self.config["model"]
        self.run_in_background(
            lambda: ask_gemini(key, model, GENERATOR_SYSTEM, [{"text": prompt}], TASK_SCHEMA),
            self.on_task_ready,
        )

    # ---------- 2. full-screen countdown ----------

    def on_task_ready(self, task):
        self.task = task
        self.status_label.configure(text="")
        self.root.withdraw()  # hide main window during the game

        overlay = tk.Toplevel(self.root)
        overlay.attributes("-fullscreen", True)
        overlay.attributes("-topmost", True)
        overlay.configure(bg="black")
        label = tk.Label(overlay, bg="black", fg=ACCENT, font=("Segoe UI", 200, "bold"))
        label.pack(expand=True)

        steps = ["3", "2", "1", "GO!"]

        def show_step(i):
            if i < len(steps):
                label.configure(text=steps[i], fg=GOOD if steps[i] == "GO!" else ACCENT)
                overlay.after(1000, show_step, i + 1)  # non-blocking, like millis()
            else:
                overlay.destroy()
                self.open_game_window()

        show_step(0)

    # ---------- 3. game window with timer ----------

    def open_game_window(self):
        win = tk.Toplevel(self.root)
        self.game_window = win
        win.title(f"CAD Quest - {self.task['title']}")
        win.configure(bg=BG)
        win.geometry("460x620+20+20")
        win.attributes("-topmost", True)  # stays above SolidWorks
        win.protocol("WM_DELETE_WINDOW", self.give_up)

        limit = LEVELS[self.level]["minutes"] * 60
        self.timer_label = tk.Label(win, text="00:00", bg=BG, fg=ACCENT, font=("Consolas", 48, "bold"))
        self.timer_label.pack(pady=(10, 0))
        tk.Label(win, text=f"limit {format_time(limit)}  •  {self.level}", bg=BG, fg="#999").pack()

        text = tk.Text(win, wrap="word", bg=PANEL, fg=FG, font=("Segoe UI", 10), relief="flat", padx=10, pady=10)
        text.pack(fill="both", expand=True, padx=12, pady=10)
        text.insert("end", self.task["title"] + "\n\n", "title")
        text.insert("end", self.task["description"] + "\n\n")
        for number, requirement in enumerate(self.task["requirements"], start=1):
            text.insert("end", f"{number}. {requirement}\n")
        box = " x ".join(f"{v:g}" for v in self.task["bounding_box_mm"])
        text.insert("end", f"\nOverall size (X x Y x Z): {box} mm\n")
        text.insert("end", "\nWhen done: take screenshots (isometric + front/top/right views help) "
                           "and optionally File > Save As > STL.")
        text.tag_configure("title", font=("Segoe UI", 14, "bold"), foreground=ACCENT)
        text.configure(state="disabled")  # read-only

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Button(buttons, text="Give up", command=self.give_up).pack(side="left")
        ttk.Button(buttons, text="FINISH & upload", command=self.finish).pack(side="right")

        self.start_time = time.monotonic()  # monotonic = not affected by clock changes
        self.timer_running = True
        self.update_timer()

    def update_timer(self):
        if not self.timer_running:
            return
        self.elapsed = time.monotonic() - self.start_time
        limit = LEVELS[self.level]["minutes"] * 60
        color = BAD if self.elapsed > limit else ACCENT
        self.timer_label.configure(text=format_time(self.elapsed), fg=color)
        self.root.after(200, self.update_timer)

    def give_up(self):
        if not messagebox.askyesno("CAD Quest", "Give up this challenge? It counts as 0 points.",
                                   parent=self.game_window):
            return
        self.timer_running = False
        self.save_game(accuracy=0, points=0, gave_up=True)
        self.end_game()

    # ---------- 4. upload and review ----------

    def finish(self):
        self.timer_running = False  # time stops when you press FINISH, upload doesn't count
        paths = filedialog.askopenfilenames(
            parent=self.game_window,
            title="Select screenshots (PNG/JPG) and optionally an STL file",
            filetypes=[("Model files", "*.png *.jpg *.jpeg *.stl"), ("All files", "*.*")],
        )
        if not paths:
            self.timer_running = True  # cancelled -> keep counting
            self.start_time = time.monotonic() - self.elapsed
            self.update_timer()
            return

        images = [p for p in paths if p.lower().endswith((".png", ".jpg", ".jpeg"))][:MAX_IMAGES]
        stls = [p for p in paths if p.lower().endswith(".stl")]
        if not images and not stls:
            messagebox.showwarning("CAD Quest", "Select at least one image or STL file.", parent=self.game_window)
            self.finish()
            return

        self.game_window.title("CAD Quest - Gemini is checking your model...")
        self.timer_label.configure(text="checking...", font=("Consolas", 28, "bold"))

        task = self.task
        key, model = self.api_key(), self.config["model"]

        def work():
            parts = [image_to_part(p) for p in images]
            report = "\n\n".join(read_stl(p) for p in stls) or "No STL provided."
            parts.append({"text": (
                f"The exercise:\n{json.dumps(task, indent=2)}\n\n{report}\n\n"
                "Grade the player's model."
            )})
            return ask_gemini(key, model, REVIEWER_SYSTEM, parts, REVIEW_SCHEMA)

        self.run_in_background(work, self.on_review_ready)

    def on_review_ready(self, review):
        limit = LEVELS[self.level]["minutes"] * 60
        accuracy = max(0, min(100, int(review["accuracy"])))
        passed = review["passed"]
        # Time bonus only for a passed model finished within the limit: up to +25
        bonus = round(25 * (1 - self.elapsed / limit)) if passed and self.elapsed < limit else 0
        points = round((accuracy + bonus) * LEVELS[self.level]["multiplier"])

        old_rank = rank_for(sum(g["points"] for g in self.history))
        self.save_game(accuracy=accuracy, points=points, gave_up=False)
        new_rank = rank_for(sum(g["points"] for g in self.history))

        self.end_game()
        self.show_result(review, accuracy, bonus, points, rank_up=new_rank if new_rank != old_rank else None)

    def show_result(self, review, accuracy, bonus, points, rank_up):
        win = tk.Toplevel(self.root)
        win.title("CAD Quest - Result")
        win.configure(bg=BG)
        win.geometry("560x560")

        verdict, color = ("PASSED", GOOD) if review["passed"] else ("NOT PASSED", BAD)
        tk.Label(win, text=verdict, bg=BG, fg=color, font=("Segoe UI", 28, "bold")).pack(pady=(14, 0))
        tk.Label(win, text=f"+{points} XP", bg=BG, fg=ACCENT, font=("Segoe UI", 20, "bold")).pack()
        tk.Label(win, bg=BG, fg=FG, text=(
            f"Accuracy {accuracy}%  •  Time {format_time(self.elapsed)}  •  "
            f"Time bonus +{bonus}  •  x{LEVELS[self.level]['multiplier']}"
        )).pack(pady=4)
        if rank_up:
            tk.Label(win, text=f"RANK UP: {rank_up}!", bg=BG, fg="#ffd54f",
                     font=("Segoe UI", 14, "bold")).pack()

        text = tk.Text(win, wrap="word", bg=PANEL, fg=FG, font=("Segoe UI", 10), relief="flat", padx=10, pady=10)
        text.pack(fill="both", expand=True, padx=12, pady=10)
        icons = {"ok": ("✔", "ok"), "wrong": ("✘", "wrong"), "not_visible": ("?", "unknown")}
        for check in review["checks"]:
            icon, tag = icons.get(check["status"], ("?", "unknown"))
            text.insert("end", f"{icon} ", tag)
            text.insert("end", f"{check['requirement']}\n")
            text.insert("end", f"   {check['comment']}\n\n", "comment")
        text.insert("end", "Feedback: ", "ok")
        text.insert("end", review["feedback"])
        text.tag_configure("ok", foreground=GOOD, font=("Segoe UI", 10, "bold"))
        text.tag_configure("wrong", foreground=BAD, font=("Segoe UI", 10, "bold"))
        text.tag_configure("unknown", foreground="#ffd54f", font=("Segoe UI", 10, "bold"))
        text.tag_configure("comment", foreground="#aaa")
        text.configure(state="disabled")

        ttk.Button(win, text="Close", command=win.destroy).pack(pady=(0, 12))

    # ---------- end of game ----------

    def save_game(self, accuracy, points, gave_up):
        self.history.append({
            "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "level": self.level,
            "task": self.task["title"],
            "seconds": round(self.elapsed),
            "accuracy": accuracy,
            "points": points,
            "gave_up": gave_up,
        })
        save_json(HISTORY_FILE, self.history)

    def end_game(self):
        if self.game_window:
            self.game_window.destroy()
            self.game_window = None
        self.root.deiconify()  # show main window again
        self.start_button.configure(state="normal")
        self.refresh_stats()


if __name__ == "__main__":
    root = tk.Tk()
    CadQuestApp(root)
    root.mainloop()
