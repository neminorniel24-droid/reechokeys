"""
ReechoKeys - plays a sound every time you press a key, in ANY app.

* 12 built-in synthesized sounds (guns, laser, boing, coin, ...)
* Add your own sounds (.wav / .ogg / .mp3) with one click
* A sound can also be a "pack": put several files in a sub-folder and a
  random one plays on each key press
* Press F9 anywhere to mute / unmute

The app only reacts to "a key was pressed". It never records or saves
what you type.
"""
import json
import math
import os
import queue
import random
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import numpy as np
import pygame
from pynput import keyboard

try:
    import pystray
    from PIL import Image, ImageDraw
except Exception:          # tray is optional; app still works without it
    pystray = None

SR = 44100
rng = np.random.default_rng()

CONFIG_DIR = Path.home() / ".reechokeys"
SOUNDS_DIR = CONFIG_DIR / "sounds"
SETTINGS_FILE = CONFIG_DIR / "settings.json"
AUDIO_EXT = {".wav", ".ogg", ".mp3", ".flac"}
MAX_PLAY_MS = 3000          # long files are cut off so keys never pile up


# ---------- tiny DSP helpers ----------
def tarr(dur):
    return np.arange(int(dur * SR)) / SR


def noise(n):
    return rng.uniform(-1, 1, n)


def lowpass(x, fc):
    fc = np.broadcast_to(np.asarray(fc, dtype=float), x.shape)
    alpha = 1 - np.exp(-2 * np.pi * fc / SR)
    y = np.empty_like(x)
    prev = 0.0
    for i in range(len(x)):
        prev += alpha[i] * (x[i] - prev)
        y[i] = prev
    return y


def highpass(x, fc):
    return x - lowpass(x, fc)


def env(t, tau):
    return np.exp(-t / tau)


def sweep_sine(t, f0, f1, tau):
    f = f1 + (f0 - f1) * np.exp(-t / tau)
    return np.sin(2 * np.pi * np.cumsum(f) / SR)


def echo(x, delays, decay=0.5):
    out = x.copy()
    for k, d in enumerate(delays, 1):
        s = int(d * SR)
        if s < len(x):
            out[s:] += x[: len(x) - s] * (decay ** k)
    return out


def finalize(x, peak=0.9):
    x = x / (np.max(np.abs(x)) + 1e-9)
    x = np.tanh(1.6 * x) / np.tanh(1.6) * peak
    pcm = (x * 32767).astype(np.int16)
    stereo = np.ascontiguousarray(np.column_stack([pcm, pcm]))
    return pygame.mixer.Sound(buffer=stereo.tobytes())


# ---------- built-in sound recipes ----------
def shotgun():
    t = tarr(1.1)
    crack = highpass(noise(len(t)), rng.uniform(700, 1100)) * env(t, 0.035)
    body = lowpass(noise(len(t)), 180 + 4200 * np.exp(-t / 0.12)) * env(t, 0.16) * 1.4
    thump = sweep_sine(t, rng.uniform(140, 170), 32, 0.06) * env(t, 0.13) * 1.8
    x = echo(crack + body + thump, [0.045, 0.09, 0.15, 0.23], 0.45)
    s = int(0.38 * SR)
    m = np.zeros_like(t)
    seg = highpass(lowpass(noise(len(t) - s), 4000), 1500)
    m[s:] = seg * env(np.arange(len(seg)) / SR, 0.02) * 0.4   # pump "chk"
    return finalize(x + m)


def pistol():
    t = tarr(0.6)
    crack = highpass(noise(len(t)), rng.uniform(1100, 1500)) * env(t, 0.02)
    body = lowpass(noise(len(t)), 300 + 2800 * np.exp(-t / 0.05)) * env(t, 0.07) * 1.1
    thump = sweep_sine(t, 210, 60, 0.04) * env(t, 0.06) * 1.2
    return finalize(echo(crack + body + thump, [0.04, 0.09, 0.16], 0.35))


def rifle():
    t = tarr(0.9)
    crack = highpass(noise(len(t)), rng.uniform(900, 1300)) * env(t, 0.025) * 1.2
    body = lowpass(noise(len(t)), 250 + 3500 * np.exp(-t / 0.07)) * env(t, 0.1)
    thump = sweep_sine(t, 160, 45, 0.05) * env(t, 0.09) * 1.5
    return finalize(echo(crack + body + thump, [0.06, 0.13, 0.22], 0.4))


def machine_gun():
    t = tarr(0.22)
    crack = highpass(noise(len(t)), 1300) * env(t, 0.012)
    body = lowpass(noise(len(t)), 250 + 2500 * np.exp(-t / 0.03)) * env(t, 0.035)
    thump = sweep_sine(t, 130, 50, 0.03) * env(t, 0.04)
    return finalize(crack + body + thump)


def sniper():
    t = tarr(1.8)
    crack = highpass(noise(len(t)), 900) * env(t, 0.03) * 1.3
    body = lowpass(noise(len(t)), 150 + 3000 * np.exp(-t / 0.2)) * env(t, 0.22)
    thump = sweep_sine(t, 120, 28, 0.09) * env(t, 0.2) * 1.8
    return finalize(echo(crack + body + thump, [0.12, 0.25, 0.42, 0.65], 0.5))


def explosion():
    t = tarr(2.0)
    body = lowpass(noise(len(t)), 80 + 1800 * np.exp(-t / 0.4)) * env(t, 0.5) * 1.5
    thump = sweep_sine(t, 70, 22, 0.15) * env(t, 0.4) * 2.0
    crack = highpass(noise(len(t)), 600) * env(t, 0.03)
    return finalize(echo(body + thump + crack, [0.08, 0.2, 0.4], 0.5))


def laser():
    t = tarr(0.35)
    vib = 1 + 0.03 * np.sin(2 * np.pi * 40 * t)
    f = (200 + 1800 * np.exp(-t / 0.08)) * vib
    x = np.sin(2 * np.pi * np.cumsum(f) / SR)
    x += 0.4 * np.sign(np.sin(2 * np.pi * np.cumsum(f * 1.5) / SR))
    return finalize(x * env(t, 0.1))


def mech_click():
    t = tarr(0.12)
    click = highpass(noise(len(t)), rng.uniform(2500, 3500)) * env(t, 0.003)
    thock = sweep_sine(t, rng.uniform(220, 280), 90, 0.012) * env(t, 0.02) * 1.2
    return finalize(click + thock)


def coin():
    t = tarr(0.5)
    a = np.sin(2 * np.pi * 988 * t) * env(t, 0.08)
    s = int(0.07 * SR)
    b = np.zeros_like(t)
    b[s:] = np.sin(2 * np.pi * 1319 * t[: len(t) - s]) * env(t[: len(t) - s], 0.18)
    return finalize(a * 0.7 + b)


def boing():
    t = tarr(0.6)
    vib = 1 + 0.25 * np.sin(2 * np.pi * 22 * t) * env(t, 0.25)
    f = (180 + 520 * np.exp(-t / 0.12)) * vib
    return finalize(np.sin(2 * np.pi * np.cumsum(f) / SR) * env(t, 0.22))


def water_drop():
    t = tarr(0.3)
    f = 1300 - 900 * np.exp(-t / 0.025)
    return finalize(np.sin(2 * np.pi * np.cumsum(f) / SR) * env(t, 0.05))


def sword():
    t = tarr(0.45)
    window = np.sin(np.pi * t / t[-1]) ** 2
    x = highpass(noise(len(t)), 1800) * window
    return finalize(lowpass(x, 1500 + 6000 * window) * 1.5)


BUILTIN = {
    "Shotgun": shotgun, "Pistol": pistol, "Rifle": rifle,
    "Machine Gun": machine_gun, "Sniper": sniper, "Explosion": explosion,
    "Laser": laser, "Mechanical Click": mech_click, "Coin": coin,
    "Boing": boing, "Water Drop": water_drop, "Sword Swish": sword,
}
VARIANTS = 4


# ---------- custom sounds on disk ----------
def scan_custom():
    """{name: [paths]} - each file is a sound; each sub-folder is a pack."""
    found = {}
    SOUNDS_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(SOUNDS_DIR.iterdir()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXT:
            found[p.stem] = [p]
        elif p.is_dir():
            files = [f for f in sorted(p.iterdir())
                     if f.is_file() and f.suffix.lower() in AUDIO_EXT]
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


RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def make_icon(size=64):
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([2, 2, size - 2, size - 2], fill=(217, 72, 15, 255))
    c = size / 2
    pts = []
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


class App:
    def __init__(self, root):
        pygame.mixer.pre_init(SR, -16, 2, 256)
        pygame.mixer.init()
        pygame.mixer.set_num_channels(32)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        self.root = root
        self.bank = {}              # built-in cache
        self.current = []           # sounds used for key presses
        self.keys = []              # list rows -> ("builtin"/"custom", name)
        self.custom = {}
        self.pressed = set()
        self.count = 0
        self._enabled = True
        self.cmds = queue.Queue()
        self.tray = None
        self.hint_shown = False
        self._volume = 0.8

        settings = {}
        try:
            settings = json.loads(SETTINGS_FILE.read_text())
        except Exception:
            pass
        self._volume = float(settings.get("volume", 0.8))
        wanted = settings.get("sound", "builtin|Shotgun")
        self.hint_shown = bool(settings.get("hint_shown", False))

        root.title("ReechoKeys")
        root.geometry("380x520")
        root.minsize(340, 440)
        pad = {"padx": 12, "pady": 5}

        ttk.Label(root, text="Pick a sound (plays on every key press, in any app)",
                  wraplength=350).pack(anchor="w", **pad)

        frame = ttk.Frame(root)
        frame.pack(fill="both", expand=True, **pad)
        self.listbox = tk.Listbox(frame, exportselection=False, font=("Segoe UI", 11),
                                  activestyle="none")
        sb = ttk.Scrollbar(frame, command=self.listbox.yview)
        self.listbox.config(yscrollcommand=sb.set)
        self.listbox.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.listbox.bind("<<ListboxSelect>>", self.on_pick)

        row = ttk.Frame(root)
        row.pack(fill="x", **pad)
        ttk.Button(row, text="Test", command=self.play).pack(side="left")
        ttk.Button(row, text="+ Add my sound...", command=self.add_sound).pack(side="left", padx=6)
        ttk.Button(row, text="Remove", command=self.remove_sound).pack(side="left")
        ttk.Button(row, text="Folder", command=lambda: open_folder(SOUNDS_DIR)).pack(side="right")

        ttk.Label(root, text="Volume").pack(anchor="w", **pad)
        self.vol = tk.DoubleVar(value=self._volume)
        ttk.Scale(root, from_=0, to=1, variable=self.vol,
                  command=lambda v: setattr(self, "_volume", float(v))).pack(fill="x", **pad)

        self.enabled_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(root, text="Enabled   (press F9 anywhere to mute / unmute)",
                        variable=self.enabled_var,
                        command=lambda: setattr(self, "_enabled", self.enabled_var.get())
                        ).pack(anchor="w", **pad)
        if sys.platform.startswith("win"):
            self.auto_var = tk.BooleanVar(value=autostart_get())
            ttk.Checkbutton(root, text="Start automatically when Windows starts",
                            variable=self.auto_var,
                            command=lambda: autostart_set(self.auto_var.get())
                            ).pack(anchor="w", **pad)
        self.status = ttk.Label(root, text="Shots: 0")
        self.status.pack(anchor="w", **pad)

        self.refresh_list(wanted)

        self.listener = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        self.listener.daemon = True
        self.listener.start()
        self.has_tray = self.start_tray()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        if "--minimized" in sys.argv and self.has_tray:
            root.withdraw()
        self.poll()

    # ----- list / selection -----
    def refresh_list(self, select=None):
        self.custom = scan_custom()
        self.keys = [("builtin", n) for n in BUILTIN] + [("custom", n) for n in self.custom]
        self.listbox.delete(0, "end")
        for kind, name in self.keys:
            self.listbox.insert("end", name if kind == "builtin" else f"(yours)  {name}")
        idx = 0
        if select:
            for i, (kind, name) in enumerate(self.keys):
                if f"{kind}|{name}" == select:
                    idx = i
        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(idx)
        self.listbox.see(idx)
        self.select(idx)

    def on_pick(self, _event=None):
        sel = self.listbox.curselection()
        if sel:
            self.select(sel[0])
            self.play()

    def select(self, idx):
        kind, name = self.keys[idx]
        self.sel_key = f"{kind}|{name}"
        if kind == "builtin":
            if name not in self.bank:
                self.bank[name] = [BUILTIN[name]() for _ in range(VARIANTS)]
            self.current = self.bank[name]
        else:
            loaded = []
            for p in self.custom.get(name, []):
                try:
                    loaded.append(pygame.mixer.Sound(str(p)))
                except Exception:
                    pass
            self.current = loaded
            if not loaded:
                messagebox.showwarning("ReechoKeys", f"Couldn't load '{name}'. Try a .wav file.")

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
        self.refresh_list(last)

    def remove_sound(self):
        sel = self.listbox.curselection()
        if not sel or self.keys[sel[0]][0] != "custom":
            messagebox.showinfo("ReechoKeys", "Select one of your own sounds to remove it.")
            return
        name = self.keys[sel[0]][1]
        if not messagebox.askyesno("ReechoKeys", f"Remove '{name}'?"):
            return
        for p in self.custom.get(name, []):
            p.unlink(missing_ok=True)
        folder = SOUNDS_DIR / name
        if folder.is_dir():
            shutil.rmtree(folder, ignore_errors=True)
        self.refresh_list("builtin|Shotgun")

    # ----- playing -----
    def play(self):
        if not self.current:
            return
        snd = random.choice(self.current)
        snd.set_volume(self._volume)
        snd.play(maxtime=MAX_PLAY_MS)
        self.count += 1

    def on_press(self, key):               # runs in the listener thread
        if key in self.pressed:            # ignore held-key auto-repeat
            return
        self.pressed.add(key)
        if key == keyboard.Key.f9:
            self._enabled = not self._enabled
            return
        if self._enabled:
            self.play()

    def on_release(self, key):
        self.pressed.discard(key)

    def start_tray(self):
        if pystray is None:
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
        if not self.has_tray:
            return self.quit()
        self.root.withdraw()
        if not self.hint_shown:
            self.hint_shown = True
            messagebox.showinfo(
                "ReechoKeys is still running",
                "ReechoKeys keeps running in the background.\n\n"
                "Find its icon near the clock (bottom-right, you may need to click the ^ arrow). "
                "Click it to open, or right-click > Quit to close for good.")

    def poll(self):                        # runs in the UI thread
        while not self.cmds.empty():
            cmd = self.cmds.get()
            if cmd == "show":
                self.root.deiconify()
                self.root.lift()
            elif cmd == "toggle":
                self._enabled = not self._enabled
            elif cmd == "quit":
                return self.quit()
        self.enabled_var.set(self._enabled)
        self.status.config(text=f"Shots: {self.count}" + ("" if self._enabled else "   (muted)"))
        self.root.after(150, self.poll)

    def quit(self):
        try:
            SETTINGS_FILE.write_text(json.dumps(
                {"sound": getattr(self, "sel_key", "builtin|Shotgun"), "volume": self._volume,
                 "hint_shown": self.hint_shown}))
        except Exception:
            pass
        self.listener.stop()
        if self.tray:
            self.tray.stop()
        pygame.mixer.quit()
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
