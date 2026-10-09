"""Headless check: every built-in sound builds, is audible, and survives all effects.
Run:  python tests/test_sounds.py   (on Linux CI: xvfb-run -a python tests/test_sounds.py)
"""
import os
import sys

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import numpy as np
import pygame

import reechokeys as r

pygame.mixer.init(r.SR, -16, 2, 512, allowedchanges=0)
plain = {"pitch": 0, "bass": 0.0, "reverb": 0.0, "length": 3.0, "humanize": False}
heavy = {"pitch": 5, "bass": 1.0, "reverb": 1.0, "length": 0.2, "humanize": True}

assert len(r.SOUNDS) >= 30, "sound library shrank"
for name, (cat, fn) in r.SOUNDS.items():
    assert cat in r.CATEGORIES, f"{name}: unknown category {cat!r}"
    x = fn()
    assert np.all(np.isfinite(x)), f"{name}: NaN/inf"
    assert np.max(np.abs(x)) > 1e-4, f"{name}: silent"
    assert len(x) > 600, f"{name}: too short"
    for fx in (plain, heavy):
        snd = r.to_sound(r.apply_fx(x.copy(), fx))
        assert snd.get_length() > 0.01, f"{name}: empty after effects"
print(f"OK - {len(r.SOUNDS)} sounds passed")
