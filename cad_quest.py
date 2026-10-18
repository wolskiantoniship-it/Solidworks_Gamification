"""CAD Quest - a game that turns SolidWorks (or any 3D CAD) practice into timed challenges.

Flow:
  1. Choose a level and press START.
  2. Gemini generates a part to model (with all dimensions).
  3. Full-screen countdown 3-2-1-GO, then the timer starts.
  4. Model the part in your CAD program, then press FINISH and upload
     screenshots (PNG/JPG) and optionally an STL export.
  5. Gemini checks your model and you get points.

The interface (and Gemini's tasks) can be in English or Polish.
Settings and history live in %APPDATA%\\CADQuest (outside the project folder).
"""

import base64
import ctypes
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

# minutes = time limit, multiplier = points multiplier, scope = what Gemini should design,
# color = button color when selected. The English key is what history.json stores.
LEVELS = {
    "Beginner": {
        "minutes": 10,
        "multiplier": 1.0,
        "color": "#15803d",
        "scope": "one simple part with 2-4 features: extrudes, cut-extrudes, a fillet or chamfer.",
    },
    "Intermediate": {
        "minutes": 20,
        "multiplier": 1.5,
        "color": "#1d4ed8",
        "scope": "one part with 5-8 features: revolve, hole pattern, shell, ribs, fillets, chamfers.",
    },
    "Advanced": {
        "minutes": 35,
        "multiplier": 2.0,
        "color": "#c2410c",
        "scope": "one complex part with 8-12 features, including at least one sweep or loft, draft and patterns.",
    },
    "Expert": {
        "minutes": 50,
        "multiplier": 3.0,
        "color": "#b91c1c",
        "scope": "one demanding part with 12+ features: lofts, sweeps along 3D paths, variable fillets, "
        "patterns driven by other features, tight dimensional relations.",
    },
}

# (minimum XP, English name, Polish name)
RANKS = [
    (0, "Sketch Rookie", "Nowicjusz Szkicu"),
    (300, "Extrude Apprentice", "Czeladnik Wyciągnięć"),
    (1000, "Feature Engineer", "Inżynier Operacji"),
    (2500, "Loft Specialist", "Mistrz Profili"),
    (5000, "CAD Legend", "Legenda CAD"),
]

# Colors (dark theme)
BG = "#12141a"        # window background
CARD = "#1b1e26"      # card background
CARD2 = "#262a35"     # inputs, buttons, table header
BORDER = "#2f3441"
TEXT = "#e6e8ee"
MUTED = "#8b90a0"
ACCENT = "#38bdf8"    # sky blue
ACCENT_HOVER = "#7dd3fc"
PURPLE = "#a78bfa"
GOOD = "#4ade80"
BAD = "#f87171"
GOLD = "#fbbf24"

FONT = "Segoe UI"
NUM_FONT = "Bahnschrift"  # built into Windows 10/11, nice for numbers and titles

# ------------------------------------------------------------ translations --
# Every text shown in the GUI lives here. self.t("key") picks the current language.

TEXTS = {
    "en": {
        "subtitle": "Model fast. Model right. Level up.",
        "level": "Level",
        "level_Beginner": "Beginner",
        "level_Intermediate": "Intermediate",
        "level_Advanced": "Advanced",
        "level_Expert": "Expert",
        "level_info": "{minutes} min  ·  ×{mult}",
        "api_box": "Gemini API key",
        "api_hint": "free at aistudio.google.com",
        "show": "Show",
        "save_key": "Save key",
        "model": "Model",
        "refresh": "Refresh",
        "key_note": "Stored as plain text in %APPDATA%\\CADQuest.\n"
                    "Free tier: Google may use your inputs to improve its products.",
        "recent": "Recent games",
        "tile_rank": "Rank",
        "tile_xp": "Total XP",
        "tile_games": "Games",
        "tile_avg": "Avg accuracy",
        "tile_best": "Best game",
        "next_rank": "{left} XP to {rank}",
        "max_rank": "Highest rank reached!",
        "col_date": "Date",
        "col_level": "Level",
        "col_task": "Task",
        "col_time": "Time",
        "col_accuracy": "Accuracy",
        "col_points": "Points",
        "gave_up_cell": "gave up",
        "no_games": "No games yet. Press START!",
        "start": "START",
        "key_saved": "API key saved.",
        "models_loading": "Loading model list...",
        "models_found": "{n} models available.",
        "need_key": "Enter your Gemini API key first.",
        "designing": "Gemini is designing your challenge...",
        "go": "GO!",
        "limit": "limit {limit}",
        "overall": "Overall size (X × Y × Z): {box} mm",
        "done_hint": "Click a requirement to tick it off.\nWhen done: take screenshots (isometric + front/top/right) "
                     "and optionally File > Save As > STL.",
        "give_up": "Give up",
        "finish": "FINISH & upload",
        "give_up_q": "Give up this challenge? It counts as 0 points.",
        "file_title": "Select screenshots (PNG/JPG) and optionally an STL file",
        "file_types": "Model files",
        "all_files": "All files",
        "need_file": "Select at least one image or STL file.",
        "checking_title": "Gemini is checking your model...",
        "checking": "checking...",
        "result": "Result",
        "passed": "PASSED",
        "failed": "NOT PASSED",
        "result_line": "Accuracy {acc}%   ·   Time {time}   ·   Time bonus +{bonus}   ·   ×{mult}",
        "rank_up": "RANK UP: {rank}!",
        "feedback": "Feedback: ",
        "close": "Close",
        "err_key": "Your Gemini API key was rejected. Check it in the main window.",
        "err_quota": "Free quota used up for this model (requests per minute or per day).\n"
                     "Wait a bit, or pick another model in the main window.",
        "err_model": "This model doesn't exist for your key. Click 'Refresh' and choose another.",
        "err_api": "Gemini API error {code}: {message}",
        "err_net": "Cannot reach the Gemini API. Check your internet connection.",
        "err_blocked": "Gemini blocked the request (reason: {reason}). Try again.",
        "err_empty": "Gemini returned no usable answer (finish reason: {reason}). Try again.",
        "err_stl": "No triangles found in {name}.",
    },
    "pl": {
        "subtitle": "Modeluj szybko. Modeluj dokładnie. Awansuj.",
        "level": "Poziom",
        "level_Beginner": "Początkujący",
        "level_Intermediate": "Średniozaawansowany",
        "level_Advanced": "Zaawansowany",
        "level_Expert": "Ekspert",
        "level_info": "{minutes} min  ·  ×{mult}",
        "api_box": "Klucz API Gemini",
        "api_hint": "darmowy na aistudio.google.com",
        "show": "Pokaż",
        "save_key": "Zapisz klucz",
        "model": "Model",
        "refresh": "Odśwież",
        "key_note": "Zapisany jako zwykły tekst w %APPDATA%\\CADQuest.\n"
                    "Darmowy plan: Google może używać przesłanych danych do ulepszania usług.",
        "recent": "Ostatnie gry",
        "tile_rank": "Ranga",
        "tile_xp": "Suma XP",
        "tile_games": "Gry",
        "tile_avg": "Śr. dokładność",
        "tile_best": "Najlepsza gra",
        "next_rank": "{left} XP do rangi: {rank}",
        "max_rank": "Osiągnięto najwyższą rangę!",
        "col_date": "Data",
        "col_level": "Poziom",
        "col_task": "Zadanie",
        "col_time": "Czas",
        "col_accuracy": "Dokładność",
        "col_points": "Punkty",
        "gave_up_cell": "poddano",
        "no_games": "Brak gier. Naciśnij START!",
        "start": "START",
        "key_saved": "Klucz API zapisany.",
        "models_loading": "Wczytywanie listy modeli...",
        "models_found": "Dostępne modele: {n}.",
        "need_key": "Najpierw wpisz klucz API Gemini.",
        "designing": "Gemini projektuje Twoje zadanie...",
        "go": "START!",
        "limit": "limit {limit}",
        "overall": "Wymiary całkowite (X × Y × Z): {box} mm",
        "done_hint": "Kliknij wymaganie, aby je odhaczyć.\nNa koniec: zrób zrzuty ekranu (izometria + przód/góra/prawo) "
                     "i opcjonalnie Plik > Zapisz jako > STL.",
        "give_up": "Poddaj się",
        "finish": "KONIEC i prześlij",
        "give_up_q": "Poddać się? Gra liczy się jako 0 punktów.",
        "file_title": "Wybierz zrzuty ekranu (PNG/JPG) i opcjonalnie plik STL",
        "file_types": "Pliki modelu",
        "all_files": "Wszystkie pliki",
        "need_file": "Wybierz co najmniej jeden obraz lub plik STL.",
        "checking_title": "Gemini sprawdza Twój model...",
        "checking": "sprawdzam...",
        "result": "Wynik",
        "passed": "ZALICZONE",
        "failed": "NIEZALICZONE",
        "result_line": "Dokładność {acc}%   ·   Czas {time}   ·   Bonus za czas +{bonus}   ·   ×{mult}",
        "rank_up": "AWANS: {rank}!",
        "feedback": "Uwagi: ",
        "close": "Zamknij",
        "err_key": "Klucz API Gemini został odrzucony. Sprawdź go w oknie głównym.",
        "err_quota": "Wyczerpany darmowy limit dla tego modelu (na minutę lub na dzień).\n"
                     "Odczekaj chwilę albo wybierz inny model w oknie głównym.",
        "err_model": "Ten model nie jest dostępny dla Twojego klucza. Kliknij 'Odśwież' i wybierz inny.",
        "err_api": "Błąd API Gemini {code}: {message}",
        "err_net": "Brak połączenia z API Gemini. Sprawdź internet.",
        "err_blocked": "Gemini zablokowało zapytanie (powód: {reason}). Spróbuj ponownie.",
        "err_empty": "Gemini nie zwróciło poprawnej odpowiedzi (powód: {reason}). Spróbuj ponownie.",
        "err_stl": "Nie znaleziono trójkątów w pliku {name}.",
    },
}

# Extra instruction added to Gemini's prompt, so tasks and feedback come in the chosen language
GEMINI_LANGUAGE = {
    "en": "Write all text fields in English.",
    "pl": "Write all text fields in Polish, using the Polish SolidWorks terminology "
          "(e.g. 'Wyciągnięcie dodania', 'Wycięcie wyciągnięciem', 'Zaokrąglenie', 'Szyk kołowy').",
}


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
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def format_time(seconds):
    seconds = int(seconds)
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def rank_index(xp):
    """Index in RANKS of the highest rank reached with this much XP."""
    index = 0
    for i, (min_xp, _, _) in enumerate(RANKS):
        if xp >= min_xp:
            index = i
    return index


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
        raise ApiError("err_stl", name=Path(path).name)

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
    """An error the player should see. `key` points into TEXTS, so the GUI can translate it."""

    def __init__(self, key, **params):
        super().__init__(key)
        self.key = key
        self.params = params


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
            raise ApiError("err_key")
        if error.code == 429:
            raise ApiError("err_quota")
        if error.code == 404:
            raise ApiError("err_model")
        raise ApiError("err_api", code=error.code, message=message)
    except urllib.error.URLError:
        raise ApiError("err_net")


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
        raise ApiError("err_blocked", reason=answer.get("promptFeedback", {}).get("blockReason", "?"))
    parts = candidates[0].get("content", {}).get("parts", [])
    # Skip "thought" parts (some models return their reasoning separately)
    text = "".join(p.get("text", "") for p in parts if not p.get("thought"))
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ApiError("err_empty", reason=candidates[0].get("finishReason", "?"))


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


# ------------------------------------------------------------- GUI helpers --


def make_button(parent, text, command, kind="normal", **options):
    """A flat button with a hover effect. kind: 'primary', 'normal' or 'danger'."""
    colors = {
        "primary": (ACCENT, "#0b1220", ACCENT_HOVER),
        "normal": (CARD2, TEXT, BORDER),
        "danger": (CARD2, BAD, BORDER),
    }
    bg, fg, hover = colors[kind]
    button = tk.Button(
        parent, text=text, command=command, bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
        relief="flat", bd=0, cursor="hand2", padx=14, pady=6, font=(FONT, 10, "bold"), **options,
    )
    # <Enter>/<Leave> = mouse moves over / away from the widget
    button.bind("<Enter>", lambda e: button.configure(bg=hover) if button["state"] == "normal" else None)
    button.bind("<Leave>", lambda e: button.configure(bg=bg) if button["state"] == "normal" else None)
    return button


def make_card(parent, title=None):
    """A dark rounded-looking panel with an optional small uppercase title. Returns the inner frame."""
    outer = tk.Frame(parent, bg=CARD, highlightthickness=1, highlightbackground=BORDER)
    inner = tk.Frame(outer, bg=CARD)
    inner.pack(fill="both", expand=True, padx=14, pady=12)
    if title:
        tk.Label(inner, text=title.upper(), bg=CARD, fg=MUTED, font=(FONT, 8, "bold")).pack(anchor="w", pady=(0, 6))
    inner.outer = outer  # so the caller can pack/grid the outer frame
    return inner


# ---------------------------------------------------------------------- app --


class CadQuestApp:
    def __init__(self, root):
        self.root = root
        self.config = load_json(CONFIG_FILE, {})
        self.history = load_json(HISTORY_FILE, [])
        self.lang = self.config.get("language", "en")
        self.model_names = []

        # State of the current game
        self.task = None  # dict matching TASK_SCHEMA
        self.level = None
        self.start_time = 0.0
        self.elapsed = 0.0
        self.timer_running = False
        self.game_window = None

        # Window sizes are in pixels, so scale them on high-DPI screens (e.g. 150% in Windows settings)
        self.scale = root.winfo_fpixels("1i") / 96

        root.title("CAD Quest")
        root.geometry(self.size(900, 760) + "+40+10")
        root.minsize(*map(int, self.size(760, 640).split("x")))
        root.configure(bg=BG)
        self.setup_styles()
        self.build_main_window()
        self.refresh_stats()
        if self.api_key():
            self.refresh_models()

    # ---------- small helpers ----------

    def t(self, key, **params):
        """Translated text for `key` in the current language, with {placeholders} filled in."""
        text = TEXTS[self.lang].get(key, TEXTS["en"][key])
        return text.format(**params)

    def rank_name(self, index):
        return RANKS[index][2 if self.lang == "pl" else 1]

    def size(self, width, height):
        """Window size string like '900x760', scaled for DPI and limited to the screen size."""
        width = min(int(width * self.scale), self.root.winfo_screenwidth() - 40)
        height = min(int(height * self.scale), self.root.winfo_screenheight() - 140)  # room for title bar + taskbar
        return f"{width}x{height}"

    # ---------- look ----------

    def setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=CARD, foreground=TEXT, fieldbackground=CARD,
                        rowheight=int(28 * self.scale), borderwidth=0, font=(FONT, 10))
        style.configure("Treeview.Heading", background=CARD2, foreground=MUTED, relief="flat",
                        font=(FONT, 9, "bold"))
        style.map("Treeview", background=[("selected", BORDER)], foreground=[("selected", TEXT)])
        style.map("Treeview.Heading", background=[("active", BORDER)])
        style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])  # removes the white border
        style.configure("TCombobox", fieldbackground=CARD2, background=CARD2, foreground=TEXT,
                        arrowcolor=TEXT, bordercolor=BORDER, lightcolor=CARD2, darkcolor=CARD2)
        style.map("TCombobox", fieldbackground=[("readonly", CARD2)], foreground=[("readonly", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", CARD2)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        # Progress bars: one for XP, one for time (and a red one when time is over)
        for name, color in (("XP", PURPLE), ("Time", ACCENT), ("Warn", GOLD), ("Over", BAD)):
            style.configure(f"{name}.Horizontal.TProgressbar", troughcolor=CARD2, background=color,
                            bordercolor=CARD2, lightcolor=color, darkcolor=color, thickness=int(8 * self.scale))

    # ---------- main window ----------

    def build_main_window(self):
        page = tk.Frame(self.root, bg=BG)
        page.pack(fill="both", expand=True, padx=18, pady=14)

        # Header: title on the left, language switch on the right
        header = tk.Frame(page, bg=BG)
        header.pack(fill="x")
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="CAD QUEST", bg=BG, fg=ACCENT, font=(NUM_FONT, 26, "bold")).pack(anchor="w")
        tk.Label(titles, text=self.t("subtitle"), bg=BG, fg=MUTED, font=(FONT, 10)).pack(anchor="w")

        lang_box = tk.Frame(header, bg=CARD2)
        lang_box.pack(side="right", anchor="n", pady=6)
        for code in ("en", "pl"):
            selected = code == self.lang
            tk.Button(
                lang_box, text=code.upper(), command=lambda c=code: self.set_language(c),
                bg=ACCENT if selected else CARD2, fg="#0b1220" if selected else MUTED,
                activebackground=ACCENT_HOVER, relief="flat", bd=0, cursor="hand2",
                padx=12, pady=4, font=(FONT, 9, "bold"),
            ).pack(side="left")

        # Rank card with XP bar + stat tiles
        rank_card = make_card(page)
        rank_card.outer.pack(fill="x", pady=(12, 10))
        self.rank_label = tk.Label(rank_card, bg=CARD, fg=GOLD, font=(NUM_FONT, 18, "bold"))
        self.rank_label.pack(anchor="w")
        self.xp_bar = ttk.Progressbar(rank_card, style="XP.Horizontal.TProgressbar", maximum=100)
        self.xp_bar.pack(fill="x", pady=(6, 2))
        self.next_rank_label = tk.Label(rank_card, bg=CARD, fg=MUTED, font=(FONT, 9))
        self.next_rank_label.pack(anchor="w")

        tiles = tk.Frame(rank_card, bg=CARD)
        tiles.pack(fill="x", pady=(10, 0))
        self.tile_values = {}
        for column, key in enumerate(("tile_xp", "tile_games", "tile_avg", "tile_best")):
            tile = tk.Frame(tiles, bg=CARD2)
            tile.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0))
            tiles.columnconfigure(column, weight=1)  # all tiles get equal width
            value = tk.Label(tile, bg=CARD2, fg=TEXT, font=(NUM_FONT, 18, "bold"))
            value.pack(anchor="w", padx=12, pady=(8, 0))
            tk.Label(tile, text=self.t(key), bg=CARD2, fg=MUTED, font=(FONT, 9)).pack(anchor="w", padx=12, pady=(0, 8))
            self.tile_values[key] = value

        # Level + API key side by side
        middle = tk.Frame(page, bg=BG)
        middle.pack(fill="x")
        middle.columnconfigure(1, weight=1)

        level_card = make_card(middle, self.t("level"))
        level_card.outer.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.level_var = tk.StringVar(value=self.config.get("level", "Beginner"))
        for name, info in LEVELS.items():
            # indicatoron=0 -> a radio button that looks like a toggle button
            tk.Radiobutton(
                level_card, variable=self.level_var, value=name, indicatoron=0,
                text=f"{self.t('level_' + name)}\n{self.t('level_info', minutes=info['minutes'], mult=info['multiplier'])}",
                justify="left", anchor="w", width=20, padx=12, pady=5,
                bg=CARD2, fg=TEXT, selectcolor=info["color"], activebackground=BORDER, activeforeground=TEXT,
                relief="flat", bd=0, cursor="hand2", font=(FONT, 9, "bold"),
            ).pack(fill="x", pady=2)

        key_card = make_card(middle, f"{self.t('api_box')}  ·  {self.t('api_hint')}")
        key_card.outer.grid(row=0, column=1, sticky="nsew")
        self.key_var = tk.StringVar(value=self.config.get("api_key", ""))
        self.key_entry = tk.Entry(
            key_card, textvariable=self.key_var, show="•", bg=CARD2, fg=TEXT, insertbackground=TEXT,
            relief="flat", highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT, font=(FONT, 10),
        )
        self.key_entry.pack(fill="x", ipady=5)
        row = tk.Frame(key_card, bg=CARD)
        row.pack(fill="x", pady=8)
        self.show_key = tk.BooleanVar(value=False)
        tk.Checkbutton(row, text=self.t("show"), variable=self.show_key, command=self.toggle_key,
                       bg=CARD, fg=TEXT, selectcolor=CARD2, activebackground=CARD, activeforeground=TEXT,
                       font=(FONT, 9)).pack(side="left")
        make_button(row, self.t("save_key"), self.save_key).pack(side="right")

        model_row = tk.Frame(key_card, bg=CARD)
        model_row.pack(fill="x", pady=(0, 8))
        tk.Label(model_row, text=self.t("model"), bg=CARD, fg=TEXT, font=(FONT, 9)).pack(side="left")
        self.model_var = tk.StringVar(value=self.config.get("model", DEFAULT_MODEL))
        self.model_box = ttk.Combobox(model_row, textvariable=self.model_var, values=self.model_names)
        self.model_box.pack(side="left", fill="x", expand=True, padx=8)
        make_button(model_row, self.t("refresh"), self.refresh_models).pack(side="right")

        tk.Label(key_card, text=self.t("key_note"), bg=CARD, fg=MUTED, font=(FONT, 8),
                 justify="left").pack(anchor="w")

        # Start - packed before the table with side="bottom", so it always stays visible
        bottom = tk.Frame(page, bg=BG)
        bottom.pack(side="bottom", fill="x")
        self.start_button = tk.Button(
            bottom, text=self.t("start"), command=self.start_game, bg=ACCENT, fg="#0b1220",
            activebackground=ACCENT_HOVER, activeforeground="#0b1220", disabledforeground=MUTED,
            relief="flat", bd=0, cursor="hand2", font=(NUM_FONT, 18, "bold"), padx=60, pady=8,
        )
        self.start_button.pack()
        self.start_button.bind("<Enter>", lambda e: self.start_button["state"] == "normal"
                               and self.start_button.configure(bg=ACCENT_HOVER))
        self.start_button.bind("<Leave>", lambda e: self.start_button["state"] == "normal"
                               and self.start_button.configure(bg=ACCENT))
        self.status_label = tk.Label(bottom, text="", bg=BG, fg=MUTED, font=(FONT, 9))
        self.status_label.pack(pady=(6, 0))

        # Recent games table (takes the space that is left)
        games_card = make_card(page, self.t("recent"))
        games_card.outer.pack(fill="both", expand=True, pady=10)
        columns = ("date", "level", "task", "time", "accuracy", "points")
        self.table = ttk.Treeview(games_card, columns=columns, show="headings", height=6)
        widths = {"date": 120, "level": 130, "task": 280, "time": 70, "accuracy": 90, "points": 70}
        for col in columns:
            self.table.heading(col, text=self.t("col_" + col))
            self.table.column(col, width=int(widths[col] * self.scale), anchor="w" if col == "task" else "center")
        self.table.tag_configure("odd", background="#20232c")  # striped rows
        self.table.tag_configure("gave_up", foreground=MUTED)
        self.table.pack(fill="both", expand=True)
        self.no_games_label = tk.Label(games_card, text=self.t("no_games"), bg=CARD, fg=MUTED, font=(FONT, 10))

    def set_language(self, code):
        if code == self.lang:
            return
        self.lang = code
        self.config["language"] = code
        self.config["model"] = self.model_var.get().strip()
        self.config["level"] = self.level_var.get()
        save_json(CONFIG_FILE, self.config)
        # Simplest way to translate everything: destroy the window content and build it again
        for widget in self.root.winfo_children():
            widget.destroy()
        self.build_main_window()
        self.refresh_stats()

    def set_start_enabled(self, enabled):
        if enabled:
            self.start_button.configure(state="normal", bg=ACCENT)
        else:
            self.start_button.configure(state="disabled", bg=BORDER)

    def toggle_key(self):
        self.key_entry.configure(show="" if self.show_key.get() else "•")

    def save_key(self):
        self.config["api_key"] = self.key_var.get().strip()
        save_json(CONFIG_FILE, self.config)
        self.status_label.configure(text=self.t("key_saved"))
        self.refresh_models()

    def api_key(self):
        # Key from the GUI first, environment variable as a backup
        return self.key_var.get().strip() or os.environ.get("GEMINI_API_KEY", "")

    def refresh_models(self):
        key = self.api_key()
        if not key:
            messagebox.showwarning("CAD Quest", self.t("need_key"))
            return
        self.status_label.configure(text=self.t("models_loading"))

        def on_models(names):
            self.model_names = names
            self.model_box.configure(values=names)
            if names and self.model_var.get() not in names:
                # Prefer a "flash" model: fast and has the most generous free quota
                flash = [n for n in names if "flash" in n and "lite" not in n]
                self.model_var.set(flash[-1] if flash else names[-1])
            self.status_label.configure(text=self.t("models_found", n=len(names)))

        self.run_in_background(lambda: list_models(key), on_models)

    def refresh_stats(self):
        for row in self.table.get_children():
            self.table.delete(row)
        for i, game in enumerate(reversed(self.history[-30:])):  # newest first
            accuracy = self.t("gave_up_cell") if game["gave_up"] else f"{game['accuracy']}%"
            tags = ["odd"] if i % 2 else []
            if game["gave_up"]:
                tags.append("gave_up")
            self.table.insert("", "end", tags=tags, values=(
                game["date"], self.t("level_" + game["level"]), game["task"],
                format_time(game["seconds"]), accuracy, game["points"],
            ))
        # Show a hint instead of an empty table
        if self.history:
            self.no_games_label.place_forget()
        else:
            self.no_games_label.place(relx=0.5, rely=0.6, anchor="center")

        xp = sum(g["points"] for g in self.history)
        finished = [g for g in self.history if not g["gave_up"]]
        avg = sum(g["accuracy"] for g in finished) / len(finished) if finished else 0
        best = max((g["points"] for g in self.history), default=0)

        index = rank_index(xp)
        self.rank_label.configure(text=f"★  {self.rank_name(index)}")
        if index + 1 < len(RANKS):
            low, high = RANKS[index][0], RANKS[index + 1][0]
            self.xp_bar["value"] = (xp - low) / (high - low) * 100
            self.next_rank_label.configure(text=self.t("next_rank", left=high - xp, rank=self.rank_name(index + 1)))
        else:
            self.xp_bar["value"] = 100
            self.next_rank_label.configure(text=self.t("max_rank"))

        self.tile_values["tile_xp"].configure(text=str(xp), fg=PURPLE)
        self.tile_values["tile_games"].configure(text=str(len(self.history)))
        self.tile_values["tile_avg"].configure(text=f"{avg:.0f}%")
        self.tile_values["tile_best"].configure(text=str(best), fg=GOLD)

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
        self.set_start_enabled(True)
        self.status_label.configure(text="")
        if self.game_window:  # review failed - let the player press FINISH again
            self.game_window.title(f"CAD Quest - {self.task['title']}")
            self.timer_label.configure(text=format_time(self.elapsed), font=(NUM_FONT, 54, "bold"))
        message = self.t(error.key, **error.params) if isinstance(error, ApiError) else str(error)
        messagebox.showerror("CAD Quest", message, parent=self.game_window or self.root)

    # ---------- 1. generate a task ----------

    def start_game(self):
        if not self.api_key():
            messagebox.showwarning("CAD Quest", self.t("need_key"))
            return
        self.level = self.level_var.get()
        self.config["level"] = self.level
        self.config["model"] = self.model_var.get().strip()
        save_json(CONFIG_FILE, self.config)

        self.set_start_enabled(False)
        self.status_label.configure(text=self.t("designing"))

        recent = [g["task"] for g in self.history[-15:]]
        prompt = (
            f"Level: {self.level}. Scope: {LEVELS[self.level]['scope']}\n"
            f"Time limit for the player: {LEVELS[self.level]['minutes']} minutes.\n"
            f"Do not repeat these recent tasks: {recent or 'none'}.\n"
            f"{GEMINI_LANGUAGE[self.lang]}"
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
        overlay.configure(bg=BG)
        center = tk.Frame(overlay, bg=BG)
        center.place(relx=0.5, rely=0.5, anchor="center")
        number = tk.Label(center, bg=BG, font=(NUM_FONT, 220, "bold"))
        number.pack()
        tk.Label(center, text=f"{self.t('level_' + self.level)}  ·  {task['title']}",
                 bg=BG, fg=MUTED, font=(FONT, 20)).pack()

        steps = [("3", PURPLE), ("2", ACCENT), ("1", GOLD), (self.t("go"), GOOD)]

        def show_step(i):
            if i < len(steps):
                text, color = steps[i]
                number.configure(text=text, fg=color)
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
        win.geometry(self.size(480, 680) + "+20+20")
        win.attributes("-topmost", True)  # stays above SolidWorks
        win.protocol("WM_DELETE_WINDOW", self.give_up)

        limit = LEVELS[self.level]["minutes"] * 60
        timer_card = make_card(win)
        timer_card.outer.pack(fill="x", padx=12, pady=(12, 0))
        self.timer_label = tk.Label(timer_card, text="00:00", bg=CARD, fg=ACCENT, font=(NUM_FONT, 54, "bold"))
        self.timer_label.pack()
        self.time_bar = ttk.Progressbar(timer_card, style="Time.Horizontal.TProgressbar", maximum=limit)
        self.time_bar.pack(fill="x", pady=(4, 4))
        tk.Label(timer_card, text=f"{self.t('limit', limit=format_time(limit))}  ·  {self.t('level_' + self.level)}",
                 bg=CARD, fg=MUTED, font=(FONT, 9)).pack()

        buttons = tk.Frame(win, bg=BG)
        buttons.pack(side="bottom", fill="x", padx=12, pady=(0, 12))
        make_button(buttons, self.t("give_up"), self.give_up, kind="danger").pack(side="left")
        make_button(buttons, self.t("finish"), self.finish, kind="primary").pack(side="right")

        text = tk.Text(win, wrap="word", bg=CARD, fg=TEXT, font=(FONT, 10), relief="flat", padx=14, pady=12,
                       highlightthickness=1, highlightbackground=BORDER, cursor="arrow", spacing1=2, spacing3=2)
        text.pack(fill="both", expand=True, padx=12, pady=10)
        text.insert("end", self.task["title"] + "\n", "title")
        text.insert("end", self.task["description"] + "\n\n")
        # Clickable checklist: each requirement gets its own tag, so a click knows which line it was
        for number, requirement in enumerate(self.task["requirements"], start=1):
            tag = f"req{number}"
            text.insert("end", f"☐  {number}. {requirement}\n", ("req", tag))
            text.tag_bind(tag, "<Button-1>", lambda e, tag=tag: self.toggle_requirement(text, tag))
        box = " × ".join(f"{v:g}" for v in self.task["bounding_box_mm"])
        text.insert("end", "\n" + self.t("overall", box=box) + "\n", "size")
        text.insert("end", "\n" + self.t("done_hint"), "hint")
        text.tag_configure("title", font=(NUM_FONT, 15, "bold"), foreground=ACCENT, spacing3=8)
        text.tag_configure("req", lmargin2=28, spacing1=4)  # wrapped lines indent under the text
        text.tag_configure("done", foreground=MUTED, overstrike=True)
        text.tag_configure("size", foreground=GOLD, font=(FONT, 10, "bold"))
        text.tag_configure("hint", foreground=MUTED, font=(FONT, 9, "italic"))
        text.tag_bind("req", "<Enter>", lambda e: text.configure(cursor="hand2"))
        text.tag_bind("req", "<Leave>", lambda e: text.configure(cursor="arrow"))
        text.configure(state="disabled")  # read-only

        self.start_time = time.monotonic()  # monotonic = not affected by clock changes
        self.timer_running = True
        self.update_timer()

    def toggle_requirement(self, text, tag):
        """Swap ☐ <-> ☑ at the start of the clicked line and strike the line through."""
        start, end = text.tag_ranges(tag)[:2]
        text.configure(state="normal")  # a disabled Text can't be edited, even by code
        ticked = text.get(start) == "☑"
        text.delete(start)
        text.insert(start, "☐" if ticked else "☑", ("req", tag))
        if ticked:
            text.tag_remove("done", start, end)
        else:
            text.tag_add("done", start, end)
        text.configure(state="disabled")

    def update_timer(self):
        if not self.timer_running:
            return
        self.elapsed = time.monotonic() - self.start_time
        limit = LEVELS[self.level]["minutes"] * 60
        if self.elapsed > limit:
            color, style = BAD, "Over"
        elif self.elapsed > 0.8 * limit:  # last 20% of the time -> warning color
            color, style = GOLD, "Warn"
        else:
            color, style = ACCENT, "Time"
        self.timer_label.configure(text=format_time(self.elapsed), fg=color)
        self.time_bar.configure(value=min(self.elapsed, limit), style=f"{style}.Horizontal.TProgressbar")
        self.root.after(200, self.update_timer)

    def give_up(self):
        if not messagebox.askyesno("CAD Quest", self.t("give_up_q"), parent=self.game_window):
            return
        self.timer_running = False
        self.save_game(accuracy=0, points=0, gave_up=True)
        self.end_game()

    # ---------- 4. upload and review ----------

    def finish(self):
        self.timer_running = False  # time stops when you press FINISH, upload doesn't count
        paths = filedialog.askopenfilenames(
            parent=self.game_window,
            title=self.t("file_title"),
            filetypes=[(self.t("file_types"), "*.png *.jpg *.jpeg *.stl"), (self.t("all_files"), "*.*")],
        )
        if not paths:
            self.timer_running = True  # cancelled -> keep counting
            self.start_time = time.monotonic() - self.elapsed
            self.update_timer()
            return

        images = [p for p in paths if p.lower().endswith((".png", ".jpg", ".jpeg"))][:MAX_IMAGES]
        stls = [p for p in paths if p.lower().endswith(".stl")]
        if not images and not stls:
            messagebox.showwarning("CAD Quest", self.t("need_file"), parent=self.game_window)
            self.finish()
            return

        self.game_window.title("CAD Quest - " + self.t("checking_title"))
        self.timer_label.configure(text=self.t("checking"), font=(NUM_FONT, 30, "bold"))

        task = self.task
        key, model = self.api_key(), self.config["model"]
        language = GEMINI_LANGUAGE[self.lang]

        def work():
            parts = [image_to_part(p) for p in images]
            report = "\n\n".join(read_stl(p) for p in stls) or "No STL provided."
            parts.append({"text": (
                f"The exercise:\n{json.dumps(task, indent=2, ensure_ascii=False)}\n\n{report}\n\n"
                f"Grade the player's model. {language}"
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

        old_rank = rank_index(sum(g["points"] for g in self.history))
        self.save_game(accuracy=accuracy, points=points, gave_up=False)
        new_rank = rank_index(sum(g["points"] for g in self.history))

        self.end_game()
        self.show_result(review, accuracy, bonus, points, rank_up=new_rank if new_rank != old_rank else None)

    def show_result(self, review, accuracy, bonus, points, rank_up):
        win = tk.Toplevel(self.root)
        win.title("CAD Quest - " + self.t("result"))
        win.configure(bg=BG)
        win.geometry(self.size(600, 640) + "+60+20")

        verdict, color = (self.t("passed"), GOOD) if review["passed"] else (self.t("failed"), BAD)
        tk.Label(win, text=verdict, bg=BG, fg=color, font=(NUM_FONT, 30, "bold")).pack(pady=(16, 0))
        xp_label = tk.Label(win, text="+0 XP", bg=BG, fg=PURPLE, font=(NUM_FONT, 24, "bold"))
        xp_label.pack()
        tk.Label(win, bg=BG, fg=TEXT, font=(FONT, 10), text=self.t(
            "result_line", acc=accuracy, time=format_time(self.elapsed), bonus=bonus,
            mult=LEVELS[self.level]["multiplier"],
        )).pack(pady=4)
        if rank_up is not None:
            tk.Label(win, text="★ " + self.t("rank_up", rank=self.rank_name(rank_up)), bg=BG, fg=GOLD,
                     font=(NUM_FONT, 15, "bold")).pack()

        # XP counts up from 0 in ~1 second (30 steps, 33 ms each)
        def count_up(step=1):
            xp_label.configure(text=f"+{round(points * step / 30)} XP")
            if step < 30:
                win.after(33, count_up, step + 1)

        count_up()

        make_button(win, self.t("close"), win.destroy).pack(side="bottom", pady=(0, 14))
        text = tk.Text(win, wrap="word", bg=CARD, fg=TEXT, font=(FONT, 10), relief="flat", padx=14, pady=12,
                       highlightthickness=1, highlightbackground=BORDER, spacing1=2)
        text.pack(fill="both", expand=True, padx=14, pady=12)
        icons = {"ok": ("✔", "ok"), "wrong": ("✘", "wrong"), "not_visible": ("?", "unknown")}
        for check in review["checks"]:
            icon, tag = icons.get(check["status"], ("?", "unknown"))
            text.insert("end", f"{icon}  ", tag)
            text.insert("end", f"{check['requirement']}\n", "req")
            text.insert("end", f"{check['comment']}\n\n", "comment")
        text.insert("end", self.t("feedback"), "ok")
        text.insert("end", review["feedback"])
        text.tag_configure("ok", foreground=GOOD, font=(FONT, 10, "bold"))
        text.tag_configure("wrong", foreground=BAD, font=(FONT, 10, "bold"))
        text.tag_configure("unknown", foreground=GOLD, font=(FONT, 10, "bold"))
        text.tag_configure("req", lmargin2=24)
        text.tag_configure("comment", foreground=MUTED, lmargin1=24, lmargin2=24)
        text.configure(state="disabled")

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
        self.set_start_enabled(True)
        self.refresh_stats()


if __name__ == "__main__":
    # Tell Windows we handle high-DPI ourselves -> sharp text instead of blurry upscaling
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass  # not Windows, or an old Windows version
    root = tk.Tk()
    CadQuestApp(root)
    root.mainloop()
