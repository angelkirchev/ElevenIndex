# ElevenIndex: notes for working on it

Brand: the app is **ElevenIndex**; its mark is **XI INDEX** (a cream X and a lime I, `logo.svg`), slogan
"Rate smarter. Build better.", description "Squad & staff scoring for Football Manager 27". Palette in `theme.css`:
near-black #0f1412, cream #f2efe7, lime #c5f04f (accent), sky blue #8bd3f7 (second accent), green bar #1b6b3a.

ElevenIndex is a Windows app for Football Manager 2027. The user exports lists from FM with the FM27 Exporter plugin (F8),
imports them in the app, and gets pages that score every player at each position and every staff member for each job,
and track them over time. Everything runs locally; the user's data never leaves their computer.

## How it fits together

- `app.py`: the window (pywebview). Import file… → `import_export.import_file` → `score.build` + `score.write_pages` →
  shows the page. Also the first-run setup screen, the new-save and error screens (`screen()`; a missing page opens the error one), the Saves panel (switch, new, delete: a deleted save waits in `saves/_deleted` for 14 days, restorable, then
  `purge_deleted` removes it), Menu → Theme… (five colour themes: `THEMES` in score.py, `:root[data-theme]` in
  theme.css, config.toml's `theme`, stamped on each page's `<html>`), Clear old lists, and the Attributes page's
  save / switch / import / export. Its title is `ElevenIndex <VERSION> — Save: <save>`.
- `import_export.py`: an FM27 Exporter file (CSV or HTML) → observation CSVs in the save. Players go to
  `saves/<save>/obs/<date>_<source>_<gk|outfield>.csv`, staff to `saves/<save>/staff/<date>_<source>.csv`.
- `score.py`: reads a save's observations, scores them, and writes `out/<save>/<page>.html` from `template.html`
  (every page but Research) and `charts.html` (Research), with `theme.css` and `logo.svg` inlined. Standard library only.
  `python score.py [--save NAME] [--open PAGE]` rebuilds a save's pages.
- `build.py`: `dist/ElevenIndex.exe` with PyInstaller (one file, windowed, `docs/ElevenIndex.ico`), holding app.py, the two
  templates, the theme, the logo and `weights/`, and `dist/ElevenIndex-FM27-views-and-filters.zip` (`fm_zip`: `views/`,
  `player search filters/` and `staff search filters/` in FM's own folder names, to unzip into
  `Documents\Sports Interactive\Football Manager 27`). A release publishes both as assets (`ElevenIndex.exe`,
  `ElevenIndex-FM27-views-and-filters.zip`), which README's download links point to.
- `views/`, `player search filters/`, `staff search filters/`: the user's FM27 views and search filters (`.fmf`); views give
  FM's lists the columns the imports need.
- `ElevenIndex.bat`: runs the app from source (installs pywebview when it's missing). CRLF line endings.

Two folders: `score.APP` holds the program's files (PyInstaller's unpack folder in the .exe), `score.ROOT` the user's
data: `Documents\ElevenIndex` in the .exe (`documents()` finds Documents, OneDrive included), the source folder otherwise,
and `ELEVENINDEX_HOME` when set (tests). Data: `config.toml`, `saves/`, `out/`, `app.log`.

## Pages

The top menu is `MENU` in score.py: keys 1-4, then A and G. **Squad** (1), **Players** (3) and **Staff** (4) are groups
whose pages show as sub-tabs under the bar (`SUB_LABELS`); a group's key opens it on its first page (Lineup, Last import)
and, pressed again, the next sub-tab (`NAV_SCRIPT`, `fmNavTarget`). `PAGE_LABELS` holds each page's full name. Each list
page has a line about its list at the strip's right (`with_info`).

| Key | File | Menu | What it shows |
|---|---|---|---|
| 1 | squad | Squad › Lineup | a pitch for a formation (`pitchSlots`); click a position for everyone who plays it and the best on your lists (`targets`); the squad table below |
| 1 | formations | Squad › Formations | the 20 shapes ranked (`formations`); one on a pitch with its XI, backups and numbers |
| 1 | analysis | Squad › Analysis | the squad position by position, ranked by score (or an attribute), against imported players |
| 1 | development | Squad › Development | two squad snapshots compared |
| 2 | charts | Research | per position, the listed players against your best; then one graph at a time |
| 3 | elsewhere | Players › All players | everyone imported who isn't at the club |
| 3 | last | Players › Last import | the latest player import only |
| 3 | filters | Players › Search | per position, the squad's best and suggested search minimums (Filter) |
| 3 | compare | Players › Compare | two players side by side (`compare_page`; two picks in localStorage, by the icon by a name, x or + Compare) |
| 4 | staff | Staff › All staff | every staff member, scored for each job |
| 4 | last_staff | Staff › Last import | the latest staff import only |
| 4 | staff_filters | Staff › Search | per job, the best staff imported and suggested minimums |
| a | attributes | Attributes | the attribute weights (1-20), editable, exported and imported as a list |
| g | guide | Guide | how to use everything (`GUIDE`; Menu → About shows `settings_tile`) |

`write_pages` deletes page files it no longer writes. The version (`VERSION`) and project link (`PROJECT_URL`) are in
score.py; bump `VERSION` with each release (tag `v<VERSION>`).

## Imports

- **Squad**: a file whose name starts with First Team or Squad. Its source is the club's slug, and its date includes the
  export's time (`2026-11-14T0032_<club>_outfield.csv`, read by `obs_parts`), so every squad export is its own snapshot.
- **A search or shortlist**: source `search-<file name>-<HH-MM-SS>`, so each export is its own list; re-importing the same
  file updates it. `save.toml`'s `last_batch` names the latest one (page 6).
- **Staff**: told apart by the Person, Preferred Job or Club Job columns. Your club's staff screen (no Club column) is
  filed as `<club>-staff`; its several views each add their attributes to the person's row of the day.
- Dates come from the exporter's file name (`export_date`), else the file's own date. They're real dates, not FM's.
- Players whose known attributes can't score any position are left out (and the import message says so). Keepers are
  anyone in a goalkeeping export, spotted from their attributes, scouted ranges included.
- FM's Position column, when the view has it, is kept (`Position`).

## Scoring

- **Position score** = Σ(attribute × weight) ÷ Σ(weights) over the attributes known, on the 1–20 scale (`weighted`), when
  those carry at least 85% of the position's weight (`MIN_KNOWN_WEIGHT`). Outfield players get every outfield
  position, keepers only GK.
- **Over time** (`carry_forward`): an attribute keeps its newest known value; club, age, wage, value and loan come from the
  newest observation that shows them.
- **Best**: the highest score, among the positions FM says he plays when the Position column was exported
  (`fm_positions`), else among all.
- **vs Age** = the best score ÷ the expected score at his age (`age_benchmark`, clamped to the table's ends).
- **Foot**: Left when the left foot is Fairly Strong or better (`LEFT_FOOTED`), else Right.
- **Staff**: FM's words → the middle of their 1–20 range (`[staff_words]`); job score = the same weighted average, every
  weighted attribute known. Each `[staff.<label>]` table is a job; `[staff_names]` gives its full name. How the staff
  weights are set: `weights/staff_notes.md`.
- **Weights**: one attribute weight per attribute, 1–20, one table for every position, keepers' too (`[attributes]`):
  `weights/default.toml`'s (see its header and `docs/README.md`), or your own in `saves/attributes.toml`
  (`ATTRIBUTES_FILE`), one table for every save, written by the Attributes page. A position's weight for an attribute =
  its CA cost there (`docs/ca_costs_fm27.csv`, `COSTS_FILE`) × its attribute weight, scaled to a top of 10 (`compose`);
  `load_weights` gives the composed `outfield` / `gk` tables the rest of the code reads.

## Squad page details

- **The old Lineup table** (`grid`, still used by Analysis for each player's role): slots from save.toml's `grid` (else one per position). Seniors (older than `youth_age`, default 18)
  go best first to their best position in the formation: its first free Starter, then Backup, else Third (surplus).
  Empty Starter/Backup slots are then filled from Third by whoever plays them best, marked with where he came from.
  Youngsters go to one Youth list per slot, best for their age first; a new intake (a file whose source contains
  `intake`) gets an Intake column until a squad export shows it. Without FM's positions a player only goes to his best
  position or its neighbours (`NEAR`). save.toml's `grid_pins`, `grid_senior`, `grid_youth` and `grid_exclude` override.
- **Formations** (`FORMATIONS`, `formations`, `best_xi`): for each of 20 shapes, the best XI and a second XI (greedy:
  best player-position pair first, each player once, where he plays), their averages and the weakest spot. Complete
  shapes first.
- Who's at the club: the newest full squad export (not a `-u18s`/`-u21s`/`-reserves` one, `PARTIAL_SQUAD`) decides;
  save.toml's `released`, `signed` and `recalled` cover moves since (`squad_moves`).

## Conventions

- Two git repositories: this one (public, on GitHub) and `saves/` (local only, never pushed: the user's data). Commit
  data with `git -C saves …`. Never put personal details or real save names in this repository.
- score.py and import_export.py use only Python's standard library; app.py needs pywebview, build.py PyInstaller.
- In TOML, quote keys with `/` or spaces: `[outfield."DL/DR"]`, `"First Touch" = 1`.
- Shown as: scores `12.3`, vs Age `95%`.
- Test changes on a copy of a save (`ELEVENINDEX_HOME` pointing at a copy of the data folder).
