---
name: new-save
description: Help someone get FMAnalyst going for an FM save (first time, or another club). Use when the user asks to set up FMAnalyst, a new save, or another club, or there is no config.toml yet.
---

# Getting someone started

Talk plainly and do the typing for them; README.md is what they've seen.

1. **The app.** Usually they run `FMAnalyst.exe` from the latest GitHub release: its first start shows a setup screen
   (club exactly as FM writes it, currency, FM27 Exporter's folder) and makes the save itself. From source it's
   `FMAnalyst.bat` (or `python app.py`), which needs Python 3.11+ and pywebview.
2. **Another save.** The app's **Saves** button makes one; `score.new_save(name, club)` does the same from code. Every save
   scores with the same attribute weights: `weights/default.toml`'s, or yours from the Attributes page (`saves/attributes.toml`).
3. **FM27 Exporter.** BepInEx 6 and the plugin in FM's game folder (README step 1). F8 exports the list under the mouse.
4. **Views.** With FM closed, copy the files from `views/` into the `views` folder in FM's data folder (`Documents\Sports Interactive\Football Manager 27`,
   possibly under OneDrive), never replacing a file of theirs without asking. The squad view needs every attribute
   (Ctrl + F8 on the list adds them) and, ideally, FM's **Position** column.
5. **First import.** F8 on their squad screen, then **Import file…** in the app.
