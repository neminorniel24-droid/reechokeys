# Contributing to ReechoKeys

Thanks for wanting to help! 🎉

**How changes work here:** anyone can *propose* a change, but only the maintainer
([@neminorniel24-droid](https://github.com/neminorniel24-droid)) can merge it.
You never edit the main code directly - you send a **pull request** and it gets reviewed.

## Ways to help

- **Report a bug** - [open an issue](https://github.com/neminorniel24-droid/reechokeys/issues/new/choose) with your system and what happened.
- **Suggest a sound or feature** - open a *Feature request* issue.
- **Test on macOS / Linux** - tell us what works and what doesn't.
- **Send code** - follow the steps below.

## Sending a change (pull request)

1. **Fork** the repository (the *Fork* button, top right).
2. **Clone** your fork and make a branch:
   ```bash
   git clone https://github.com/YOUR-NAME/reechokeys.git
   cd reechokeys
   git checkout -b my-change
   ```
3. Make your change. Run the checks:
   ```bash
   pip install -r requirements.txt
   python tests/test_sounds.py
   python reechokeys.py        # try it for real
   ```
4. Commit and push, then open a **Pull Request** from your branch on GitHub:
   ```bash
   git add .
   git commit -m "Add Rubber Duck sound"
   git push -u origin my-change
   ```
5. Wait for review. The maintainer may ask for changes. Once approved, it is merged.

Keep pull requests **small and focused** - one idea per PR is much easier to review.

## Adding a new sound (easiest contribution!)

Sounds are small functions in `reechokeys.py` that return a mono NumPy array. Example:

```python
def rubber_duck():
    t = tarr(0.25)                                   # 0.25 seconds of time values
    f = 700 + 300 * np.exp(-t / 0.05)                # pitch that drops quickly
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * env(t, 0.08)   # tone fading out
```

Then register it in the `SOUNDS` dictionary with a category:

```python
"Rubber Duck": ("Fun", rubber_duck),
```

Helpers you can use: `tarr`, `noise`, `env`, `sweep`, `tone`, `lowpass`, `highpass`,
`bandpass`, `echo`, `at`. Look at the existing sounds for ideas. Tips:

- Keep sounds **short** (under ~1.5 s) - they play on every key press.
- Use `rng` / `random` for small variations so repeats don't sound identical.
- Run `python tests/test_sounds.py` - it checks your sound is audible and finite.

## Code guidelines

- Keep the app **private**: never log, store or transmit which keys were pressed.
- No network access, no telemetry, no analytics.
- Must keep working on Windows; avoid breaking macOS and Linux.
- Prefer simple, readable code over clever code.
- UI changes: include a screenshot in your PR.

## Behaviour

Be kind and patient. Everyone starts somewhere. Harassment or rude behaviour gets you blocked.

## License

By contributing you agree your work is released under the [MIT License](LICENSE).
