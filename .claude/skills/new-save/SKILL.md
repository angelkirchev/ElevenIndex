---
name: new-save
description: Help someone get ElevenIndex going for an FM save (first time, or another club). Use when the user asks to set up ElevenIndex, a new save, or another club, or there is no config.toml yet.
---

# Getting someone started

Talk plainly and do the typing for them; README.md is what they've seen.

1. **The app.** Usually they run `ElevenIndex.exe` from the website (elevenindex.pages.dev): its first start shows a
   setup screen (club exactly as FM writes it, currency; FM's save games folder is found by itself) and makes the save.
   From source it's `ElevenIndex.bat` (or `python app.py`), which needs Python 3.14+ and pywebview.
2. **Another club.** **Menu → Game profiles… → New** makes one; `score.new_save(name, club)` does the same from code. Every
   game profile scores with the same attribute weights: `weights/default.toml`'s, or theirs from the Attributes page
   (`saves/attributes.toml`).
3. **First read.** In FM, save the game; in the app, **Menu → Read save game…** (or drop the `.fm` on the window). The
   save's club must be the game profile's club. Auto-read (on by default) reads every later save by itself.
