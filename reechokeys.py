"""
ReechoKeys - plays a sound every time you press a key, in ANY app.

* 34 built-in synthesized sounds in 5 categories
* Live effects: reverb, bass boost, pitch, humanize
* "Clean cut" mode so fast typing never turns into mush
* Add your own sounds (.wav / .ogg / .mp3 / .flac)
* F9 anywhere = mute / unmute.  Power switch + Quit button inside the app.

It only reacts to "a key was pressed". It never records or saves what you type.
"""
import json
import math
import os
import queue
import random
import shutil
import subprocess
import sys
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk
import numpy as np
import pygame
from pynput import keyboard

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:          # tray is optional; the app still works without it
    pystray = None
TRAY_OK = pystray is not None and sys.platform != "darwin"

SR = 44100
rng = np.random.default_rng()

CONFIG_DIR = Path.home() / ".reechokeys"
SOUNDS_DIR = CONFIG_DIR / "sounds"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
AUDIO_EXT = {".wav", ".ogg", ".mp3", ".flac"}
MAX_PLAY_MS = 3000
VARIANTS = 5
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"

# ---------- look ----------
BG, PANEL, CARD, CARD_H = "#0B0C10", "#14161C", "#1D2029", "#292D3A"
ACC, ACC_H = "#FF5A36", "#FF7452"
TXT, MUT, RED = "#F2F3F5", "#8A90A0", "#C73E2E"


# =====================================================================
#  DSP helpers (all vectorised, so building sounds takes milliseconds)
# =====================================================================
def tarr(dur):
    return np.arange(int(dur * SR)) / SR


def noise(n):
    return rng.uniform(-1, 1, n)


def env(t, tau):
    return np.exp(-t / tau)


def _lp(x, fc):
    """Zero-phase 2nd-order low-pass done in the frequency domain."""
    n = len(x)
    f = np.fft.rfftfreq(n, 1 / SR)
    h = 1 / np.sqrt(1 + (f / fc) ** 4)
    return np.fft.irfft(np.fft.rfft(x) * h, n)


def lowpass(x, fc):
    """fc may be a number or a per-sample array (a sweep)."""
    fc = np.asarray(fc, dtype=float)
    if fc.ndim == 0:
        return _lp(x, float(fc))
    lo = max(float(fc.min()), 20.0)
    hi = max(float(fc.max()), lo * 1.02)
    grid = np.geomspace(lo, hi, 10)
    bank = np.stack([_lp(x, g) for g in grid])
    pos = (np.log(np.clip(fc, lo, hi)) - np.log(lo)) / (np.log(hi) - np.log(lo)) * (len(grid) - 1)
    i0 = np.clip(pos.astype(int), 0, len(grid) - 2)
    fr = pos - i0
    idx = np.arange(len(x))
    return bank[i0, idx] * (1 - fr) + bank[i0 + 1, idx] * fr


def highpass(x, fc):
    return x - lowpass(x, fc)


def bandpass(x, lo, hi):
    return highpass(lowpass(x, hi), lo)


def sweep(t, f0, f1, tau):
    f = f1 + (f0 - f1) * np.exp(-t / tau)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def tone(t, f):
    return np.sin(2 * np.pi * f * t)


def echo(x, delays, decay=0.5):
    out = x.copy()
    for k, d in enumerate(delays, 1):
        s = int(d * SR)
        if s < len(x):
            out[s:] += x[: len(x) - s] * (decay ** k)
    return out


def at(base, x, when):
    """Mix x into base starting at `when` seconds."""
    s = int(when * SR)
    if s < len(base):
        m = min(len(x), len(base) - s)
        base[s:s + m] += x[:m]
    return base


# =====================================================================
#  Sound recipes - each returns a mono float array
# =====================================================================
# ---- guns ----
def shotgun():
    t = tarr(1.1)
    crack = highpass(noise(len(t)), rng.uniform(700, 1100)) * env(t, 0.035)
    body = lowpass(noise(len(t)), 180 + 4200 * np.exp(-t / 0.12)) * env(t, 0.16) * 1.4
    thump = sweep(t, rng.uniform(140, 170), 32, 0.06) * env(t, 0.13) * 1.8
    x = echo(crack + body + thump, [0.045, 0.09, 0.15, 0.23], 0.45)
    chk = bandpass(noise(int(0.12 * SR)), 1500, 4000) * env(tarr(0.12), 0.02) * 0.4
    return at(x, chk, 0.38)


def pistol():
    t = tarr(0.6)
    crack = highpass(noise(len(t)), rng.uniform(1100, 1500)) * env(t, 0.02)
    body = lowpass(noise(len(t)), 300 + 2800 * np.exp(-t / 0.05)) * env(t, 0.07) * 1.1
    thump = sweep(t, 210, 60, 0.04) * env(t, 0.06) * 1.2
    return echo(crack + body + thump, [0.04, 0.09, 0.16], 0.35)


def revolver():
    t = tarr(0.85)
    crack = highpass(noise(len(t)), rng.uniform(800, 1000)) * env(t, 0.028)
    body = lowpass(noise(len(t)), 400 + 3200 * np.exp(-t / 0.07)) * env(t, 0.1) * 1.2
    thump = sweep(t, 150, 48, 0.06) * env(t, 0.09) * 1.6
    return echo(crack + body + thump, [0.05, 0.11, 0.2], 0.4)


def rifle():
    t = tarr(0.9)
    crack = highpass(noise(len(t)), rng.uniform(900, 1300)) * env(t, 0.025) * 1.2
    body = lowpass(noise(len(t)), 250 + 3500 * np.exp(-t / 0.07)) * env(t, 0.1)
    thump = sweep(t, 160, 45, 0.05) * env(t, 0.09) * 1.5
    return echo(crack + body + thump, [0.06, 0.13, 0.22], 0.4)


def machine_gun():
    t = tarr(0.22)
    crack = highpass(noise(len(t)), 1300) * env(t, 0.012)
    body = lowpass(noise(len(t)), 250 + 2500 * np.exp(-t / 0.03)) * env(t, 0.035)
    thump = sweep(t, 130, 50, 0.03) * env(t, 0.04)
    return crack + body + thump


def sniper():
    t = tarr(1.8)
    crack = highpass(noise(len(t)), 900) * env(t, 0.03) * 1.3
    body = lowpass(noise(len(t)), 150 + 3000 * np.exp(-t / 0.2)) * env(t, 0.22)
    thump = sweep(t, 120, 28, 0.09) * env(t, 0.2) * 1.8
    return echo(crack + body + thump, [0.12, 0.25, 0.42, 0.65], 0.5)


def silenced():
    t = tarr(0.3)
    puff = lowpass(noise(len(t)), 2600) * env(t, 0.03) * 0.9
    tick = highpass(noise(len(t)), 3500) * env(t, 0.008) * 0.35
    thump = sweep(t, 120, 60, 0.02) * env(t, 0.035) * 0.6
    return puff + tick + thump


def cannon():
    t = tarr(1.7)
    body = lowpass(noise(len(t)), 120 + 1300 * np.exp(-t / 0.25)) * env(t, 0.35) * 1.5
    thump = sweep(t, 90, 25, 0.12) * env(t, 0.3) * 2.2
    crack = highpass(noise(len(t)), 500) * env(t, 0.04)
    return echo(body + thump + crack, [0.1, 0.22, 0.4, 0.6], 0.5)


# ---- impact ----
def explosion():
    t = tarr(2.0)
    body = lowpass(noise(len(t)), 80 + 1800 * np.exp(-t / 0.4)) * env(t, 0.5) * 1.5
    thump = sweep(t, 70, 22, 0.15) * env(t, 0.4) * 2.0
    crack = highpass(noise(len(t)), 600) * env(t, 0.03)
    return echo(body + thump + crack, [0.08, 0.2, 0.4], 0.5)


def thunder():
    t = tarr(2.4)
    rumble = lowpass(noise(len(t)), 90 + 500 * np.exp(-t / 0.7)) * env(t, 0.9)
    rumble *= 1 + 0.5 * np.sin(2 * np.pi * rng.uniform(3, 6) * t)
    crack = highpass(noise(len(t)), 800) * env(t, 0.05) * 0.8
    return rumble * 2 + crack


def punch():
    t = tarr(0.3)
    thump = sweep(t, 110, 45, 0.03) * env(t, 0.05) * 1.6
    slap = lowpass(noise(len(t)), 1800) * env(t, 0.02) * 0.9
    return thump + slap


def sword():
    t = tarr(0.45)
    w = np.sin(np.pi * t / t[-1]) ** 2
    return lowpass(highpass(noise(len(t)), 1800) * w, 1500 + 6000 * w) * 1.5


def whoosh():
    t = tarr(0.75)
    w = np.sin(np.pi * t / t[-1]) ** 1.5
    return lowpass(noise(len(t)), 300 + 4500 * w) * w


def glass():
    t = tarr(0.9)
    x = highpass(noise(len(t)), 3000) * env(t, 0.02)
    for _ in range(14):
        tt = tarr(0.25)
        ping = tone(tt, rng.uniform(3000, 9000)) * env(tt, rng.uniform(0.02, 0.07)) * rng.uniform(0.15, 0.4)
        at(x, ping, rng.uniform(0.0, 0.5))
    return x


# ---- sci-fi ----
def laser():
    t = tarr(0.35)
    vib = 1 + 0.03 * np.sin(2 * np.pi * 40 * t)
    f = (200 + 1800 * np.exp(-t / 0.08)) * vib
    x = np.sin(2 * np.pi * np.cumsum(f) / SR)
    x += 0.4 * np.sign(np.sin(2 * np.pi * np.cumsum(f * 1.5) / SR))
    return lowpass(x, 6000) * env(t, 0.1)


def plasma():
    t = tarr(0.45)
    f = (120 + 900 * np.exp(-t / 0.15)) * (1 + 0.05 * np.sin(2 * np.pi * 60 * t))
    ph = 2 * np.pi * np.cumsum(f) / SR
    return lowpass(np.sin(ph) + 0.5 * np.sign(np.sin(ph)), 4000) * env(t, 0.13)


def zap():
    t = tarr(0.28)
    buzz = highpass(noise(len(t)), 2000) * (1 + np.sin(2 * np.pi * 300 * t)) * env(t, 0.05)
    return buzz * 0.7 + sweep(t, 3000, 400, 0.03) * env(t, 0.06)


def power_up():
    t = tarr(0.55)
    f = 300 + 1000 * (t / t[-1]) ** 1.6
    x = np.sin(2 * np.pi * np.cumsum(f) / SR) + 0.3 * np.sin(4 * np.pi * np.cumsum(f) / SR)
    return x * np.minimum(1, t / 0.01) * (1 - t / (t[-1] + 1e-3)) ** 1.2


def ray_gun():
    t = tarr(0.32)
    f = 300 + 2200 * np.exp(-t / 0.07)
    ph = 2 * np.pi * np.cumsum(f) / SR
    return lowpass(np.sign(np.sin(ph)) * 0.6 + np.sin(ph), 5000) * env(t, 0.09)


def teleport():
    t = tarr(0.7)
    f = 800 + 600 * np.sin(2 * np.pi * 8 * t) * env(t, 0.3) + 800 * (1 - t / t[-1])
    return echo(np.sin(2 * np.pi * np.cumsum(f) / SR) * env(t, 0.2), [0.06, 0.12], 0.5)


# ---- keyboard ----
def blue_switch():
    t = tarr(0.14)
    x = highpass(noise(len(t)), rng.uniform(2800, 3600)) * env(t, 0.002)
    at(x, highpass(noise(int(0.06 * SR)), 2500) * env(tarr(0.06), 0.003) * 0.7, 0.045)
    return x + sweep(t, 300, 120, 0.01) * env(t, 0.015) * 0.5


def red_switch():
    t = tarr(0.12)
    return sweep(t, rng.uniform(190, 230), 90, 0.012) * env(t, 0.018) + \
        lowpass(noise(len(t)), 2200) * env(t, 0.006) * 0.5


def brown_switch():
    t = tarr(0.12)
    return sweep(t, rng.uniform(230, 270), 100, 0.01) * env(t, 0.015) * 0.9 + \
        highpass(noise(len(t)), 2500) * env(t, 0.003) * 0.7


def thock():
    t = tarr(0.16)
    return sweep(t, rng.uniform(170, 200), 70, 0.02) * env(t, 0.04) * 1.3 + \
        lowpass(noise(len(t)), 900) * env(t, 0.01) * 0.5


def typewriter():
    t = tarr(0.22)
    x = highpass(noise(len(t)), 1500) * env(t, 0.004) * 1.2
    at(x, highpass(noise(int(0.08 * SR)), 2000) * env(tarr(0.08), 0.005) * 0.6, 0.05)
    return x + sweep(t, 500, 150, 0.008) * env(t, 0.02)


# ---- fun ----
def coin():
    t = tarr(0.5)
    x = tone(t, 988) * env(t, 0.08) * 0.7
    return at(x, tone(tarr(0.43), 1319) * env(tarr(0.43), 0.18), 0.07)


def boing():
    t = tarr(0.6)
    vib = 1 + 0.25 * np.sin(2 * np.pi * 22 * t) * env(t, 0.25)
    f = (180 + 520 * np.exp(-t / 0.12)) * vib
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(t, 0.22)


def water_drop():
    t = tarr(0.3)
    f = 1300 - 900 * np.exp(-t / 0.025)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(t, 0.05)


def pop():
    t = tarr(0.18)
    return sweep(t, 950, 200, 0.015) * env(t, 0.03) + highpass(noise(len(t)), 2500) * env(t, 0.004) * 0.3


def bell():
    t = tarr(1.4)
    f0 = random.choice([523.25, 659.25, 783.99, 880.0])
    x = sum(a * tone(t, f0 * m) * env(t, d)
            for m, a, d in [(1, 1.0, 0.5), (2.76, 0.5, 0.3), (5.4, 0.3, 0.18), (8.9, 0.15, 0.1)])
    return x


def marimba():
    t = tarr(0.7)
    f0 = random.choice([262, 294, 330, 392, 440, 523, 587, 659])
    return tone(t, f0) * env(t, 0.16) + 0.35 * tone(t, f0 * 4) * env(t, 0.03)


def kick():
    t = tarr(0.4)
    return sweep(t, 160, 40, 0.04) * env(t, 0.17) * 1.8 + highpass(noise(len(t)), 2000) * env(t, 0.003) * 0.3


def snare():
    t = tarr(0.3)
    return highpass(noise(len(t)), 1500) * env(t, 0.08) + sweep(t, 220, 120, 0.02) * env(t, 0.06) * 0.7


def hihat():
    t = tarr(0.12)
    return highpass(noise(len(t)), 7000) * env(t, 0.025)


SOUNDS = {
    "Shotgun": ("Guns", shotgun), "Pistol": ("Guns", pistol), "Revolver": ("Guns", revolver),
    "Rifle": ("Guns", rifle), "Machine Gun": ("Guns", machine_gun), "Sniper": ("Guns", sniper),
    "Silenced": ("Guns", silenced), "Cannon": ("Guns", cannon),
    "Explosion": ("Impact", explosion), "Thunder": ("Impact", thunder), "Punch": ("Impact", punch),
    "Sword Swish": ("Impact", sword), "Whoosh": ("Impact", whoosh), "Glass Break": ("Impact", glass),
    "Laser": ("Sci-Fi", laser), "Plasma": ("Sci-Fi", plasma), "Zap": ("Sci-Fi", zap),
    "Power Up": ("Sci-Fi", power_up), "Ray Gun": ("Sci-Fi", ray_gun), "Teleport": ("Sci-Fi", teleport),
    "Blue Switch": ("Keyboard", blue_switch), "Red Switch": ("Keyboard", red_switch),
    "Brown Switch": ("Keyboard", brown_switch), "Thock": ("Keyboard", thock),
    "Typewriter": ("Keyboard", typewriter),
    "Coin": ("Fun", coin), "Boing": ("Fun", boing), "Water Drop": ("Fun", water_drop),
    "Pop": ("Fun", pop), "Bell": ("Fun", bell), "Marimba": ("Fun", marimba),
    "Kick Drum": ("Fun", kick), "Snare": ("Fun", snare), "Hi-Hat": ("Fun", hihat),
}
CATEGORIES = ["All", "Guns", "Impact", "Sci-Fi", "Keyboard", "Fun", "Mine"]


# =====================================================================
#  Effects + conversion to a playable Sound
# =====================================================================
def reverb(x, amt):
    n = int(0.9 * SR)
    t = np.arange(n) / SR
    ir = lowpass(noise(n) * np.exp(-t / 0.22), 5000)
    ir /= np.sqrt(np.sum(ir ** 2)) + 1e-9
    total = len(x) + n
    size = 1 << (total - 1).bit_length()
    wet = np.fft.irfft(np.fft.rfft(x, size) * np.fft.rfft(ir, size), size)[:total]
    dry = np.concatenate([x, np.zeros(n)])
    return dry * (1 - 0.35 * amt) + wet * amt * 0.9


def apply_fx(x, fx):
    semi = fx["pitch"] + (rng.uniform(-0.7, 0.7) if fx["humanize"] else 0.0)
    if abs(semi) > 1e-3:
        r = 2 ** (semi / 12)
        x = np.interp(np.arange(0, len(x) - 1, r), np.arange(len(x)), x)
    if fx["bass"] > 0.01:
        x = x + fx["bass"] * 1.5 * lowpass(x, 160)
    if fx["reverb"] > 0.01:
        x = reverb(x, fx["reverb"])
    return x


def to_sound(x):
    x = np.asarray(x, dtype=float)
    x = x / (np.max(np.abs(x)) + 1e-9)
    x = np.tanh(1.3 * x) / np.tanh(1.3)              # gentle limiter, stays clear
    fi, fo = int(0.0006 * SR), int(0.012 * SR)
    if len(x) > fi + fo:
        x[:fi] *= np.linspace(0, 1, fi)
        x[-fo:] *= np.linspace(1, 0, fo)
    pcm = (x * 0.92 * 32767).astype(np.int16)
    stereo = np.ascontiguousarray(np.column_stack([pcm, pcm]))
    return pygame.mixer.Sound(buffer=stereo.tobytes())


def sound_to_array(snd):
    a = pygame.sndarray.array(snd).astype(np.float64) / 32768
    return a.mean(axis=1) if a.ndim == 2 else a


# =====================================================================
#  Files, icon, autostart
# =====================================================================
def scan_custom():
    found = {}
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(SOUNDS_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT:
            found[p.stem] = [p]
        elif p.is_dir():
            files = [f for f in sorted(p.iterdir()) if f.is_file() and f.suffix.lower() in AUDIO_EXT]
            if files:
                found[p.name] = files
    return found


def open_folder(path):
    if sys.platform.startswith("win"):
        os.startfile(path)                      # noqa
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


def resource(name):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, name)


def make_icon(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 2, size - 2], fill=(217, 72, 15, 255))
    c, pts = size / 2, []
    for i in range(16):
        r = size * 0.38 if i % 2 == 0 else size * 0.16
        a = math.pi * 2 * i / 16 - math.pi / 2
        pts.append((c + r * math.cos(a), c + r * math.sin(a)))
    d.polygon(pts, fill=(255, 230, 120, 255))
    return img


def autostart_get():
    if not sys.platform.startswith("win"):
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, "ReechoKeys")
            return True
    except OSError:
        return False


def autostart_set(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            if getattr(sys, "frozen", False):
                cmd = f'"{sys.executable}" --minimized'
            else:
                cmd = f'"{sys.executable}" "{os.path.abspath(__file__)}" --minimized'
            winreg.SetValueEx(k, "ReechoKeys", 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, "ReechoKeys")
            except OSError:
                pass


# =====================================================================
#  The app
# =====================================================================
class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        pygame.mixer.init(SR, -16, 2, 512, allowedchanges=0)
        pygame.mixer.set_num_channels(32)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        self.raw_cache, self.raw, self.current = {}, [], []
        self.custom, self.cards, self.voices = {}, {}, []
        self.pressed, self.cmds = set(), queue.Queue()
        self.count, self.tray, self.listener = 0, None, None
        self._enabled = True
        self._rerender_job = None
        self.sel_key = "builtin|Shotgun"

        st = {}
        try:
            st = json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
        self._volume = float(st.get("volume", 0.8))
        self.fx = {"reverb": float(st.get("reverb", 0.0)), "bass": float(st.get("bass", 0.0)),
                   "pitch": int(st.get("pitch", 0)), "humanize": bool(st.get("humanize", True))}
        self.clean = bool(st.get("clean", True))
        self.close_mode = st.get("close_mode", "Keep running in tray")
        self.hint_shown = bool(st.get("hint_shown", False))
        self.sel_key = st.get("sound", "builtin|Shotgun")
        self.category = "All"

        ctk.set_appearance_mode("dark")
        self.title("ReechoKeys")
        self.geometry("940x680")
        self.minsize(860, 600)
        self.configure(fg_color=BG)
        self.after(300, self._set_icon)

        self.build_ui()
        self.refresh_cards()
        if self.sel_key not in self.valid_keys():
            self.sel_key = "builtin|Shotgun"
        self.select_key(self.sel_key)

        try:
            self.listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
            self.listener.daemon = True
            self.listener.start()
        except Exception:
            self.listener = None
        self.has_tray = self.start_tray()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        if "--minimized" in sys.argv and self.has_tray:
            self.withdraw()
        self.poll()

    # ---------- UI ----------
    def _set_icon(self):
        try:
            if sys.platform.startswith("win"):
                self.iconbitmap(resource("icon.ico"))
        except Exception:
            pass

    def build_ui(self):
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=1)
        F = ctk.CTkFont

        # header
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=24, pady=(18, 8))
        head.grid_columnconfigure(1, weight=1)
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(title, text="Reecho", font=F(size=30, weight="bold"), text_color=TXT).pack(side="left")
        ctk.CTkLabel(title, text="Keys", font=F(size=30, weight="bold"), text_color=ACC).pack(side="left")
        ctk.CTkLabel(head, text="Every key press, a sound.", font=F(size=13), text_color=MUT
                     ).grid(row=1, column=0, sticky="w")

        power = ctk.CTkFrame(head, fg_color=PANEL, corner_radius=18)
        power.grid(row=0, column=2, rowspan=2, sticky="e")
        self.power_label = ctk.CTkLabel(power, text="ON", font=F(size=15, weight="bold"), text_color=ACC, width=60)
        self.power_label.pack(side="left", padx=(18, 4), pady=12)
        self.power_switch = ctk.CTkSwitch(power, text="", width=60, switch_width=56, switch_height=28,
                                          progress_color=ACC, button_color="#FFFFFF",
                                          button_hover_color="#E8E8E8", fg_color="#3A3F4D",
                                          command=self.on_power)
        self.power_switch.select()
        self.power_switch.pack(side="left", padx=(0, 6))
        ctk.CTkLabel(power, text="F9 mutes anywhere", font=F(size=11), text_color=MUT
                     ).pack(side="left", padx=(6, 18))

        # body
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=24, pady=6)
        body.grid_columnconfigure(0, weight=3)
        body.grid_columnconfigure(1, weight=2, minsize=320)
        body.grid_rowconfigure(0, weight=1)

        left = ctk.CTkFrame(body, fg_color=PANEL, corner_radius=20)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.grid_columnconfigure(0, weight=1)
        left.grid_rowconfigure(1, weight=1)
        self.seg = ctk.CTkSegmentedButton(
            left, values=CATEGORIES, command=self.on_category, height=36, corner_radius=12,
            fg_color=CARD, selected_color=ACC, selected_hover_color=ACC_H,
            unselected_color=CARD, unselected_hover_color=CARD_H, text_color=TXT, font=F(size=13))
        self.seg.set("All")
        self.seg.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        self.card_frame = ctk.CTkScrollableFrame(left, fg_color="transparent",
                                                 scrollbar_button_color=CARD_H,
                                                 scrollbar_button_hover_color=MUT)
        self.card_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 12))

        right = ctk.CTkScrollableFrame(body, fg_color=PANEL, corner_radius=20,
                                       scrollbar_button_color=CARD_H, scrollbar_button_hover_color=MUT)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(right, text="Sound tuning", font=F(size=17, weight="bold"), text_color=TXT
                     ).grid(row=0, column=0, sticky="w", padx=18, pady=(10, 0))
        self.sl_vol = self.slider(right, 1, "Volume", 0, 1, 100, self._volume, lambda v: f"{int(v * 100)}%", self.on_volume)
        self.sl_rev = self.slider(right, 2, "Reverb", 0, 1, 20, self.fx["reverb"], lambda v: f"{int(v * 100)}%", self.on_fx("reverb"))
        self.sl_bass = self.slider(right, 3, "Bass boost", 0, 1, 20, self.fx["bass"], lambda v: f"{int(v * 100)}%", self.on_fx("bass"))
        self.sl_pitch = self.slider(right, 4, "Pitch", -12, 12, 24, self.fx["pitch"], lambda v: f"{int(round(v)):+d} st", self.on_fx("pitch"))

        sw = dict(progress_color=ACC, button_color="#FFFFFF", button_hover_color="#E8E8E8",
                  fg_color="#3A3F4D", font=F(size=13), text_color=TXT)
        self.sw_clean = ctk.CTkSwitch(right, text="Clean cut  (no overlapping sounds)", command=self.on_clean, **sw)
        self.sw_clean.grid(row=5, column=0, sticky="w", padx=18, pady=(10, 4))
        (self.sw_clean.select if self.clean else self.sw_clean.deselect)()
        self.sw_hum = ctk.CTkSwitch(right, text="Humanize  (each press slightly different)", command=self.on_humanize, **sw)
        self.sw_hum.grid(row=6, column=0, sticky="w", padx=18, pady=4)
        (self.sw_hum.select if self.fx["humanize"] else self.sw_hum.deselect)()

        btn = dict(corner_radius=12, height=36, font=F(size=13, weight="bold"))
        ctk.CTkButton(right, text="Test sound", fg_color=ACC, hover_color=ACC_H, text_color="#FFF",
                      command=self.play, **btn).grid(row=7, column=0, sticky="ew", padx=18, pady=(12, 6))
        row = ctk.CTkFrame(right, fg_color="transparent")
        row.grid(row=8, column=0, sticky="ew", padx=18, pady=(0, 14))
        row.grid_columnconfigure((0, 1, 2), weight=1, uniform="b")
        small = dict(corner_radius=12, height=34, font=F(size=12, weight="bold"),
                     fg_color=CARD, hover_color=CARD_H, text_color=TXT)
        ctk.CTkButton(row, text="+ Add mine", command=self.add_sound, **small).grid(row=0, column=0, sticky="ew", padx=(0, 3))
        ctk.CTkButton(row, text="Remove", command=self.remove_sound, **small).grid(row=0, column=1, sticky="ew", padx=3)
        ctk.CTkButton(row, text="Folder", command=lambda: open_folder(SOUNDS_DIR), **small).grid(row=0, column=2, sticky="ew", padx=(3, 0))

        # footer
        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.grid(row=2, column=0, sticky="ew", padx=24, pady=(6, 18))
        foot.grid_columnconfigure(2, weight=1)
        self.status = ctk.CTkLabel(foot, text="Shots: 0", font=F(size=13), text_color=MUT, width=90, anchor="w")
        self.status.grid(row=0, column=0, sticky="w")
        col = 1
        if sys.platform.startswith("win"):
            self.sw_auto = ctk.CTkSwitch(foot, text="Start with Windows", command=self.on_autostart, **sw)
            self.sw_auto.grid(row=0, column=col, padx=(10, 6))
            (self.sw_auto.select if autostart_get() else self.sw_auto.deselect)()
            col += 1
        ctk.CTkLabel(foot, text="").grid(row=0, column=2, sticky="ew")
        if TRAY_OK:
            ctk.CTkLabel(foot, text="When I close the window:", font=F(size=12), text_color=MUT
                         ).grid(row=0, column=3, padx=(6, 6))
            self.close_menu = ctk.CTkOptionMenu(
                foot, values=["Keep running in tray", "Quit app"], command=self.on_close_mode, width=170,
                fg_color=CARD, button_color=CARD_H, button_hover_color=MUT, dropdown_fg_color=CARD,
                dropdown_hover_color=CARD_H, text_color=TXT, font=F(size=12))
            self.close_menu.set(self.close_mode)
            self.close_menu.grid(row=0, column=4, padx=(0, 10))
        else:
            ctk.CTkLabel(foot, text="Closing the window quits ReechoKeys", font=F(size=12),
                         text_color=MUT).grid(row=0, column=3, columnspan=2, padx=(6, 12))
        ctk.CTkButton(foot, text="Quit ReechoKeys", fg_color=RED, hover_color="#E04A39", text_color="#FFF",
                      width=140, command=self.quit_app, **btn).grid(row=0, column=5)

    def slider(self, parent, row, label, lo, hi, steps, init, fmt, cmd):
        f = ctk.CTkFrame(parent, fg_color="transparent")
        f.grid(row=row, column=0, sticky="ew", padx=18, pady=4)
        f.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(f, text=label, font=ctk.CTkFont(size=13), text_color=TXT).grid(row=0, column=0, sticky="w")
        val = ctk.CTkLabel(f, text=fmt(init), font=ctk.CTkFont(size=13), text_color=ACC)
        val.grid(row=0, column=1, sticky="e")

        def changed(v):
            val.configure(text=fmt(v))
            cmd(v)
        s = ctk.CTkSlider(f, from_=lo, to=hi, number_of_steps=steps, command=changed, height=18,
                          progress_color=ACC, button_color="#FFFFFF", button_hover_color="#E8E8E8",
                          fg_color="#3A3F4D")
        s.set(init)
        s.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        return s

    # ---------- sound list ----------
    def valid_keys(self):
        return [f"builtin|{n}" for n in SOUNDS] + [f"custom|{n}" for n in self.custom]

    def refresh_cards(self):
        self.custom = scan_custom()
        for w in self.card_frame.winfo_children():
            w.destroy()
        items = []
        for name, (cat, _) in SOUNDS.items():
            if self.category in ("All", cat):
                items.append((f"builtin|{name}", name))
        if self.category in ("All", "Mine"):
            items += [(f"custom|{n}", n) for n in self.custom]
        cols = 3
        for c in range(cols):
            self.card_frame.grid_columnconfigure(c, weight=1, uniform="cards")
        self.cards = {}
        if not items:
            ctk.CTkLabel(self.card_frame, text="No sounds here yet.\nClick  + Add my sound  to bring your own.",
                         font=ctk.CTkFont(size=13), text_color=MUT, justify="center"
                         ).grid(row=0, column=0, columnspan=cols, pady=60)
        for i, (key, label) in enumerate(items):
            b = ctk.CTkButton(self.card_frame, text=label, height=54, corner_radius=14,
                              font=ctk.CTkFont(size=13, weight="bold"), fg_color=CARD, hover_color=CARD_H,
                              text_color=TXT, command=lambda k=key: self.pick(k))
            b.grid(row=i // cols, column=i % cols, padx=5, pady=5, sticky="ew")
            self.cards[key] = b
        self.paint()

    def paint(self):
        for key, b in self.cards.items():
            on = key == self.sel_key
            b.configure(fg_color=ACC if on else CARD, hover_color=ACC_H if on else CARD_H,
                        text_color="#FFFFFF" if on else TXT)

    def on_category(self, value):
        self.category = value
        self.refresh_cards()

    def pick(self, key):
        self.select_key(key)
        self.play()

    def select_key(self, key):
        kind, name = key.split("|", 1)
        if key not in self.raw_cache:
            if kind == "builtin":
                self.raw_cache[key] = [SOUNDS[name][1]() for _ in range(VARIANTS)]
            else:
                arrs = []
                for p in self.custom.get(name, []):
                    try:
                        arrs.append(sound_to_array(pygame.mixer.Sound(str(p)))[: MAX_PLAY_MS * SR // 1000])
                    except Exception:
                        pass
                if not arrs:
                    messagebox.showwarning("ReechoKeys", f"Couldn't load '{name}'. Try a .wav file.")
                    return
                self.raw_cache[key] = arrs * 4 if len(arrs) == 1 else arrs
        self.sel_key = key
        self.raw = self.raw_cache[key]
        self.rerender()
        self.paint()

    def rerender(self):
        fx = dict(self.fx)
        self.current = [to_sound(apply_fx(x.copy(), fx)) for x in self.raw]

    def schedule_rerender(self):
        if self._rerender_job:
            self.after_cancel(self._rerender_job)
        self._rerender_job = self.after(250, self.rerender)

    # ---------- controls ----------
    def on_power(self):
        self._enabled = bool(self.power_switch.get())

    def on_volume(self, v):
        self._volume = float(v)

    def on_fx(self, name):
        def cb(v):
            self.fx[name] = int(round(v)) if name == "pitch" else float(v)
            self.schedule_rerender()
        return cb

    def on_clean(self):
        self.clean = bool(self.sw_clean.get())

    def on_humanize(self):
        self.fx["humanize"] = bool(self.sw_hum.get())
        self.schedule_rerender()

    def on_autostart(self):
        try:
            autostart_set(bool(self.sw_auto.get()))
        except Exception as e:
            messagebox.showwarning("ReechoKeys", f"Couldn't change startup setting:\n{e}")

    def on_close_mode(self, v):
        self.close_mode = v

    def add_sound(self):
        files = filedialog.askopenfilenames(
            title="Choose sound files",
            filetypes=[("Audio", "*.wav *.ogg *.mp3 *.flac"), ("All files", "*.*")])
        if not files:
            return
        SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
        last = None
        for f in files:
            dest = SOUNDS_DIR / Path(f).name
            shutil.copy2(f, dest)
            last = f"custom|{dest.stem}"
            self.raw_cache.pop(last, None)
        self.category = "Mine"
        self.seg.set("Mine")
        self.refresh_cards()
        if last:
            self.select_key(last)
            self.play()

    def remove_sound(self):
        if not self.sel_key.startswith("custom|"):
            messagebox.showinfo("ReechoKeys", "Select one of your own sounds first, then click Remove.")
            return
        name = self.sel_key.split("|", 1)[1]
        if not messagebox.askyesno("ReechoKeys", f"Remove '{name}'?"):
            return
        for p in self.custom.get(name, []):
            p.unlink(missing_ok=True)
        folder = SOUNDS_DIR / name
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        self.raw_cache.pop(self.sel_key, None)
        self.sel_key = "builtin|Shotgun"
        self.refresh_cards()
        self.select_key(self.sel_key)

    # ---------- playing ----------
    def play(self):
        cur = self.current
        if not cur:
            return
        snd = random.choice(cur)
        snd.set_volume(self._volume)
        if self.clean:                              # cut the previous sound cleanly
            for ch in self.voices:
                if ch.get_busy():
                    ch.fadeout(25)
            self.voices = []
        elif len(self.voices) >= 6:                 # layered mode: cap the pile-up
            old = self.voices.pop(0)
            if old.get_busy():
                old.fadeout(60)
        ch = snd.play(maxtime=MAX_PLAY_MS)
        if ch:
            self.voices.append(ch)
        self.count += 1

    def on_press(self, key):                        # listener thread
        if key in self.pressed:                     # ignore held-key repeat
            return
        self.pressed.add(key)
        if key == keyboard.Key.f9:
            self._enabled = not self._enabled
            return
        if self._enabled:
            self.play()

    def on_release(self, key):
        self.pressed.discard(key)

    # ---------- tray / lifecycle ----------
    def start_tray(self):
        if not TRAY_OK:
            return False
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Open ReechoKeys", lambda i, it: self.cmds.put("show"), default=True),
                pystray.MenuItem("Mute / Unmute  (F9)", lambda i, it: self.cmds.put("toggle")),
                pystray.MenuItem("Quit", lambda i, it: self.cmds.put("quit")))
            self.tray = pystray.Icon("ReechoKeys", make_icon(), "ReechoKeys", menu)
            self.tray.run_detached()
            return True
        except Exception:
            return False

    def on_close(self):
        if self.close_mode == "Quit app" or not self.has_tray:
            return self.quit_app()
        self.withdraw()
        if not self.hint_shown:
            self.hint_shown = True
            messagebox.showinfo(
                "ReechoKeys is still running",
                "ReechoKeys keeps running in the background.\n\n"
                "Find its icon near the clock (bottom-right; you may need to click the ^ arrow). "
                "Click it to open, or right-click > Quit to turn it off completely.\n\n"
                "You can change this with 'When I close the window' at the bottom of the app.")

    def poll(self):                                 # UI thread
        while not self.cmds.empty():
            cmd = self.cmds.get()
            if cmd == "show":
                self.deiconify()
                self.lift()
            elif cmd == "toggle":
                self._enabled = not self._enabled
            elif cmd == "quit":
                return self.quit_app()
        if bool(self.power_switch.get()) != self._enabled:
            (self.power_switch.select if self._enabled else self.power_switch.deselect)()
        self.power_label.configure(text="ON" if self._enabled else "MUTED",
                                   text_color=ACC if self._enabled else MUT)
        self.status.configure(text=f"Shots: {self.count}")
        self.after(150, self.poll)

    def quit_app(self):
        try:
            SETTINGS_FILE.write_text(json.dumps({
                "sound": self.sel_key, "volume": self._volume, "reverb": self.fx["reverb"],
                "bass": self.fx["bass"], "pitch": self.fx["pitch"], "humanize": self.fx["humanize"],
                "clean": self.clean, "close_mode": self.close_mode, "hint_shown": self.hint_shown}))
        except Exception:
            pass
        try:
            if self.listener:
                self.listener.stop()
            if self.tray:
                self.tray.stop()
            pygame.mixer.quit()
        finally:
            self.destroy()


if __name__ == "__main__":
    App().mainloop()
