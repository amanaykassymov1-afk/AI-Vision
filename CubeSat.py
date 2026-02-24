# -*- coding: utf-8 -*-
"""
CubeSatPRO — NASA HUD + Caching + Weather + 5Y + Charts + Gemini (Tkinter)
------------------------------------------------------------------------------
✅ City OR coords: "Almaty" or "43.2389, 76.8897"
✅ Current first (fast), History later (heavy)
✅ Disk cache + memory cache:
   - Current TTL: 10 minutes
   - History TTL: 7 days
✅ NASA/HUD UI: gradient + grid + scanline animation + corner brackets + neon
✅ PRO UI: KPI cards, status + progress, tabs, charts, map, Gemini advice + chat
✅ No alpha HEX colors (Tkinter-safe)

Install:
  pip install requests pillow google-genai matplotlib
"""

import os
import sys
import re
import json
import time
import math
import threading
import webbrowser
from datetime import date, timedelta

import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

import requests

# Pillow optional (background image not required)
try:
    from PIL import Image, ImageTk
    PIL_OK = True
except Exception:
    PIL_OK = False

# Gemini optional
try:
    from google import genai
    GEMINI_OK = True
except Exception:
    GEMINI_OK = False

# Matplotlib optional
try:
    import matplotlib
    matplotlib.use("TkAgg")
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    MPL_OK = True
except Exception:
    MPL_OK = False


# ===== UTF-8 FOR EVERYTHING (Mac/PyCharm friendly) =====
os.environ["PYTHONUTF8"] = "1"
os.environ["PYTHONIOENCODING"] = "utf-8"
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


# =========================
# CONFIG
# =========================
GEMINI_API_KEY = "AIzaSyDk3Y91cAIwQGQXepU3J9Mzmbd3Z5DpHFE"  # <-- өз кілтіңді қой
DEFAULT_CITY = "Алматы"

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
REVERSE_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/reverse"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"

USER_AGENT = "CubeSatPRO/1.0"

# Cache TTLs
TTL_CURRENT_SEC = 10 * 60          # 10 minutes
TTL_HISTORY_SEC = 7 * 24 * 3600    # 7 days

CACHE_PATH = os.path.join(os.path.expanduser("~"), ".ecoeye_cache.json")


# =========================
# SIMPLE DISK CACHE
# =========================
class DiskCache:
    """
    JSON cache:
      { key: { "ts": epoch_seconds, "value": ... }, ... }
    """
    def __init__(self, path: str):
        self.path = path
        self._mem = {}
        self._loaded = False
        self._lock = threading.Lock()

    def _load(self):
        if self._loaded:
            return
        self._loaded = True
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    self._mem = json.load(f) or {}
            else:
                self._mem = {}
        except Exception:
            self._mem = {}

    def _save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._mem, f, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception:
            pass

    def get(self, key: str, ttl_sec: int):
        now = time.time()
        with self._lock:
            self._load()
            item = self._mem.get(key)
            if not item:
                return None
            ts = item.get("ts", 0)
            if (now - ts) > ttl_sec:
                return None
            return item.get("value")

    def set(self, key: str, value):
        with self._lock:
            self._load()
            self._mem[key] = {"ts": time.time(), "value": value}
            self._save()


CACHE = DiskCache(CACHE_PATH)


# =========================
# WMO WEATHER CODE TEXT
# =========================
def wmo_weather_text(code: int) -> str:
    try:
        code = int(code)
    except Exception:
        return "Белгісіз"

    mapping = {
        0: "Ашық", 1: "Көбіне ашық", 2: "Аздап бұлтты", 3: "Бұлтты",
        45: "Тұман", 48: "Қыраулы тұман",
        51: "Әлсіз сіркіреме", 53: "Орташа сіркіреме", 55: "Күшті сіркіреме",
        56: "Әлсіз мұздақ сіркіреме", 57: "Күшті мұздақ сіркіреме",
        61: "Әлсіз жаңбыр", 63: "Орташа жаңбыр", 65: "Қатты жаңбыр",
        66: "Әлсіз мұздақ жаңбыр", 67: "Қатты мұздақ жаңбыр",
        71: "Әлсіз қар", 73: "Орташа қар", 75: "Қатты қар", 77: "Қар түйіршігі",
        80: "Әлсіз нөсер", 81: "Орташа нөсер", 82: "Қатты нөсер",
        85: "Әлсіз нөсер қар", 86: "Қатты нөсер қар",
        95: "Найзағай", 96: "Найзағай (бұршақ)", 99: "Найзағай (қатты бұршақ)",
    }
    return mapping.get(code, f"Белгісіз (код: {code})")


# =========================
# INPUT PARSING
# =========================
COORD_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[, ]\s*([+-]?\d+(?:\.\d+)?)\s*$")


def parse_location(text: str):
    m = COORD_RE.match(text or "")
    if m:
        return ("coords", float(m.group(1)), float(m.group(2)))
    return ("city", (text or "").strip())


# =========================
# OPEN-METEO HELPERS (+ cache)
# =========================
def _get_json(url: str, params: dict, timeout=(6, 18)):
    headers = {"User-Agent": USER_AGENT}
    r = requests.get(url, params=params, timeout=timeout, headers=headers)
    r.raise_for_status()
    return r.json()


def geocode_city(city: str):
    key = f"geo:city:{city.lower().strip()}"
    cached = CACHE.get(key, ttl_sec=30 * 24 * 3600)  # 30 days cache
    if cached:
        return cached

    data = _get_json(
        GEOCODE_URL,
        params={"name": city, "count": 1, "language": "ru", "format": "json"},
        timeout=(6, 15),
    )
    results = data.get("results") or []
    if not results:
        return None
    x = results[0]
    name = f"{x.get('name','')}, {x.get('country','')}".strip(", ")
    val = (float(x["latitude"]), float(x["longitude"]), name)
    CACHE.set(key, val)
    return val


def reverse_geocode(lat: float, lon: float):
    key = f"geo:rev:{lat:.4f},{lon:.4f}"
    cached = CACHE.get(key, ttl_sec=30 * 24 * 3600)
    if cached:
        return cached
    try:
        data = _get_json(
            REVERSE_GEOCODE_URL,
            params={"latitude": lat, "longitude": lon, "count": 1, "language": "ru", "format": "json"},
            timeout=(6, 15),
        )
        results = data.get("results") or []
        if results:
            x = results[0]
            name = f"{x.get('name','')}, {x.get('country','')}".strip(", ")
            if name:
                CACHE.set(key, name)
                return name
    except Exception:
        pass
    name = f"{lat:.4f}, {lon:.4f}"
    CACHE.set(key, name)
    return name


def fetch_current(lat: float, lon: float):
    # cache per lat/lon rounded to avoid key explosion
    key = f"cur:{lat:.3f},{lon:.3f}"
    cached = CACHE.get(key, ttl_sec=TTL_CURRENT_SEC)
    if cached:
        return cached

    params = {
        "latitude": lat,
        "longitude": lon,
        "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code,wind_speed_10m,wind_direction_10m",
        "timezone": "auto",
    }
    data = _get_json(FORECAST_URL, params=params, timeout=(6, 18))
    CACHE.set(key, data)
    return data


def fetch_history_5y(lat: float, lon: float):
    end = date.today()
    start = end - timedelta(days=365 * 5)

    key = f"hist5y:{lat:.3f},{lon:.3f}:{start.isoformat()}:{end.isoformat()}"
    cached = CACHE.get(key, ttl_sec=TTL_HISTORY_SEC)
    if cached:
        return cached

    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": ",".join([
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "wind_speed_10m_max",
            "weather_code",
        ]),
        "timezone": "auto",
    }
    data = _get_json(ARCHIVE_URL, params=params, timeout=(8, 30))
    CACHE.set(key, data)
    return data


# =========================
# HISTORY SUMMARY
# =========================
def _year_bucket_counts(codes):
    counts = {"ашық": 0, "бұлт": 0, "жаңбыр": 0, "қар": 0, "тұман": 0, "найзағай": 0, "басқа": 0}
    for c in codes:
        try:
            c = int(c)
        except Exception:
            counts["басқа"] += 1
            continue

        if c in (0, 1):
            counts["ашық"] += 1
        elif c in (2, 3):
            counts["бұлт"] += 1
        elif c in (45, 48):
            counts["тұман"] += 1
        elif 51 <= c <= 57 or 61 <= c <= 67 or 80 <= c <= 82:
            counts["жаңбыр"] += 1
        elif 71 <= c <= 77 or 85 <= c <= 86:
            counts["қар"] += 1
        elif c in (95, 96, 99):
            counts["найзағай"] += 1
        else:
            counts["басқа"] += 1
    return counts


def summarize_5y(history_json: dict):
    daily = history_json.get("daily") or {}
    times = daily.get("time") or []
    tmax = daily.get("temperature_2m_max") or []
    tmin = daily.get("temperature_2m_min") or []
    prcp = daily.get("precipitation_sum") or []
    wind = daily.get("wind_speed_10m_max") or []
    codes = daily.get("weather_code") or []

    if not times:
        return {"years": [], "trend": {}, "text": "5 жылдық архив дерегі табылмады."}

    by_year = {}
    for i, d in enumerate(times):
        y = int(d[:4])
        by_year.setdefault(y, {"tmax": [], "tmin": [], "prcp": [], "wind": [], "codes": []})
        if i < len(tmax): by_year[y]["tmax"].append(tmax[i])
        if i < len(tmin): by_year[y]["tmin"].append(tmin[i])
        if i < len(prcp): by_year[y]["prcp"].append(prcp[i])
        if i < len(wind): by_year[y]["wind"].append(wind[i])
        if i < len(codes): by_year[y]["codes"].append(codes[i])

    years_sorted = sorted(by_year.keys())

    def _avg(nums):
        nums2 = [n for n in nums if isinstance(n, (int, float))]
        return (sum(nums2) / len(nums2)) if nums2 else None

    def _sum(nums):
        nums2 = [n for n in nums if isinstance(n, (int, float))]
        return sum(nums2) if nums2 else None

    rows = []
    for y in years_sorted:
        data = by_year[y]
        rows.append({
            "year": y,
            "tmax_avg": _avg(data["tmax"]),
            "tmin_avg": _avg(data["tmin"]),
            "prcp_sum": _sum(data["prcp"]),
            "wind_avg": _avg(data["wind"]),
            "counts": _year_bucket_counts(data["codes"])
        })

    first, last = rows[0], rows[-1]

    def _d(a, b):
        if a is None or b is None:
            return None
        return b - a

    trend = {
        "tmax_avg_delta": _d(first["tmax_avg"], last["tmax_avg"]),
        "tmin_avg_delta": _d(first["tmin_avg"], last["tmin_avg"]),
        "prcp_sum_delta": _d(first["prcp_sum"], last["prcp_sum"]),
        "wind_avg_delta": _d(first["wind_avg"], last["wind_avg"]),
    }

    def fmt(x, nd=2, unit=""):
        if x is None:
            return "—"
        return f"{x:.{nd}f}{unit}"

    lines = []
    lines.append("📊 Соңғы 5 жыл (жылдық қорытынды):")
    lines.append("Жыл | TMax орта | TMin орта | Жауын (мм) | Жел орта (м/с) | Ашық/Жаңбыр/Қар/Тұман/Найзағай")
    lines.append("-" * 104)
    for r in rows:
        c = r["counts"]
        lines.append(
            f"{r['year']} | {fmt(r['tmax_avg'],2):>8} | {fmt(r['tmin_avg'],2):>8} | "
            f"{fmt(r['prcp_sum'],1):>9} | {fmt(r['wind_avg'],2):>12} | "
            f"{c['ашық']}/{c['жаңбыр']}/{c['қар']}/{c['тұман']}/{c['найзағай']}"
        )

    def fmt_delta(x, unit=""):
        if x is None:
            return "—"
        sign = "+" if x >= 0 else ""
        return f"{sign}{x:.2f}{unit}"

    lines.append("\n📈 Тренд (5 жыл):")
    lines.append(f"• TMax өзгерісі: {fmt_delta(trend['tmax_avg_delta'], '°C')}")
    lines.append(f"• TMin өзгерісі: {fmt_delta(trend['tmin_avg_delta'], '°C')}")
    lines.append(f"• Жауын өзгерісі: {fmt_delta(trend['prcp_sum_delta'], ' мм')}")
    lines.append(f"• Жел өзгерісі: {fmt_delta(trend['wind_avg_delta'], ' м/с')}")

    return {"years": rows, "trend": trend, "text": "\n".join(lines)}


# =========================
# LOCAL ADVICE (fallback)
# =========================
def local_weather_advice(temp, wind, desc, trend=None) -> str:
    tips = []
    try:
        t = float(temp)
    except Exception:
        t = None
    try:
        w = float(wind)
    except Exception:
        w = None

    if t is not None:
        if t <= -15:
            tips.append("Өте суық: бет/қолды қорғаңыз, ұзақ сыртта жүрмеңіз.")
        elif t <= 0:
            tips.append("Суық: жылы киім, бас киім, қолғап қажет.")
        elif t >= 30:
            tips.append("Ыстық: су ішіңіз, күннің ыстық уақытында сақ болыңыз.")
        elif 20 <= t < 30:
            tips.append("Жылы: жеңіл киім, кешке салқындауы мүмкін.")

    if w is not None and w >= 12:
        tips.append("Жел күшейген: сыртта абай болыңыз.")

    dlow = (desc or "").lower()
    if any(x in dlow for x in ["жаңбыр", "нөсер", "сіркіреме"]):
        tips.append("Қолшатыр алып жүріңіз.")
    if "қар" in dlow:
        tips.append("Жол тайғақ болуы мүмкін.")
    if "тұман" in dlow:
        tips.append("Көлікте жылдамдықты азайтыңыз.")
    if "найзағай" in dlow:
        tips.append("Ашық жерде сақ болыңыз.")

    if trend:
        tmax_d = trend.get("tmax_avg_delta")
        if isinstance(tmax_d, (int, float)) and tmax_d > 0.6:
            tips.append("Тренд: жылыну байқалады — ыстық күндерге дайын болыңыз.")

    return " ".join(tips) if tips else "Ауа райына сай киініп, қауіпсіздікті сақтаңыз."


# =========================
# GEMINI
# =========================
def make_gemini_client():
    if not GEMINI_OK:
        return None
    if not GEMINI_API_KEY or "МҰНДА_СЕНІҢ" in GEMINI_API_KEY:
        return None
    try:
        return genai.Client(api_key=GEMINI_API_KEY)
    except Exception:
        return None


def ask_gemini(client, prompt: str, model: str, retries: int = 1):
    if client is None:
        raise RuntimeError("Gemini қолжетімсіз (кілт/кітапхана).")

    last_err = None
    for attempt in range(retries + 1):
        try:
            resp = client.models.generate_content(model=model, contents=prompt)
            return getattr(resp, "text", "") or ""
        except Exception as e:
            last_err = e
            msg = str(e)
            if ("RESOURCE_EXHAUSTED" in msg) or ("429" in msg):
                time.sleep(10 + attempt * 12)
                continue
            raise
    raise last_err


# =========================
# NASA HUD BACKGROUND
# =========================
def draw_hud_background(canvas: tk.Canvas, w: int, h: int, phase: float):
    """
    NASA/HUD look without alpha:
    - gradient
    - grid
    - corner brackets
    - scanline
    """
    canvas.delete("bg")

    # Gradient
    top = (6, 14, 28)      # #060e1c
    bot = (10, 38, 68)     # #0a2644
    steps = 180
    for i in range(steps):
        t = i / max(steps - 1, 1)
        r = int(top[0] + (bot[0] - top[0]) * t)
        g = int(top[1] + (bot[1] - top[1]) * t)
        b = int(top[2] + (bot[2] - top[2]) * t)
        color = f"#{r:02x}{g:02x}{b:02x}"
        y0 = int(h * i / steps)
        y1 = int(h * (i + 1) / steps)
        canvas.create_rectangle(0, y0, w, y1, fill=color, outline="", tags="bg")

    # Subtle glow blobs (solid layers)
    def glow(cx, cy, radii_colors):
        for rad, col in radii_colors:
            canvas.create_oval(cx-rad, cy-rad, cx+rad, cy+rad, fill=col, outline="", tags="bg")

    glow(220, 180, [(260, "#0b223c"), (190, "#0c3a63"), (130, "#0e4f86"), (80, "#0ea5e9")])
    glow(w-180, 140, [(240, "#0a1f35"), (170, "#123b60"), (110, "#1d4ed8")])
    glow(w-220, h-160, [(260, "#0b1f35"), (190, "#0b2b4b"), (120, "#0f766e")])

    # Grid
    grid_col = "#0f2b4d"
    major_col = "#143b66"
    step = 28
    for x in range(0, w + 1, step):
        canvas.create_line(x, 0, x, h, fill=grid_col, tags="bg")
    for y in range(0, h + 1, step):
        canvas.create_line(0, y, w, y, fill=grid_col, tags="bg")
    # major lines
    step2 = step * 4
    for x in range(0, w + 1, step2):
        canvas.create_line(x, 0, x, h, fill=major_col, tags="bg")
    for y in range(0, h + 1, step2):
        canvas.create_line(0, y, w, y, fill=major_col, tags="bg")

    # Corner brackets
    accent = "#38bdf8"
    pad = 14
    L = 42
    # TL
    canvas.create_line(pad, pad, pad+L, pad, fill=accent, width=2, tags="bg")
    canvas.create_line(pad, pad, pad, pad+L, fill=accent, width=2, tags="bg")
    # TR
    canvas.create_line(w-pad, pad, w-pad-L, pad, fill=accent, width=2, tags="bg")
    canvas.create_line(w-pad, pad, w-pad, pad+L, fill=accent, width=2, tags="bg")
    # BL
    canvas.create_line(pad, h-pad, pad+L, h-pad, fill=accent, width=2, tags="bg")
    canvas.create_line(pad, h-pad, pad, h-pad-L, fill=accent, width=2, tags="bg")
    # BR
    canvas.create_line(w-pad, h-pad, w-pad-L, h-pad, fill=accent, width=2, tags="bg")
    canvas.create_line(w-pad, h-pad, w-pad, h-pad-L, fill=accent, width=2, tags="bg")

    # Scanline (animated)
    scan_y = int((math.sin(phase) * 0.5 + 0.5) * (h - 1))
    canvas.create_rectangle(0, scan_y, w, min(scan_y + 3, h), fill="#2dd4bf", outline="", tags="bg")


# =========================
# APP
# =========================
class CubeSatPRO:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("CubeSat PRO — NASA HUD")
        self.root.geometry("1060x760")
        self.root.minsize(1060, 760)

        # Palette (Tkinter-safe)
        self.BG = "#060e1c"
        self.CARD = "#0f2238"
        self.CARD2 = "#102a43"
        self.TEXT = "#e6eef7"
        self.MUTED = "#a9bfd6"
        self.ACCENT = "#38bdf8"
        self.ACCENT2 = "#2dd4bf"
        self.BORDER = "#1e3a5f"

        # Background canvas (HUD)
        self.hud = tk.Canvas(self.root, highlightthickness=0, bd=0)
        self.hud.place(x=0, y=0, relwidth=1, relheight=1)
        self._phase = 0.0

        # Foreground container
        self.container = ttk.Frame(self.root, padding=16)
        self.container.place(x=0, y=0, relwidth=1, relheight=1)
        self.container.lift()

        # Redraw HUD on resize + animate scanline
        self.root.bind("<Configure>", self._on_resize)
        self._animate_hud()

        # Gemini
        self.gemini_client = make_gemini_client()

        # ttk Style
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass

        style.configure(".", font=("Arial", 11))

        style.configure("Header.TLabel", font=("Arial", 18, "bold"), foreground=self.TEXT, background=self.BG)
        style.configure("Sub.TLabel", font=("Arial", 11), foreground=self.MUTED, background=self.BG)
        style.configure("Card.TFrame", background=self.CARD)
        style.configure("Card2.TFrame", background=self.CARD2)

        style.configure("TLabelframe", background=self.CARD, borderwidth=1, relief="flat")
        style.configure("TLabelframe.Label", background=self.CARD, foreground=self.TEXT, font=("Arial", 11, "bold"))
        style.configure("TLabel", background=self.CARD, foreground=self.TEXT)

        style.configure("TNotebook", background=self.BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 8), font=("Arial", 11, "bold"))
        style.map("TNotebook.Tab",
                  background=[("selected", self.CARD), ("!selected", self.CARD2)],
                  foreground=[("selected", self.TEXT), ("!selected", self.MUTED)])

        style.configure("Accent.TButton", font=("Arial", 11, "bold"), padding=10)
        style.map("Accent.TButton", foreground=[("active", "#001018")])

        # Header bar
        head = ttk.Frame(self.container, style="Card.TFrame", padding=12)
        head.pack(fill="x")
        ttk.Label(head, text="🛰 CubeSat PRO", style="Header.TLabel").pack(side="left")
        ttk.Label(head, text="NASA HUD • Cache • Current→History • Charts • Gemini", style="Sub.TLabel").pack(side="left", padx=12)

        # KPI strip (PRO tiles)
        self.kpi = ttk.Frame(self.container, style="Card.TFrame", padding=12)
        self.kpi.pack(fill="x", pady=10)

        self.kpi_loc = self._make_kpi_tile(self.kpi, "Орналасуы", "—")
        self.kpi_temp = self._make_kpi_tile(self.kpi, "Температура", "—")
        self.kpi_wind = self._make_kpi_tile(self.kpi, "Жел", "—")
        self.kpi_desc = self._make_kpi_tile(self.kpi, "Көрсеткіш", "—")

        # Search card
        search = ttk.Labelframe(self.container, text="Басқару", padding=12)
        search.pack(fill="x", pady=(0, 12))

        ttk.Label(search, text="Қала немесе координата (lat, lon):").grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar(value=DEFAULT_CITY)
        self.input_entry = ttk.Entry(search, textvariable=self.input_var, width=52)
        self.input_entry.grid(row=0, column=1, sticky="w", padx=10)
        self.input_entry.bind("<Return>", lambda e: self.on_fetch())

        self.ai_enabled = tk.BooleanVar(value=True)
        ttk.Checkbutton(search, text="AI кеңес", variable=self.ai_enabled).grid(row=0, column=2, sticky="w", padx=8)

        ttk.Label(search, text="AI режимі:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.ai_mode = tk.StringVar(value="short")
        mode_frame = ttk.Frame(search, style="Card.TFrame")
        mode_frame.grid(row=1, column=1, sticky="w", padx=10, pady=(10, 0))
        ttk.Radiobutton(mode_frame, text="Қысқа", variable=self.ai_mode, value="short").pack(side="left")
        ttk.Radiobutton(mode_frame, text="Толық", variable=self.ai_mode, value="full").pack(side="left", padx=10)

        ttk.Label(search, text="Gemini model:").grid(row=2, column=0, sticky="w", pady=(10, 0))
        self.model_var = tk.StringVar(value="gemini-2.0-flash")
        self.model_combo = ttk.Combobox(
            search, textvariable=self.model_var, width=22, state="readonly",
            values=["gemini-2.0-flash", "gemini-2.0-pro", "gemini-1.5-flash", "gemini-1.5-pro"]
        )
        self.model_combo.grid(row=2, column=1, sticky="w", padx=10, pady=(10, 0))

        btns = ttk.Frame(search, style="Card.TFrame")
        btns.grid(row=0, column=3, rowspan=3, sticky="e", padx=(10, 0))

        self.fetch_btn = ttk.Button(btns, text="🚀 Іздеу", command=self.on_fetch, style="Accent.TButton")
        self.fetch_btn.pack(side="top", fill="x")

        self.map_btn = ttk.Button(btns, text="🗺 Карта", command=self.open_map, style="Accent.TButton", state="disabled")
        self.map_btn.pack(side="top", fill="x", pady=8)

        self.clear_cache_btn = ttk.Button(btns, text="🧹 Кэш тазалау", command=self.clear_cache, style="Accent.TButton")
        self.clear_cache_btn.pack(side="top", fill="x")

        # Status + Progress
        bottom = ttk.Frame(self.container, style="Card.TFrame", padding=10)
        bottom.pack(fill="x", pady=(0, 10))

        self.status_var = tk.StringVar(value="Дайын.")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left")

        self.progress = ttk.Progressbar(bottom, mode="indeterminate", length=220)
        self.progress.pack(side="right")

        # Tabs
        self.tabs = ttk.Notebook(self.container)
        self.tabs.pack(fill="both", expand=True)

        self.tab_weather = ttk.Frame(self.tabs, padding=10, style="Card.TFrame")
        self.tab_history = ttk.Frame(self.tabs, padding=10, style="Card.TFrame")
        self.tab_charts = ttk.Frame(self.tabs, padding=10, style="Card.TFrame")
        self.tab_ai = ttk.Frame(self.tabs, padding=10, style="Card.TFrame")

        self.tabs.add(self.tab_weather, text="Қазіргі уақыт")
        self.tabs.add(self.tab_history, text="5жылдық архив")
        self.tabs.add(self.tab_charts, text="Кесте")
        self.tabs.add(self.tab_ai, text="ЖИ / Чат")

        # Textboxes
        self.weather_box = ScrolledText(self.tab_weather, wrap="word", font=("Menlo", 12), relief="flat", bd=0)
        self.weather_box.pack(fill="both", expand=True)

        self.history_box = ScrolledText(self.tab_history, wrap="word", font=("Menlo", 12), relief="flat", bd=0)
        self.history_box.pack(fill="both", expand=True)

        # Charts
        self.charts_info = ttk.Label(self.tab_charts, text="", background=self.CARD, foreground=self.MUTED)
        self.charts_info.pack(anchor="w", pady=(0, 8))
        self.chart_frame = ttk.Frame(self.tab_charts, style="Card.TFrame")
        self.chart_frame.pack(fill="both", expand=True)

        # AI
        ai_top = ttk.Labelframe(self.tab_ai, text="Briefing", padding=10)
        ai_top.pack(fill="both", expand=True)

        self.ai_box = ScrolledText(ai_top, wrap="word", font=("Menlo", 12), relief="flat", bd=0, height=12)
        self.ai_box.pack(fill="both", expand=True)

        chat = ttk.Labelframe(self.tab_ai, text="Comms", padding=10)
        chat.pack(fill="x", pady=10)
        self.chat_var = tk.StringVar()
        self.chat_entry = ttk.Entry(chat, textvariable=self.chat_var)
        self.chat_entry.pack(side="left", fill="x", expand=True)
        self.chat_entry.bind("<Return>", lambda e: self.on_chat())
        ttk.Button(chat, text="➤ SEND", command=self.on_chat, style="Accent.TButton").pack(side="left", padx=8)

        # Apply HUD textbox look
        self._apply_hud_textbox(self.weather_box)
        self._apply_hud_textbox(self.history_box)
        self._apply_hud_textbox(self.ai_box)

        self._last_context = None

        self._set_text(self.weather_box, "Type a city or coordinates and press RUN SCAN.\nExample: Алматы or 43.2389, 76.8897")
        self._set_text(self.history_box, "Archive will appear here after Live scan.")
        self._set_text(self.ai_box, "AI briefing will appear here.\nIf Gemini limit happens, fallback advice is shown.")
        self._charts_placeholder()

    # ---------- UI: KPI ----------
    def _make_kpi_tile(self, parent, title, value):
        tile = ttk.Frame(parent, style="Card2.TFrame", padding=(12, 10))
        tile.pack(side="left", fill="x", expand=True, padx=6)

        t = tk.Label(tile, text=title, font=("Menlo", 10, "bold"), fg=self.MUTED, bg=self.CARD2)
        t.pack(anchor="w")
        v = tk.Label(tile, text=value, font=("Menlo", 16, "bold"), fg=self.TEXT, bg=self.CARD2)
        v.pack(anchor="w", pady=(4, 0))
        return (tile, v)

    def _set_kpi(self, loc="—", temp="—", wind="—", desc="—"):
        _, vloc = self.kpi_loc
        _, vtmp = self.kpi_temp
        _, vwnd = self.kpi_wind
        _, vds  = self.kpi_desc
        vloc.config(text=loc)
        vtmp.config(text=temp)
        vwnd.config(text=wind)
        vds.config(text=desc)

    # ---------- UI helpers ----------
    def _apply_hud_textbox(self, tb: ScrolledText):
        tb.configure(
            bg="#0b1b2e",
            fg=self.TEXT,
            insertbackground=self.TEXT,
            selectbackground=self.ACCENT,
            padx=12,
            pady=10,
            highlightthickness=1,
            highlightbackground=self.BORDER,
            highlightcolor=self.ACCENT2,
        )

    def _set_text(self, widget, text: str):
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text if isinstance(text, str) else str(text))
        widget.see(tk.END)
        widget.configure(state="normal")

    def _append_text(self, widget, text: str):
        widget.configure(state="normal")
        widget.insert(tk.END, text if isinstance(text, str) else str(text))
        widget.see(tk.END)
        widget.configure(state="normal")

    def _status(self, s: str):
        self.status_var.set(s)

    def _busy(self, is_busy: bool):
        if is_busy:
            self.progress.start(12)
        else:
            self.progress.stop()

    # ---------- HUD background ----------
    def _on_resize(self, event):
        if event.widget is self.root:
            self._redraw_hud()

    def _redraw_hud(self):
        w = max(self.root.winfo_width(), 1)
        h = max(self.root.winfo_height(), 1)
        draw_hud_background(self.hud, w, h, self._phase)

    def _animate_hud(self):
        self._phase += 0.10
        self._redraw_hud()
        self.root.after(50, self._animate_hud)

    # ---------- Cache controls ----------
    def clear_cache(self):
        # safest: delete file and reset in-memory
        try:
            if os.path.exists(CACHE_PATH):
                os.remove(CACHE_PATH)
        except Exception:
            pass
        global CACHE
        CACHE = DiskCache(CACHE_PATH)
        messagebox.showinfo("Cache", "Кэш тазаланды ✅")

    # ---------- Map ----------
    def open_map(self):
        if not self._last_context:
            return
        lat = self._last_context["lat"]
        lon = self._last_context["lon"]
        url = f"https://www.openstreetmap.org/?mlat={lat:.6f}&mlon={lon:.6f}#map=11/{lat:.6f}/{lon:.6f}"
        try:
            webbrowser.open(url)
        except Exception:
            messagebox.showinfo("Map", url)

    # ---------- Charts ----------
    def _charts_placeholder(self):
        if not MPL_OK:
            self.charts_info.configure(text="Matplotlib жоқ. График үшін: pip install matplotlib")
        else:
            self.charts_info.configure(text="Charts will appear after archive loads.")

    def _clear_chart_frame(self):
        for w in self.chart_frame.winfo_children():
            w.destroy()

    def _render_charts(self, rows):
        self._clear_chart_frame()
        if not MPL_OK:
            self._charts_placeholder()
            return

        years = [r["year"] for r in rows]
        tmax = [r["tmax_avg"] if r["tmax_avg"] is not None else float("nan") for r in rows]
        tmin = [r["tmin_avg"] if r["tmin_avg"] is not None else float("nan") for r in rows]
        prcp = [r["prcp_sum"] if r["prcp_sum"] is not None else 0.0 for r in rows]
        wind = [r["wind_avg"] if r["wind_avg"] is not None else float("nan") for r in rows]

        fig1 = Figure(figsize=(9.1, 3.0), dpi=100)
        ax1 = fig1.add_subplot(111)
        ax1.plot(years, tmax, marker="o", label="TMax орта (°C)")
        ax1.plot(years, tmin, marker="o", label="TMin орта (°C)")
        ax1.set_title("Yearly Temperature (avg)")
        ax1.set_xlabel("Year")
        ax1.set_ylabel("°C")
        ax1.grid(True, alpha=0.3)
        ax1.legend(loc="best")

        canvas1 = FigureCanvasTkAgg(fig1, master=self.chart_frame)
        canvas1.draw()
        canvas1.get_tk_widget().pack(fill="x", pady=(0, 10))

        fig2 = Figure(figsize=(9.1, 3.2), dpi=100)
        ax2 = fig2.add_subplot(111)
        ax2.bar(years, prcp, label="Precip (mm)")
        ax2.set_title("Precipitation + Wind")
        ax2.set_xlabel("Year")
        ax2.set_ylabel("mm")
        ax2.grid(True, axis="y", alpha=0.3)

        ax2b = ax2.twinx()
        ax2b.plot(years, wind, marker="o", label="Wind (m/s)")
        ax2b.set_ylabel("m/s")

        h1, l1 = ax2.get_legend_handles_labels()
        h2, l2 = ax2b.get_legend_handles_labels()
        ax2.legend(h1 + h2, l1 + l2, loc="best")

        canvas2 = FigureCanvasTkAgg(fig2, master=self.chart_frame)
        canvas2.draw()
        canvas2.get_tk_widget().pack(fill="x")

        self.charts_info.configure(text="✅ Charts ready.")

    # ---------- Main flow (Current → History) ----------
    def on_fetch(self):
        query = self.input_var.get().strip()
        if not query:
            messagebox.showwarning("Input", "Қала немесе координата енгізіңіз.")
            return

        self.fetch_btn.configure(state="disabled")
        self.map_btn.configure(state="disabled")
        self._busy(True)

        self._set_kpi(loc="—", temp="—", wind="—", desc="—")
        self._status("📍 Locating...")
        self._set_text(self.weather_box, "⏳ LIVE scan in progress...")
        self._set_text(self.history_box, "Мұрағатты күту...")
        self._set_text(self.ai_box, "Брифингті күту...")
        self._clear_chart_frame()
        self._charts_placeholder()

        threading.Thread(target=self._fetch_current_first, args=(query,), daemon=True).start()

    def _fetch_current_first(self, query: str):
        try:
            kind = parse_location(query)
            if kind[0] == "city":
                city = kind[1]
                if not city:
                    raise ValueError("Қала аты бос.")
                geo = geocode_city(city)
                if not geo:
                    raise ValueError("Қала табылмады. Мысал: Алматы немесе 43.2389, 76.8897")
                lat, lon, name = geo
            else:
                _, lat, lon = kind
                if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                    raise ValueError("Координата диапазоны қате. lat: -90..90, lon: -180..180")
                name = reverse_geocode(lat, lon)

            self.root.after(0, self._status, "🌤 Loading LIVE weather...")
            current_json = fetch_current(lat, lon)

            cur = current_json.get("current") or {}
            temp = cur.get("temperature_2m", "—")
            feels = cur.get("apparent_temperature", "—")
            hum = cur.get("relative_humidity_2m", "—")
            wind = cur.get("wind_speed_10m", "—")
            wdir = cur.get("wind_direction_10m", "—")
            code = cur.get("weather_code", 0)
            desc = wmo_weather_text(code)

            weather_text = (
                f"🛰 LIVE FEED\n"
                f"📍 {name}\n"
                f"📌 {lat:.4f}, {lon:.4f}\n\n"
                f"🌡 Temp: {temp}°C  (Feels: {feels}°C)\n"
                f"💧 Humidity: {hum}%\n"
                f"💨 Wind: {wind} m/s  (Dir: {wdir}°)\n"
                f"🌦 Status: {desc}  (code: {code})\n\n"
                f"Cache: current TTL {TTL_CURRENT_SEC//60} min • history TTL {TTL_HISTORY_SEC//86400} day(s)\n"
            )

            self._last_context = {
                "name": name,
                "lat": lat,
                "lon": lon,
                "current": {"temp": temp, "feels": feels, "hum": hum, "wind": wind, "wdir": wdir, "desc": desc, "code": code},
                "history_text": "",
                "trend": None,
                "rows": [],
            }

            # UI update (fast)
            self.root.after(0, self._set_text, self.weather_box, weather_text)
            self.root.after(0, self._set_kpi, name, f"{temp}°C", f"{wind} m/s", desc)
            self.root.after(0, self.map_btn.configure, {"state": "normal"})
            self.root.after(0, self._status, "✅ LIVE ready. 📚 Loading 5Y archive...")
            self.root.after(0, self.tabs.select, self.tab_weather)

            # Launch archive in background
            threading.Thread(target=self._fetch_history_second, args=(lat, lon), daemon=True).start()

        except Exception as e:
            self.root.after(0, self._apply_error, str(e))

    def _fetch_history_second(self, lat: float, lon: float):
        try:
            self.root.after(0, self._status, "📚 Loading 5Y archive (heavy)...")
            hist_json = fetch_history_5y(lat, lon)
            hist_sum = summarize_5y(hist_json)

            if self._last_context:
                self._last_context["history_text"] = hist_sum.get("text", "")
                self._last_context["trend"] = hist_sum.get("trend", None)
                self._last_context["rows"] = hist_sum.get("years", [])

            self.root.after(0, self._set_text, self.history_box, hist_sum.get("text", ""))

            rows = hist_sum.get("years", [])
            if rows:
                self.root.after(0, self._render_charts, rows)

            if self.ai_enabled.get() and self._last_context:
                self.root.after(0, self._status, "🧠 ЖАСАНДЫ ИНТЕЛЛЕКТ бойынша брифинг құру...")
                ai_text = self._make_ai_advice_safe(self._last_context)
                self.root.after(0, self._set_text, self.ai_box, ai_text)

            self.root.after(0, self._apply_done)

        except Exception as e:
            # keep LIVE, show archive error
            self.root.after(0, self._set_text, self.history_box, f"⚠️ Archive error: {e}")
            self.root.after(0, self._apply_done, True)

    def _apply_done(self, partial=False):
        self.fetch_btn.configure(state="normal")
        self._busy(False)
        self._status("Ready ✅" if not partial else "LIVE ready ✅ (Archive failed)")

    def _apply_error(self, err: str):
        self.fetch_btn.configure(state="normal")
        self.map_btn.configure(state="disabled")
        self._busy(False)
        self._status("Error ⚠️")
        self._set_text(self.weather_box, f"Error: {err}")
        self._set_text(self.history_box, f"Error: {err}")
        self._set_text(self.ai_box, f"Error: {err}")

    # ---------- AI ----------
    def _make_ai_advice_safe(self, ctx: dict) -> str:
        name = ctx["name"]
        cur = ctx["current"]
        hist_text = ctx.get("history_text", "")
        trend = ctx.get("trend", None)

        if self.gemini_client is None:
            return (
                "Gemini unavailable (key/library/quota).\n\n"
                "Fallback advice:\n" + local_weather_advice(cur["temp"], cur["wind"], cur["desc"], trend)
            )

        mode = self.ai_mode.get()
        if mode == "full":
            task = (
                "1) 8-12 сөйлемдік толық кеңес.\n"
                "2) 5 жылдық трендті 2-3 сөйлеммен түсіндір.\n"
                "3) Киім, жол қауіпсіздігі, жоспарлау бойынша нақты ұсыныстар.\n"
                "4) Соңында 3 қысқа bullet.\n"
            )
        else:
            task = (
                "1) 3-6 сөйлем қысқа кеңес.\n"
                "2) Тренд болса 1 сөйлем.\n"
                "3) Қауіпсіздік + күнделікті әрекет.\n"
            )

        prompt = (
            "Сен ауа райы бойынша NASA mission briefing стилінде қысқа да нақты кеңес беретін ассистентсің.\n"
            f"Локация: {name}\n\n"
            "LIVE:\n"
            f"- Temp: {cur['temp']}°C (feels {cur['feels']}°C)\n"
            f"- Humidity: {cur['hum']}%\n"
            f"- Wind: {cur['wind']} m/s\n"
            f"- Status: {cur['desc']}\n\n"
            "5Y ARCHIVE SUMMARY:\n"
            f"{hist_text}\n\n"
            "Task:\n"
            f"{task}"
        )

        try:
            model = self.model_var.get().strip() or "gemini-2.0-flash"
            ans = ask_gemini(self.gemini_client, prompt, model=model, retries=1).strip()
            if not ans:
                raise RuntimeError("Gemini returned empty text.")
            return "🧠 AI BRIEFING:\n" + ans
        except Exception:
            return (
                "Gemini temporary unavailable (quota/connection).\n\n"
                "Fallback advice:\n" + local_weather_advice(cur["temp"], cur["wind"], cur["desc"], trend)
            )

    def on_chat(self):
        q = self.chat_var.get().strip()
        if not q:
            return
        self.chat_var.set("")
        self.tabs.select(self.tab_ai)
        self._append_text(self.ai_box, f"\n\n🧑 YOU: {q}\n")

        threading.Thread(target=self._chat_worker, args=(q,), daemon=True).start()

    def _chat_worker(self, q: str):
        try:
            if self.gemini_client is None:
                raise RuntimeError("Gemini unavailable (key/library).")

            ctx = self._last_context
            context_block = ""
            if ctx:
                cur = ctx["current"]
                context_block = (
                    f"\nCONTEXT:\n"
                    f"Location: {ctx['name']}\n"
                    f"Live: {cur['temp']}°C, wind {cur['wind']} m/s, {cur['desc']}\n"
                    f"(Archive loaded: {'yes' if ctx.get('history_text') else 'no'})\n"
                )

            prompt = (
                "Жауапты қазақша бер. Қысқа әрі нақты.\n"
                + context_block +
                f"\nСұрақ: {q}\n"
            )

            model = self.model_var.get().strip() or "gemini-2.0-flash"
            ans = ask_gemini(self.gemini_client, prompt, model=model, retries=1).strip() or "—"
            self.root.after(0, self._append_text, self.ai_box, f"🤖 AI: {ans}\n")

        except Exception as e:
            self.root.after(0, self._append_text, self.ai_box, f"⚠️ AI error/limit: {e}\n")


if __name__ == "__main__":
    root = tk.Tk()
    CubeSatPRO(root)
    root.mainloop()
