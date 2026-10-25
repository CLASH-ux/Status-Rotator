import copy
import ctypes
import json
import math
import os
import queue
import random
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

import requests

# ───────────────────────── المسارات والثوابت ─────────────────────────
if getattr(sys, "frozen", False):  # لو البرنامج متبني exe
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
OLD_CONFIG_PATH = os.path.join(APP_DIR, "config.json")  # المكان القديم (جنب البرنامج)


def _config_dir():
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "StatusRotator")


CONFIG_PATH = os.path.join(_config_dir(), "config.json")  # %APPDATA%\StatusRotator\config.json

API_URL = "https://discord.com/api/v9/users/@me/settings"
ME_URL = "https://discord.com/api/v9/users/@me"  # لفحص التوكن
DEFAULT_MIN_INTERVAL = 2   # الـ rate limit الافتراضي: أقل مدة بين تغييرين (بتتغير من الواجهة)
ABS_MIN = 0.5              # حد أمان مطلق مهما كتبت
MAX_INTERVAL = 86400       # أقصى مدة (يوم)
MIN_TOKEN_LEN = 20         # أقل طول للتوكن قبل الفحص التلقائي
MAX_LEN = 128      # أقصى طول لجملة الـ custom status

# ───────────────────────── الألوان والخطوط ─────────────────────────
BG, CARD, INPUT, SURFACE, BORDER = "#1e1f22", "#2b2d31", "#1e1f22", "#383a40", "#3f4147"
TEXT, MUTED, DISABLED = "#f2f3f5", "#949ba4", "#35373c"
ACCENT, ACCENT_H = "#5865f2", "#4752c4"
GREEN, GREEN_H = "#23a55a", "#1a8a4a"
RED, RED_H = "#da373c", "#a12828"
WARN = "#f0b232"

FONT = ("Segoe UI", 10)
FONT_B = ("Segoe UI", 10, "bold")
FONT_S = ("Segoe UI", 9)
FONT_SB = ("Segoe UI", 8, "bold")
FONT_H = ("Segoe UI", 18, "bold")

DEFAULT_CONFIG = {
    "token": "",
    "default_interval": 3,
    "min_interval": DEFAULT_MIN_INTERVAL,
    "shuffle": False,
    "auto_run": True,            # يبدأ التبديل أول ما البرنامج يفتح
    "start_with_windows": True,  # يضيف نفسه في الـ Startup
    "tray": True,                # أيقونة في الـ system tray (X بيخفي النافذة، والـ Startup يبدأ مخفي)
    "statuses": [
        {"text": "أهلاً بيكم 👋", "seconds": None},
        {"text": "شغال على مشروع جديد 💻", "seconds": 5},
    ],
}


# ───────────────────────── الإعدادات ─────────────────────────
def to_seconds(value):
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return min(max(v, ABS_MIN), MAX_INTERVAL)


def normalize_statuses(value):
    """يقبل الشكل الجديد (قايمة) أو القديم (نص: كل سطر جملة، وبعدها | مدة)."""
    items = []
    if isinstance(value, list):
        for it in value:
            if isinstance(it, dict):
                text, secs = str(it.get("text", "")).strip(), to_seconds(it.get("seconds"))
            else:
                text, secs = str(it).strip(), None
            if text:
                items.append({"text": text[:MAX_LEN], "seconds": secs})
    else:
        for line in str(value or "").splitlines():
            text, secs = line.strip(), None
            if "|" in text:
                head, _, tail = text.rpartition("|")
                parsed = to_seconds(tail.strip())
                if parsed is not None:
                    text, secs = head.strip(), parsed
            if text:
                items.append({"text": text[:MAX_LEN], "seconds": secs})
    return items


def read_config_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def load_config():
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    data = read_config_file(CONFIG_PATH)
    migrated = False
    if data is None and not os.path.exists(CONFIG_PATH):
        data = read_config_file(OLD_CONFIG_PATH)  # ترحيل من المكان القديم
        migrated = data is not None
    if data is not None:
        cfg["token"] = str(data.get("token", "") or "")
        cfg["default_interval"] = to_seconds(data.get("default_interval")) or 3
        cfg["min_interval"] = to_seconds(data.get("min_interval")) or DEFAULT_MIN_INTERVAL
        cfg["shuffle"] = bool(data.get("shuffle", False))
        cfg["auto_run"] = bool(data.get("auto_run", True))
        cfg["start_with_windows"] = bool(data.get("start_with_windows", True))
        cfg["tray"] = bool(data.get("tray", True))
        if "statuses" in data:
            cfg["statuses"] = normalize_statuses(data["statuses"])
        if migrated:
            save_config(cfg)  # يتكتب في AppData
    return cfg


def save_config(cfg):
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"  # نكتب في ملف مؤقت الأول عشان الكونفج ما يتبوظش لو حصل crash
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
        return True
    except OSError:
        return False


# ───────────────────────── Discord ─────────────────────────
def set_status(token, text, stop_event=None):
    """بيرجع (status_code, ok). text=None يعني مسح الـ status."""
    headers = {"authorization": token, "content-type": "application/json"}
    body = {"custom_status": {"text": text}} if text else {"custom_status": None}
    for _ in range(3):
        try:
            r = requests.patch(API_URL, headers=headers, json=body, timeout=8)
        except (requests.RequestException, UnicodeError):
            return None, False
        if r.status_code == 429:  # rate limit: نستنى المدة اللي Discord طلبها
            try:
                delay = min(float(r.json().get("retry_after", 5)), 30)
            except Exception:
                delay = 5
            if stop_event is not None:
                if stop_event.wait(delay):
                    return 429, False
            else:
                time.sleep(delay)
            continue
        return r.status_code, r.ok
    return 429, False


def check_token(token):
    """بيتأكد إن التوكن شغال. بيرجع (state, info):
    ("valid", اسم_الحساب) / ("invalid", سبب) / ("error", سبب_تعذر_الفحص)"""
    if not token:
        return "invalid", "Token is empty"
    if any(c.isspace() for c in token):
        return "invalid", "Token contains spaces or line breaks"
    try:
        token.encode("latin-1")
    except UnicodeEncodeError:
        return "invalid", "Token contains invalid characters"
    try:
        r = requests.get(ME_URL, headers={"authorization": token}, timeout=8)
    except requests.RequestException:
        return "error", "No connection to Discord"
    if r.status_code == 200:
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        return "valid", data.get("global_name") or data.get("username") or "unknown"
    if r.status_code == 401:
        return "invalid", ""
    if r.status_code == 429:
        return "error", "Rate limited, try again in a few seconds"
    return "error", f"Unexpected response ({r.status_code})"


# ───────────────────────── التشغيل مع ويندوز ─────────────────────────
IS_WINDOWS = sys.platform == "win32"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "StatusRotator"
MUTEX_NAME = "Local\\StatusRotator_SingleInstance"
_mutex_handle = None

try:
    from PIL import Image, ImageDraw
except Exception:  # Pillow مش متثبتة
    Image = ImageDraw = None
try:
    import pystray
except Exception:  # pystray مش متثبتة (أو النظام مش مدعوم)
    pystray = None
HAS_TRAY = bool(IS_WINDOWS and pystray and Image)  # أيقونة التراي: pip install pystray pillow


def startup_command():
    """الأمر اللي ويندوز هيشغّله عند الدخول (من غير نافذة cmd)."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --startup'
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.exists(pyw):
        exe = pyw
    return f'"{exe}" "{os.path.abspath(__file__)}" --startup'


def get_startup_entry():
    """القيمة المسجّلة حالياً في الـ Startup، أو None."""
    if not IS_WINDOWS:
        return None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            return winreg.QueryValueEx(key, RUN_NAME)[0]
    except (ImportError, OSError):
        return None


def set_startup(enabled):
    """بيضيف/يشيل البرنامج من الـ Startup (HKCU\\...\\Run، من غير صلاحيات أدمن)."""
    if not IS_WINDOWS:
        return False
    try:
        import winreg
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                                winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, startup_command())
            else:
                try:
                    winreg.DeleteValue(key, RUN_NAME)
                except FileNotFoundError:
                    pass
        return True
    except (ImportError, OSError):
        return False


def acquire_single_instance():
    """بيرجّع False لو فيه نسخة تانية شغالة (عشان ما يشتغلوش اتنين يتخانقوا على الـ status)."""
    global _mutex_handle
    if not IS_WINDOWS:
        return True
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p]
        _mutex_handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def show_flag_path():
    return os.path.join(os.path.dirname(CONFIG_PATH), "show.flag")


def request_show_existing():
    """النسخة التانية بتطلب من الأولى تطلّع نافذتها (بملف علامة النسخة الأولى بتشوفه)."""
    try:
        os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
        open(show_flag_path(), "w").close()
    except OSError:
        pass


def make_tray_image(running=False, size=64):
    """أيقونة البرنامج: مربع دايري بلون Discord + سهم دوران.
    running=True نقطة خضرا، False نقطة رمادي، None من غير نقطة."""
    s = size * 4  # نرسم بحجم أكبر وبعدين نصغّر عشان الحواف تطلع ناعمة
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.24), fill=(*hex_rgb(ACCENT), 255))
    pad = int(s * 0.25)
    d.arc((pad, pad, s - pad, s - pad), start=40, end=320, fill=white, width=int(s * 0.10))
    cx = cy = s / 2
    r = cx - pad
    th = math.radians(320)  # نهاية القوس: هنا بنحط راس السهم
    px, py = cx + r * math.cos(th), cy + r * math.sin(th)
    tx, ty = -math.sin(th), math.cos(th)
    nx, ny = math.cos(th), math.sin(th)
    length, half = s * 0.20, s * 0.14
    d.polygon([(px + tx * length, py + ty * length),
               (px + nx * half, py + ny * half),
               (px - nx * half, py - ny * half)], fill=white)
    if running is not None:
        dot, ring = s * 0.17, s * 0.04
        dx = dy = s * 0.76
        d.ellipse((dx - dot - ring, dy - dot - ring, dx + dot + ring, dy + dot + ring),
                  fill=(*hex_rgb(BG), 255))
        d.ellipse((dx - dot, dy - dot, dx + dot, dy + dot),
                  fill=(*hex_rgb(GREEN if running else MUTED), 255))
    return img.resize((size, size), getattr(getattr(Image, "Resampling", Image), "LANCZOS"))


# ───────────────────────── مساعدات الواجهة ─────────────────────────
def enable_hidpi():
    """عشان الخط يبقى حاد على شاشات الـ High DPI في ويندوز."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def dark_titlebar(win):
    """شريط عنوان غامق في ويندوز 10/11."""
    try:
        win.update()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1)
        for attr in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:
        pass


def shorten(text, n=60):
    return text if len(text) <= n else text[: n - 1] + "…"


def hex_rgb(c):
    c = c.lstrip("#")
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def rgb_hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(round(v)))) for v in c)


def mix(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def ease_out(t):
    return 1 - (1 - t) ** 3


def ease_spring(t):
    """نبضة: بتعدّي الهدف شوية وبعدين ترجع."""
    return 1 - math.exp(-5 * t) * math.cos(12 * t)


class Btn(tk.Canvas):
    """زرار بحواف دايرية: الـ hover بيتدرّج، والضغط بيصغّره وبعدين يرجع بنبضة."""

    def __init__(self, parent, text, command, bg=SURFACE, hover=BORDER, fg=TEXT,
                 padx=14, pady=7, radius=10, reserve=(), **kw):
        s = max(parent.winfo_fpixels("1i") / 96.0, 1.0)
        font = tkfont.Font(root=parent, font=FONT_B)
        pad = max(int(round(2 * s)), 2)  # هامش داخلي عشان النبضة ما تتقصش
        width = max(font.measure(t) for t in (text, *reserve)) + 2 * int(padx * s) + 2 * pad
        height = font.metrics("linespace") + 2 * int(pady * s) + 2 * pad
        super().__init__(parent, width=width, height=height, bg=parent.cget("bg"), bd=0,
                         highlightthickness=0, cursor="hand2", takefocus=1, **kw)
        # ملحوظة: ما نستخدمش الاسم _w لأن Tkinter بيستخدمه لمسار الـ widget
        self._bw, self._bh, self._pad, self._scale = width, height, pad, s
        self._radius = radius * s
        self._cmd = command
        self._base, self._hover, self._fg = hex_rgb(bg), hex_rgb(hover), hex_rgb(fg)
        self._press = mix(self._hover, (0, 0, 0), 0.22)
        self._cbg, self._cfg, self._inset = self._base, self._fg, 0.0
        self._enabled, self._hovering, self._pressed, self._job = True, False, False, None

        self._shape = self.create_polygon(*self._pts(), smooth=True, splinesteps=24,
                                          fill=rgb_hex(self._cbg), outline="")
        self._label = self.create_text(width / 2, height / 2, text=text, font=FONT_B,
                                       fill=rgb_hex(self._cfg))
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Configure>", self._on_resize)
        self.bind("<space>", self._on_key)
        self.bind("<Return>", self._on_key)
        self.bind("<FocusIn>", lambda e: self.itemconfig(self._shape, outline=TEXT, width=2))
        self.bind("<FocusOut>", lambda e: self.itemconfig(self._shape, outline="", width=1))

    # ---------- الرسم ----------
    def _pts(self):
        p = self._pad + self._inset
        x1, y1, x2, y2 = p, p, self._bw - p, self._bh - p
        r = max(min(self._radius, (x2 - x1) / 2, (y2 - y1) / 2), 1)
        return [x1 + r, y1, x1 + r, y1, x2 - r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                x2, y1 + r, x2, y2 - r, x2, y2 - r, x2, y2, x2 - r, y2, x2 - r, y2,
                x1 + r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y2 - r, x1, y1 + r,
                x1, y1 + r, x1, y1]

    def _paint(self):
        self.coords(self._shape, *self._pts())
        self.itemconfig(self._shape, fill=rgb_hex(self._cbg))
        self.coords(self._label, self._bw / 2,
                    self._bh / 2 + (self._scale if self._pressed else 0))
        self.itemconfig(self._label, fill=rgb_hex(self._cfg))

    def _tween(self, bg=None, fg=None, inset=None, ms=140, inset_ease=ease_out):
        """بينقل اللون والحجم للهدف بشكل ناعم (ms=0 يعني فوراً)."""
        if self._job is not None:
            self.after_cancel(self._job)
            self._job = None
        sb, sf, si = self._cbg, self._cfg, self._inset
        eb, ef = bg or sb, fg or sf
        ei = si if inset is None else inset * self._scale
        t0 = time.perf_counter()

        def step():
            t = 1.0 if ms <= 0 else min((time.perf_counter() - t0) * 1000.0 / ms, 1.0)
            k = ease_out(t) if t < 1.0 else 1.0
            ki = inset_ease(t) if t < 1.0 else 1.0
            self._cbg, self._cfg = mix(sb, eb, k), mix(sf, ef, k)
            self._inset = si + (ei - si) * ki
            self._paint()
            self._job = self.after(16, step) if t < 1.0 else None

        step()

    # ---------- الأحداث ----------
    def _rest_color(self):
        return self._hover if self._hovering else self._base

    def _on_enter(self, _):
        self._hovering = True
        if self._enabled and not self._pressed:
            self._tween(bg=self._hover, ms=130)

    def _on_leave(self, _):
        self._hovering = False
        self._pressed = False
        if self._enabled:
            self._tween(bg=self._base, inset=0, ms=170)

    def _on_press(self, _):
        if not self._enabled:
            return
        self._pressed = True
        self._tween(bg=self._press, inset=3, ms=70)

    def _on_release(self, e):
        if not self._enabled or not self._pressed:
            return
        self._pressed = False
        inside = 0 <= e.x < self._bw and 0 <= e.y < self._bh
        self._tween(bg=self._rest_color() if inside else self._base, inset=0, ms=320,
                    inset_ease=ease_spring)
        if inside and self._cmd:
            self._cmd()

    def _on_key(self, _):
        if self._enabled:
            self._pressed = True
            self._tween(bg=self._press, inset=3, ms=60)
            self.after(90, self._key_release)

    def _key_release(self):
        if self._enabled and self._pressed:
            self._pressed = False
            self._tween(bg=self._rest_color(), inset=0, ms=320, inset_ease=ease_spring)
            if self._cmd:
                self._cmd()

    def _on_resize(self, e):
        if e.width > 4 and e.height > 4:
            self._bw, self._bh = e.width, e.height
            self._paint()

    # ---------- واجهة الاستخدام ----------
    def configure(self, cnf=None, **kw):
        if "text" in kw:
            self.itemconfig(self._label, text=kw.pop("text"))
        if cnf or kw:
            return super().configure(cnf, **kw)

    config = configure

    def set_enabled(self, on, animate=True):
        self._enabled = bool(on)
        self._pressed = False
        super().configure(cursor="hand2" if on else "arrow")
        ms = 200 if animate else 0
        if on:
            self._tween(bg=self._rest_color(), fg=self._fg, inset=0, ms=ms)
        else:
            self._tween(bg=hex_rgb(DISABLED), fg=hex_rgb(MUTED), inset=0, ms=ms)

    def flash(self, color, hold=1000):
        """وميض لون سريع (مثلاً بعد الحفظ) وبعدين يرجع."""
        if self._enabled:
            self._tween(bg=hex_rgb(color), ms=150)
            self.after(hold, self._unflash)

    def _unflash(self):
        if self._enabled and not self._pressed:
            self._tween(bg=self._rest_color(), ms=350)

    def destroy(self):
        if self._job is not None:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        super().destroy()


# ───────────────────────── البرنامج ─────────────────────────
class App:
    def __init__(self, root, start_hidden=False):
        self.root = root
        self.scale = max(root.winfo_fpixels("1i") / 96.0, 1.0)
        cfg = load_config()

        self.items = cfg["statuses"]
        self.default_interval = cfg["default_interval"]
        self.min_interval = cfg["min_interval"]
        self.shuffle = cfg["shuffle"]
        self.edit_index = None

        self.q = queue.Queue()
        self.thread = None
        self.stop_event = threading.Event()
        self.running = False
        self.error = False
        self.cur_start = None
        self.cur_dur = 1.0
        self.warn_text = ""
        self.checking = False
        self.verified_token = None
        self.auto_job = None
        self.retry_job = None
        self.pulsing = False
        self.closing = False        # بنقفل: الـ loops بتبطّل تتجدول
        self.tray_icon = None
        self.tray_ready = False     # الأيقونة ظهرت فعلاً (قبل كده ما نخفيش النافذة)
        self.tray_hint_shown = False
        self.tray_pref = cfg["tray"]
        self._tray_state = None
        self._tray_images = {}
        self.tick_n = 0

        root.title("Status Rotator")
        root.configure(bg=BG)
        self.set_window_icon()
        self.setup_styles()
        self.build(cfg)
        self.refresh_list()
        self.fit_window()

        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Control-s>", lambda e: self.save())
        self.tick()
        self.animate()
        self.sync_startup_entry()
        try:
            os.remove(show_flag_path())  # علامة قديمة من نسخة سابقة
        except OSError:
            pass
        if self.tray_var.get():
            self.start_tray()
        if start_hidden:
            if self.tray_icon:
                self.root.after(2500, self.verify_hidden_start)
            else:
                self.root.iconify()  # مفيش تراي: نبدأ مصغّر في الـ taskbar بدل ما نختفي
        token_ok = len(self.get_token()) >= MIN_TOKEN_LEN
        if cfg["auto_run"] and token_ok and self.items:
            # تشغيل تلقائي: نفحص التوكن وبعدين نبدأ التبديل
            self.root.after(500, lambda: self.verify(then_start="auto"))
        elif token_ok:
            self.schedule_auto_verify(400)

    # ---------- البناء ----------
    def setup_styles(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure("Treeview", background=INPUT, fieldbackground=INPUT, foreground=TEXT,
                    borderwidth=0, rowheight=int(30 * self.scale), font=FONT)
        s.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
        s.configure("Treeview.Heading", background=CARD, foreground=MUTED, borderwidth=0,
                    relief="flat", font=FONT_SB, padding=(8, 6))
        s.map("Treeview.Heading", background=[("active", CARD)])
        s.configure("Vertical.TScrollbar", background=SURFACE, troughcolor=INPUT,
                    bordercolor=INPUT, arrowcolor=MUTED, lightcolor=SURFACE,
                    darkcolor=SURFACE, relief="flat")
        s.map("Vertical.TScrollbar", background=[("active", BORDER)])
        s.configure("Accent.Horizontal.TProgressbar", troughcolor=INPUT, background=ACCENT,
                    bordercolor=CARD, lightcolor=ACCENT, darkcolor=ACCENT,
                    thickness=int(6 * self.scale))

    def entry(self, parent, **kw):
        return tk.Entry(
            parent, bg=INPUT, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0, font=FONT,
            highlightthickness=1, highlightbackground=BORDER, highlightcolor=ACCENT,
            selectbackground=ACCENT, selectforeground="#ffffff", **kw,
        )

    def card(self, parent, title=None, **pack):
        pack.setdefault("fill", "x")
        box = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        box.pack(**pack)
        if title:
            tk.Label(box, text=title, font=FONT_SB, bg=CARD, fg=MUTED).pack(
                anchor="w", padx=14, pady=(10, 0))
        inner = tk.Frame(box, bg=CARD)
        inner.pack(fill="both", expand=True, padx=14, pady=(6 if title else 12, 12))
        return inner

    def build(self, cfg):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="both", expand=True, padx=18, pady=16)

        # الأجزاء السفلية بتتحط الأول عشان ما تتقصش لو الشاشة صغيرة
        foot = tk.Frame(outer, bg=BG)
        foot.pack(side="bottom", fill="x", pady=(12, 0))
        self.start_btn = Btn(foot, "▶  Start", self.start, bg=GREEN, hover=GREEN_H, pady=10)
        self.start_btn.pack(side="left", fill="x", expand=True)
        self.stop_btn = Btn(foot, "■  Stop", self.stop, bg=RED, hover=RED_H, pady=10)
        self.stop_btn.pack(side="left", fill="x", expand=True, padx=8)
        self.save_btn = Btn(foot, "Save", lambda: self.save(), pady=10, reserve=("Saved ✓",))
        self.save_btn.pack(side="left")
        self.stop_btn.set_enabled(False, animate=False)

        now = self.card(outer, side="bottom", pady=(12, 0))
        line = tk.Frame(now, bg=CARD)
        line.pack(fill="x")
        self.now_label = tk.Label(line, text="Nothing running", font=FONT_B, bg=CARD,
                                  fg=MUTED, anchor="w")
        self.now_label.pack(side="left", fill="x", expand=True)
        self.next_label = tk.Label(line, text="", font=FONT_S, bg=CARD, fg=MUTED)
        self.next_label.pack(side="right")
        self.progress = ttk.Progressbar(now, style="Accent.Horizontal.TProgressbar", maximum=100)
        self.progress.pack(fill="x", pady=(8, 0))

        # الهيدر
        head = tk.Frame(outer, bg=BG)
        head.pack(fill="x")
        titles = tk.Frame(head, bg=BG)
        titles.pack(side="left")
        tk.Label(titles, text="Status Rotator", font=FONT_H, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(titles, text="Rotate your Discord custom status", font=FONT_S,
                 bg=BG, fg=MUTED).pack(anchor="w")
        self.pill = tk.Frame(head, bg=CARD, padx=12, pady=5)
        self.pill.pack(side="right")
        self.pill_dot = tk.Label(self.pill, text="●", font=FONT_B, bg=CARD, fg=MUTED)
        self.pill_dot.pack(side="left")
        self.pill_text = tk.Label(self.pill, text="Stopped", font=FONT_B, bg=CARD, fg=MUTED)
        self.pill_text.pack(side="left", padx=(6, 0))

        # التوكن
        acc = self.card(outer, "DISCORD TOKEN", pady=(14, 0))
        row = tk.Frame(acc, bg=CARD)
        row.pack(fill="x")
        self.token_var = tk.StringVar(value=cfg["token"])
        self.token_entry = self.entry(row, textvariable=self.token_var, show="•")
        self.token_entry.pack(side="left", fill="x", expand=True, ipady=6)
        self.show_btn = Btn(row, "Show", self.toggle_token, reserve=("Hide",))
        self.show_btn.pack(side="left", padx=(8, 0))
        self.check_btn = Btn(row, "Check", lambda: self.verify())
        self.check_btn.pack(side="left", padx=(8, 0))
        self.token_status = tk.Label(acc, text="Not checked yet", font=FONT_S, bg=CARD,
                                     fg=MUTED, anchor="w")
        self.token_status.pack(fill="x", pady=(6, 0))

        # الجمل
        st = self.card(outer, "STATUSES", pady=(12, 0), fill="both", expand=True)
        if IS_WINDOWS and not HAS_TRAY:
            tk.Label(st, text="System tray needs:  pip install pystray pillow", font=FONT_S,
                     bg=CARD, fg=MUTED, anchor="w").pack(side="bottom", fill="x", pady=(6, 0))
        opts = tk.Frame(st, bg=CARD)
        opts.pack(side="bottom", fill="x", pady=(8, 0))
        sett = tk.Frame(st, bg=CARD)
        sett.pack(side="bottom", fill="x", pady=(10, 0))
        tools = tk.Frame(st, bg=CARD)
        tools.pack(side="bottom", fill="x", pady=(10, 0))
        add = tk.Frame(st, bg=CARD)
        add.pack(side="top", fill="x")
        listwrap = tk.Frame(st, bg=INPUT, highlightbackground=BORDER, highlightthickness=1)
        listwrap.pack(side="top", fill="both", expand=True, pady=(10, 0))

        add.columnconfigure(0, weight=1)
        tk.Label(add, text="Status text", font=FONT_S, bg=CARD, fg=MUTED).grid(
            row=0, column=0, sticky="w")
        tk.Label(add, text="Seconds (optional)", font=FONT_S, bg=CARD, fg=MUTED).grid(
            row=0, column=1, sticky="w", padx=(8, 0))
        self.text_entry = self.entry(add)
        self.text_entry.grid(row=1, column=0, sticky="ew", ipady=6, pady=(3, 0))
        self.sec_entry = self.entry(add, width=9)
        self.sec_entry.grid(row=1, column=1, padx=(8, 0), ipady=6, pady=(3, 0))
        self.add_btn = Btn(add, "Add", self.add_item, bg=ACCENT, hover=ACCENT_H,
                           reserve=("Update",))
        self.add_btn.grid(row=1, column=2, padx=(8, 0), pady=(3, 0))
        for w in (self.text_entry, self.sec_entry):
            w.bind("<Return>", lambda e: self.add_item())
            w.bind("<Escape>", lambda e: self.reset_form())

        self.tree = ttk.Treeview(listwrap, columns=("text", "sec"), show="headings",
                                 height=5, selectmode="browse")
        self.tree.heading("text", text="Status", anchor="w")
        self.tree.heading("sec", text="Duration", anchor="w")
        self.tree.column("text", anchor="w", stretch=True, width=int(330 * self.scale))
        self.tree.column("sec", anchor="w", stretch=False, width=int(110 * self.scale))
        sb = ttk.Scrollbar(listwrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.bind("<Double-1>", self.begin_edit)
        self.tree.bind("<Delete>", lambda e: self.remove_item())

        Btn(tools, "Remove", self.remove_item, hover=RED).pack(side="left")
        Btn(tools, "↑", lambda: self.move(-1), padx=12).pack(side="left", padx=(8, 0))
        Btn(tools, "↓", lambda: self.move(1), padx=12).pack(side="left", padx=(8, 0))
        tk.Label(tools, text="Double-click a row to edit", font=FONT_S, bg=CARD,
                 fg=MUTED).pack(side="right")

        # صف المدة الافتراضية + الـ rate limit
        tk.Label(sett, text="Duration (s)", font=FONT, bg=CARD, fg=TEXT).pack(side="left")
        Btn(sett, "−", lambda: self.bump(-1), padx=10, pady=3).pack(side="left", padx=(8, 4))
        self.interval_var = tk.StringVar(value=f"{cfg['default_interval']:g}")
        self.entry(sett, textvariable=self.interval_var, width=5, justify="center").pack(
            side="left", ipady=4)
        Btn(sett, "+", lambda: self.bump(1), padx=10, pady=3).pack(side="left", padx=(4, 0))

        tk.Label(sett, text="Rate limit (s)", font=FONT, bg=CARD, fg=TEXT).pack(
            side="left", padx=(22, 0))
        Btn(sett, "−", lambda: self.bump_min(-0.5), padx=10, pady=3).pack(
            side="left", padx=(8, 4))
        self.min_var = tk.StringVar(value=f"{cfg['min_interval']:g}")
        self.entry(sett, textvariable=self.min_var, width=5, justify="center").pack(
            side="left", ipady=4)
        Btn(sett, "+", lambda: self.bump_min(0.5), padx=10, pady=3).pack(
            side="left", padx=(4, 0))

        # صف الخيارات
        self.shuffle_var = tk.BooleanVar(value=cfg["shuffle"])
        self.auto_run_var = tk.BooleanVar(value=cfg["auto_run"])
        self.startup_var = tk.BooleanVar(value=bool(cfg["start_with_windows"] and IS_WINDOWS))
        self.tray_var = tk.BooleanVar(value=bool(cfg["tray"] and HAS_TRAY))
        for label, var, state in (
                ("Shuffle", self.shuffle_var, "normal"),
                ("Start when opened", self.auto_run_var, "normal"),
                ("Launch with Windows", self.startup_var, "normal" if IS_WINDOWS else "disabled"),
                ("System tray", self.tray_var, "normal" if HAS_TRAY else "disabled")):
            tk.Checkbutton(opts, text=label, variable=var, font=FONT, bg=CARD, fg=TEXT,
                           selectcolor=INPUT, activebackground=CARD, activeforeground=TEXT,
                           disabledforeground=MUTED, highlightthickness=0, bd=0,
                           cursor="hand2", state=state).pack(side="left", padx=(0, 18))

        # التغييرات دي بتتطبق فوراً حتى والبرنامج شغال
        self.interval_var.trace_add("write", self.sync_settings)
        self.min_var.trace_add("write", self.sync_settings)
        self.token_var.trace_add("write", self.on_token_edit)
        self.shuffle_var.trace_add("write", self.sync_settings)
        self.startup_var.trace_add("write", self.on_startup_toggle)
        self.tray_var.trace_add("write", self.on_tray_toggle)

    def fit_window(self):
        r = self.root
        r.update_idletasks()
        req_w, req_h = r.winfo_reqwidth(), r.winfo_reqheight()
        avail = r.winfo_screenheight() - int(90 * self.scale)
        r.geometry(f"{max(req_w, int(620 * self.scale))}x{min(req_h, avail)}")
        r.minsize(req_w, min(req_h - int(90 * self.scale), avail))

    # ---------- الإعدادات ----------
    def parse_interval(self):
        return to_seconds(self.interval_var.get())

    def parse_min(self):
        return to_seconds(self.min_var.get())

    def sync_settings(self, *_):
        v = self.parse_interval()
        if v is not None:
            self.default_interval = v
        m = self.parse_min()
        if m is not None:
            self.min_interval = m
        self.shuffle = bool(self.shuffle_var.get())
        self.refresh_list(self.selected_index())  # عشان العمود يعرض المدة الفعلية

    def bump(self, delta):
        cur = self.parse_interval() or self.default_interval
        self.interval_var.set(f"{max(cur + delta, ABS_MIN):g}")

    def bump_min(self, delta):
        cur = self.parse_min() or self.min_interval
        self.min_var.set(f"{max(cur + delta, ABS_MIN):g}")

    def persist(self):
        return save_config({
            "token": self.get_token(),
            "default_interval": self.parse_interval() or self.default_interval,
            "min_interval": self.parse_min() or self.min_interval,
            "shuffle": bool(self.shuffle_var.get()),
            "auto_run": bool(self.auto_run_var.get()),
            "start_with_windows": bool(self.startup_var.get()),
            "tray": bool(self.tray_var.get()) if HAS_TRAY else self.tray_pref,
            "statuses": self.items,
        })

    def save(self, flash=True):
        if self.parse_interval() is None or self.parse_min() is None:
            messagebox.showwarning("Invalid value",
                                   "Default duration and rate limit must be numbers.")
            return False
        if not self.persist():
            messagebox.showerror("Couldn't save", f"Unable to write:\n{CONFIG_PATH}")
            return False
        if flash:
            self.save_btn.config(text="Saved ✓")
            self.save_btn.flash(GREEN, 1000)
            self.root.after(1200, lambda: self.save_btn.config(text="Save"))
        return True

    def toggle_token(self):
        hidden = self.token_entry.cget("show") != ""
        self.token_entry.config(show="" if hidden else "•")
        self.show_btn.config(text="Hide" if hidden else "Show")

    def get_token(self):
        # بنشيل المسافات وعلامات التنصيص اللي بتيجي مع النسخ من الـ console
        return self.token_var.get().strip().strip("\"'").strip()

    def set_token_status(self, text, color=MUTED):
        self.token_status.config(text=text, fg=color)

    def on_token_edit(self, *_):
        self.verified_token = None
        self.cancel_auto()
        self.cancel_retry()
        if len(self.get_token()) >= MIN_TOKEN_LEN:
            self.set_token_status("Checking…")
            self.schedule_auto_verify(700)  # نستنى المستخدم يخلّص لصق/كتابة
        else:
            self.set_token_status("Not checked yet")

    def cancel_auto(self):
        if self.auto_job is not None:
            self.root.after_cancel(self.auto_job)
            self.auto_job = None

    def schedule_auto_verify(self, delay_ms):
        self.cancel_auto()
        self.auto_job = self.root.after(delay_ms, self.auto_verify)

    def auto_verify(self):
        self.auto_job = None
        token = self.get_token()
        if len(token) < MIN_TOKEN_LEN or self.verified_token == token:
            return
        if self.checking:  # فيه فحص شغال: نستنى ونعيد على التوكن الجديد
            self.schedule_auto_verify(300)
            return
        self.verify()

    def cancel_retry(self):
        if self.retry_job is not None:
            self.root.after_cancel(self.retry_job)
            self.retry_job = None

    def retry_auto_run(self):
        """لو النت لسه مش جاهز وقت التشغيل التلقائي (مثلاً عند الـ Startup) نعيد المحاولة."""
        self.retry_job = None
        if self.running:
            return
        if self.checking:
            self.retry_job = self.root.after(1000, self.retry_auto_run)
        elif len(self.get_token()) >= MIN_TOKEN_LEN and self.items:
            self.verify(then_start="auto")

    def verify(self, then_start=False):
        """بيفحص التوكن في الخلفية. then_start=True: يبدأ بعد ما يتأكد (مع رسايل لو فيه مشكلة).
        then_start="auto": تشغيل تلقائي عند الفتح (من غير رسايل، وبيعيد المحاولة لو النت مش جاهز)."""
        if self.checking:
            return
        token = self.get_token()
        if not token:
            messagebox.showwarning("Token missing", "Paste your Discord user token first.")
            return
        self.cancel_auto()
        self.checking = True
        self.check_btn.set_enabled(False)
        self.start_btn.set_enabled(False)
        self.set_token_status("Checking…")

        def job():
            state, info = check_token(token)
            self.q.put(("check", token, state, info, then_start))

        threading.Thread(target=job, daemon=True).start()

    def on_check_result(self, token, state, info, then_start):
        self.checking = False
        self.check_btn.set_enabled(True)
        self.start_btn.set_enabled(not self.running)
        if token != self.get_token():  # المستخدم غيّر التوكن أثناء الفحص
            return
        auto = then_start == "auto"  # تشغيل تلقائي عند فتح البرنامج
        if state == "valid":
            self.verified_token = token
            self.set_token_status(f"✓ Valid — logged in as {info}", GREEN)
            if then_start and not self.running:
                self.begin(token)
        elif state == "invalid":
            self.verified_token = None
            self.set_token_status("✗ Invalid token" + (f" ({info})" if info else ""), RED)
            if auto:
                self.show_window()  # لو البرنامج مخفي/مصغّر نطلّعه عشان تلاحظ المشكلة
            elif then_start:
                messagebox.showerror(
                    "Invalid token",
                    info or "Discord rejected this token. Check it and try again.")
        elif auto:  # مفيش نت لسه: نعيد المحاولة بهدوء
            self.verified_token = None
            self.set_token_status(f"⚠ Waiting for connection… ({info})", WARN)
            self.retry_job = self.root.after(5000, self.retry_auto_run)
        else:
            self.verified_token = None
            self.set_token_status(f"⚠ Couldn't verify: {info}", WARN)
            if then_start and messagebox.askyesno(
                    "Couldn't verify token", f"{info}.\n\nStart anyway?"):
                self.begin(token)

    # ---------- قايمة الجمل ----------
    def refresh_list(self, select=None):
        self.tree.delete(*self.tree.get_children())
        floor = self.min_interval
        for i, it in enumerate(self.items):
            if it["seconds"]:
                secs = f"{it['seconds']:g}s"
                if it["seconds"] < floor:
                    secs += f" → {floor:g}s"  # المدة اترفعت للحد الأدنى
            else:
                secs = f"default ({max(self.default_interval, floor):g}s)"
            self.tree.insert("", "end", iid=str(i), values=(it["text"], secs))
        if select is not None and 0 <= select < len(self.items):
            self.tree.selection_set(str(select))
            self.tree.see(str(select))

    def selected_index(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def reset_form(self):
        self.edit_index = None
        self.text_entry.delete(0, "end")
        self.sec_entry.delete(0, "end")
        self.add_btn.config(text="Add")

    def add_item(self):
        text = self.text_entry.get().strip()
        if not text:
            return
        if len(text) > MAX_LEN:
            messagebox.showwarning("Too long", f"Max {MAX_LEN} characters (yours: {len(text)}).")
            return
        raw = self.sec_entry.get().strip()
        secs = None
        if raw:
            secs = to_seconds(raw)
            if secs is None:
                messagebox.showwarning("Invalid duration",
                                       "Seconds must be a number, or leave it empty for the default.")
                return
        item = {"text": text, "seconds": secs}
        if self.edit_index is not None and self.edit_index < len(self.items):
            self.items[self.edit_index] = item
            select = self.edit_index
        else:
            self.items.append(item)
            select = len(self.items) - 1
        self.reset_form()
        self.refresh_list(select)
        self.text_entry.focus_set()

    def begin_edit(self, event):
        row = self.tree.identify_row(event.y)
        if not row:
            return
        i = int(row)
        it = self.items[i]
        self.edit_index = i
        self.text_entry.delete(0, "end")
        self.text_entry.insert(0, it["text"])
        self.sec_entry.delete(0, "end")
        if it["seconds"]:
            self.sec_entry.insert(0, f"{it['seconds']:g}")
        self.add_btn.config(text="Update")
        self.text_entry.focus_set()

    def remove_item(self):
        i = self.selected_index()
        if i is None:
            return
        del self.items[i]
        if self.edit_index is not None:
            self.reset_form()
        self.refresh_list(min(i, len(self.items) - 1) if self.items else None)

    def move(self, delta):
        i = self.selected_index()
        if i is None:
            return
        j = i + delta
        if not 0 <= j < len(self.items):
            return
        self.items[i], self.items[j] = self.items[j], self.items[i]
        if self.edit_index is not None:
            self.reset_form()
        self.refresh_list(j)

    # ---------- التشغيل ----------
    def set_pill(self, text, color):
        self.pulsing = text == "Running"
        self.pill_dot.config(fg=color)
        self.pill_text.config(text=text, fg=color)

    def start(self):
        token = self.get_token()
        if not token:
            messagebox.showwarning("Token missing", "Paste your Discord user token first.")
            return
        if not self.items:
            messagebox.showwarning("No statuses", "Add at least one status.")
            return
        if not self.save(flash=False):
            return
        if self.verified_token == token:  # اتفحص قبل كده
            self.begin(token)
        else:
            self.verify(then_start=True)

    def begin(self, token):
        self.cancel_retry()
        self.sync_settings()
        self.error = False
        self.warn_text = ""
        self.cur_start = None
        self.progress["value"] = 0
        self.now_label.config(text="Starting…", fg=MUTED)
        self.next_label.config(text="")
        self.stop_event = threading.Event()
        self.running = True
        self.start_btn.set_enabled(False)
        self.stop_btn.set_enabled(True)
        self.set_pill("Running", GREEN)
        self.update_tray()
        self.thread = threading.Thread(target=self.worker, args=(token, self.stop_event),
                                       daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_btn.set_enabled(False)
        self.set_pill("Stopping…", MUTED)
        self.stop_event.set()

    def worker(self, token, stop):
        index, bag, auth_failed = 0, [], False
        while not stop.is_set():
            items = [(i["text"], i["seconds"]) for i in list(self.items)]
            if not items:
                stop.wait(0.5)
                continue
            if self.shuffle:
                if not bag:
                    bag = items[:]
                    random.shuffle(bag)
                text, secs = bag.pop()
            else:
                text, secs = items[index % len(items)]
                index += 1
            dur = max(secs or self.default_interval, self.min_interval)

            code, ok = set_status(token, text, stop)
            if code == 401:
                auth_failed = True
                self.q.put(("error",))
                break
            if ok:
                self.q.put(("now", text, dur, time.time()))
            else:
                self.q.put(("warn", f"Request failed ({code or 'no connection'})"))
            stop.wait(dur)

        if not auth_failed:
            set_status(token, None)  # مسح الـ status عند الإيقاف
        self.q.put(("stopped",))

    def on_stopped(self):
        self.running = False
        self.cur_start = None
        self.progress["value"] = 0
        self.now_label.config(text="Nothing running", fg=MUTED)
        self.next_label.config(text="")
        self.start_btn.set_enabled(True)
        self.stop_btn.set_enabled(False)
        if self.error:
            self.set_pill("Invalid token", RED)
        else:
            self.set_pill("Stopped", MUTED)
        self.update_tray()

    def tick(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "now":
                    _, text, dur, started = msg
                    self.cur_dur, self.cur_start, self.warn_text = dur, started, ""
                    self.now_label.config(text=shorten(text), fg=TEXT)
                elif kind == "warn":
                    self.warn_text = msg[1]
                elif kind == "error":
                    self.error = True
                    self.verified_token = None
                    self.set_token_status("✗ Discord rejected the token", RED)
                elif kind == "check":
                    self.on_check_result(*msg[1:])
                elif kind == "tray":
                    self.on_tray_action(msg[1])
                elif kind == "stopped":
                    self.on_stopped()
                if self.closing:  # اتقفل (مثلاً Quit من التراي): نبطّل نكمل
                    return
        except queue.Empty:
            pass
        try:
            self.tick_n += 1
            if self.tick_n % 5 == 0:
                self.check_show_request()
            if self.running:
                if self.warn_text:
                    self.next_label.config(text=self.warn_text, fg=RED)
                elif self.cur_start:
                    elapsed = time.time() - self.cur_start
                    left = math.ceil(max(self.cur_dur - elapsed, 0))
                    self.next_label.config(text=f"next in {left}s", fg=MUTED)
        finally:
            if not self.closing:
                self.root.after(100, self.tick)

    def animate(self):
        """حلقة سريعة للحركات: شريط التقدم الناعم ونبض النقطة الخضرا."""
        try:
            if self.running and self.cur_start:
                self.progress["value"] = min(
                    (time.time() - self.cur_start) / self.cur_dur, 1) * 100
            if self.pulsing:
                phase = (math.sin(time.perf_counter() * 2 * math.pi / 1.6) + 1) / 2
                dim = mix(hex_rgb(GREEN), hex_rgb(CARD), 0.7 * (1 - phase))
                self.pill_dot.config(fg=rgb_hex(dim))
        finally:
            if not self.closing:
                self.root.after(33, self.animate)

    def sync_startup_entry(self):
        """بيخلّي تسجيل الـ Startup مطابق للـ checkbox (وبيصلّح المسار لو البرنامج اتنقل)."""
        if not IS_WINDOWS:
            return
        want, current = bool(self.startup_var.get()), get_startup_entry()
        if want and current != startup_command():
            if not set_startup(True):
                self.startup_var.set(False)
        elif not want and current is not None:
            set_startup(False)

    def on_startup_toggle(self, *_):
        want = bool(self.startup_var.get())
        if not set_startup(want) and want:
            messagebox.showwarning("Startup", "Couldn't add the app to Windows startup.")
            self.startup_var.set(False)

    # ---------- التراي ----------
    def set_window_icon(self):
        if Image is None:
            return
        try:
            from PIL import ImageTk
            self._win_icon = ImageTk.PhotoImage(make_tray_image(None, 64))  # لازم نحتفظ بمرجع
            self.root.iconphoto(True, self._win_icon)
        except Exception:
            pass

    def tray_title(self):
        return "Status Rotator — " + ("Running" if self.running else "Stopped")

    def start_tray(self):
        """بيعمل أيقونة في الـ system tray. بيرجّع True لو بدأت."""
        if self.tray_icon:
            return True
        if not HAS_TRAY:
            return False
        try:
            self._tray_images = {True: make_tray_image(True), False: make_tray_image(False)}
            menu = pystray.Menu(
                pystray.MenuItem("Open", lambda icon, item: self.q.put(("tray", "show")),
                                 default=True),
                pystray.MenuItem(
                    lambda item: "Stop rotation" if self.running else "Start rotation",
                    lambda icon, item: self.q.put(("tray", "toggle"))),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Quit", lambda icon, item: self.q.put(("tray", "quit"))),
            )
            icon = pystray.Icon("StatusRotator", self._tray_images[self.running],
                                self.tray_title(), menu)
        except Exception:
            return False

        def setup(ic):
            ic.visible = True
            self.tray_ready = True

        self.tray_icon, self._tray_state = icon, self.running
        threading.Thread(target=lambda: icon.run(setup), daemon=True).start()
        return True

    def stop_tray(self):
        icon, self.tray_icon, self.tray_ready = self.tray_icon, None, False
        if icon:
            try:
                icon.stop()
            except Exception:
                pass

    def update_tray(self):
        """بيحدّث شكل الأيقونة والتلميح والقايمة حسب حالة التشغيل."""
        if not self.tray_icon or self._tray_state == self.running:
            return
        self._tray_state = self.running
        try:
            self.tray_icon.icon = self._tray_images[self.running]
            self.tray_icon.title = self.tray_title()
            self.tray_icon.update_menu()
        except Exception:
            pass

    def tray_notify(self, text):
        try:
            self.tray_icon.notify(text, "Status Rotator")
        except Exception:
            pass

    def on_tray_toggle(self, *_):
        if self.tray_var.get():
            if not self.start_tray():
                messagebox.showwarning("System tray", "Couldn't create the tray icon.")
                self.tray_var.set(False)
        else:
            self.stop_tray()

    def on_tray_action(self, action):
        if action == "show":
            self.show_window()
        elif action == "toggle":
            self.tray_toggle()
        elif action == "quit":
            self.quit_app()

    def tray_toggle(self):
        if self.running:
            self.stop()
        elif len(self.get_token()) >= MIN_TOKEN_LEN and self.items:
            self.cancel_retry()
            self.verify(then_start="auto")
        else:
            self.show_window()  # ناقص توكن أو جمل: نفتح النافذة

    def show_window(self):
        r = self.root
        r.deiconify()
        r.state("normal")
        r.lift()
        r.attributes("-topmost", True)  # عشان تطلع قدّام حتى لو الطلب جه من الخلفية
        r.after(250, lambda: r.attributes("-topmost", False))
        r.focus_force()

    def hide_to_tray(self):
        self.persist()
        self.root.withdraw()
        if not self.tray_hint_shown:
            self.tray_hint_shown = True
            self.tray_notify("Still running in the tray. Right-click the icon to quit.")

    def verify_hidden_start(self):
        """بدأنا مخفيين والأيقونة ما ظهرتش: نصغّر النافذة عشان البرنامج ما يبقاش مخفي تماماً."""
        if not self.tray_ready and self.root.state() == "withdrawn":
            self.root.iconify()

    def check_show_request(self):
        """نسخة تانية اتفتحت وطلبت نطلّع النافذة."""
        flag = show_flag_path()
        if os.path.exists(flag):
            try:
                os.remove(flag)
            except OSError:
                pass
            self.show_window()

    # ---------- الإغلاق ----------
    def on_close(self):  # زرار X
        if self.tray_icon and self.tray_ready:
            self.hide_to_tray()
        else:
            self.quit_app()

    def quit_app(self):
        self.closing = True
        self.persist()
        self.stop_tray()
        if self.running:
            self.stop_event.set()
            if self.thread:
                self.thread.join(timeout=4)  # نستنى الـ status يتمسح
        self.root.destroy()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not acquire_single_instance():
        request_show_existing()  # النسخة الشغالة هتطلّع نافذتها
        return
    enable_hidpi()
    root = tk.Tk()
    start_hidden = "--startup" in argv
    if start_hidden:
        root.withdraw()  # من الـ Startup: من غير نافذة (الأيقونة في التراي بس)
    App(root, start_hidden=start_hidden)
    dark_titlebar(root)
    root.mainloop()


if __name__ == "__main__":
    main()