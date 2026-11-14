# FMAnalyst

Squad, player and staff analysis for FM27.

Scores Football Manager players on weighted attributes and tracks them over game time.
FM has no data export, so the user takes screenshots of in-game player tables and an agent
transcribes them to CSV. `score.py` turns those into twelve pages per save (keys 1–9, 0, then a and g):

1. **squad**: the club the user manages, with the Lineup panel above the table (template.html's `renderLineup`,
   data `LINEUP` from `grid()`, collapsible) and beside it the Formations panel (`FORMATIONS`, `formations()`/`best_xi`: each
   of 20 shapes' best XI and a second XI by greedy best pairs, at positions the player plays; `renderFormations`). Lineup: the squad by formation slot (save.toml's `grid`): starter, backup, then third (to sell or loan out) by position score, then the
   youth (`youth_age` and under, default 18: the Youth column) by vs Age, so the pipeline and who's blocking the next intake show at a glance
2. **analysis**: the squad position by position as the Lineup places them, ranked by Speed (Pace + Acceleration), the
   position score or any attribute (template.html's `renderAnalysis`), with a summary ranking the positions and each
   compared with the average of imported players whose best position it is (`analysis()` in score.py)
3. **development**: two squad snapshots compared (`development()`; template.html's `renderDevelopment`): score and
   vs Age change, attributes up/down, risers and fallers, squad attribute totals, joined/left. A squad import is
   dated with the export's time (`2026-11-14T0032_<club>_outfield.csv`, `obs_parts`), so each export is a snapshot.
4. **charts** (menu: Market): imported players by position, with the squad always highlighted: role scores, score vs wage,
   score vs average rating, and an attribute by position and vs rating (a menu of the chosen position's top attributes by weight: 8 outfield, 5 GK; `CHART_ATTRS`)
5. **elsewhere**: every other player they've looked at
6. **last**: just the players from the most recent set of screenshots (e.g. one transfer search)
7. **filters**: per position, the squad's best players and suggested minimums for FM's player search
8. **staff**: every staff member observed, scored for each role (manager, coach groups, scout, physio…)
9. **last_staff**: just the staff from the most recent set of staff screenshots
0. **staff_filters**: per staff role, the club's best staff and suggested minimums (as FM's words) for FM's staff search
a. **attributes**: the save's position weights, editable (template.html's `renderAttributes`, data from `attributes_page`).
   In the app, Save writes them to `saves/<save>/weights.toml` and sets save.toml's `weights = "custom"` (`score.CUSTOM`;
   `load_weights`/`weights_path` resolve it, so custom weights stay private to the save), then rescores; the menu switches
   sets (app.py `use_weights`); Export/Import write and read a plain table, a row per attribute and a column per position
   (`weights_table`/`read_weights_table`: .txt tab-separated, .csv comma; import also takes semicolons, decimal commas, .toml).
g. **guide**: how to use each page (`GUIDE` in score.py, drawn by template.html's `renderGuide`), ending with an "About and your
   settings" tile (`settings_tile`: version, project link, save, club, weights, currency, folders). There's no settings page any more; write_pages
   deletes page files it no longer writes (an old settings.html).

The menu shows them in this order (`PAGES`): Squad, Analysis, Lineup, Charts (your team) · All players (elsewhere), Last
import (last), Player search (filters) · All staff (staff), Last staff import (last_staff), Staff search
(staff_filters) · Attributes, Guide (`PAGE_LABELS`; keys 1–9, 0, then `PAGE_KEYS` a and g; groups `NAV_GROUP_STARTS`).
The file names (squad.html, grid.html…) and the names above stay as they are.

Runs on macOS, Windows and Linux: stdlib Python 3.11+ only (`python3` here; on Windows it may be `python` or `py`).

## Files

| Path | Purpose |
|---|---|
| `config.toml` | This computer's settings: `active_save` (the save being worked on; only ever touch that save), `screenshots_dir`, `currency`. Gitignored; `python3 score.py init` writes it, `config.example.toml` shows it. |
| `saves/<save>/save.toml` | `weights` (`default`, or `custom` for the save's own weights.toml), `managed_club` (exactly as FM shows it), `short_name` (page 1 label), `division` (the managed club's, as FM shows it; page 6's default), `last_batch` (obs files for page 4), `last_staff_batch` (staff files for page 8), `last_staff_names` (staff already observed to add to page 8), `youth_age`, `grid` (page 2's formation slots: `label`, weights `pos`, `left = true` for left-footed only), `grid_pins` (player → slot label), `grid_senior` (youngsters moved up to the senior columns), `grid_youth` (intake players kept: youth columns), `grid_exclude` (left off the grid) `released` (players gone since the latest squad screen) `signed` (players joined since) `recalled` (back from loan since) and `aliases` (a name FM cut off in an obs file → the full name). |
| `saves/<save>/obs/*.csv` | Observations: `<YYYY-MM-DD>_<source>_<view>.csv`, one per screenshot set. Append-only. |
| `saves/<save>/staff/*.csv` | Staff observations: `<YYYY-MM-DD>_<source>.csv` (`search-coach`). Append-only. |
| `weights/default.toml` | The one built-in weights set: position weights (`[outfield.*]`, `[gk]`), age benchmarks, and staff scoring: `[staff_words]` (FM's words → numbers) and `[staff.<role>]` (the role's column label, then attribute weights). FM's 10 position groups (DC, DL/DR, WBL/WBR, DM, MC, ML/MR, AMC, AML/AMR, ST, GK); outfield weight = CA cost (`docs/ca_costs_fm27.csv`) × (impact + 1), scaled to a top weight of 10 per position; impact = the 9→15 gain in `docs/attribute_impact_test.csv` (harvestgreen22's FM27 test), losses as 0. Outfield attributes limited to the 18 the `player_search_outfield` view shows, so view exports score at every position. GK: CA cost only. `weights_file(name)` looks in `ROOT/weights` first, then `APP/weights`. |
| `saves/<save>/weights.toml` | The save's own weights, written by the Attributes page's Save (save.toml `weights = "custom"`); any other `weights` value means the default. |
| `weights/staff_notes.md` | Notes on the staff weights: sources, FM27 key attributes, calibration. |
| `views/`, `player search filters/`, `staff search filters/` | FM27 views and search filters (`.fmf`), named like FM's own folders. |
| `score.py` | `python3 score.py [--save S] [--open PAGE]` → `out/<save>/*.html`. Also `init` (first-time setup) and `screenshots` (list new ones). Stdlib only. `APP` = the program's files (weights, templates, theme), `ROOT` = the data (config.toml, saves/, out/): the same folder from source; in the packaged .exe, `APP` is PyInstaller's unpack folder and `ROOT` is `Documents\FMAnalyst` (`documents()`: Windows' known Documents folder). `FMANALYST_HOME` overrides `ROOT` (for tests). `weights_file(name)` looks in `ROOT/weights` first, then `APP/weights`. |
| `import_export.py` | `python import_export.py EXPORT --date D [--source S]`: an FM27 Exporter file → obs CSVs (gk/outfield split), sets `last_batch`. A staff export (`staff_everything` view; also named `Player Search …`, told apart by its `Person`/`Preferred Job` columns) → `staff/<date>_<source>.csv` with headers expanded (`STAFF_RENAME`), FM's words kept, blank Club = `Free agent`; sets `last_staff_batch`. Your club's All Staff screen (`Name`, `Club Job`, no Club column) is staff too, filed as `<club>-staff` (like a squad screen); its four views (Coaching, Mental, Medical, Scouting) each hold some attributes, so staff imports add a person's new values to their row of the day (`write_obs(add_columns=True)`) instead of replacing it. |
| `app.py` | Windows app (needs `pywebview`). Its window title is `FMAnalyst <VERSION> — Save: <save>` (`VERSION` in score.py, also shown in the guide's About tile with `PROJECT_URL`: bump it with each release, tag `v<VERSION>`). An Import file… button on every page. The chosen export is imported straight away (`import_export.import_file`), dated with the date in FM27 Exporter's file name (`export_date`; the file's own date if there's none: the real date, not FM's, but in import order): your squad if the file name starts `First Team`/`Squad`, else `search-<file name>-<the exporter's HH-MM-SS>`, so each export is its own list (page 4 shows just it) and re-importing a file updates it. Then it scores, commits `saves/`, and shows the last page. On the elsewhere and staff pages, Clear old lists (click twice) moves search files to `obs/archive/` or `staff/archive/` (score.py doesn't read subfolders), keeping the squad, the latest batch, intakes and files with anyone save.toml names; it rolls back if scoring fails. With no `config.toml` (or its save missing) it opens a setup page instead: club, currency, exports folder, desktop shortcut (packaged only); `score.write_config` + `score.new_save`. It rescores the save on every start, and writes errors it can't show to `app.log` in the data folder. Its Saves button lists saves to switch to (`score.set_active_save`) and creates new ones (`score.new_save`, shared with `init`). `exports_dir` in config.toml sets the picker's folder (else found under Documents). |
| `FMAnalyst.bat` | Windows launcher for `app.py` from source (double-click): installs pywebview if missing, starts the app with `pythonw`. CRLF (`.gitattributes`). |
| `build.py` | `python build.py` → `dist/FMAnalyst.exe` (PyInstaller, one file, windowed, `docs/FMAnalyst.ico` as its icon): app.py plus `template.html`, `charts.html`, `theme.css`, `logo.svg` and `weights/`. Publish it as the `FMAnalyst.exe` asset of a GitHub release: README's download link points to `releases/latest/download/FMAnalyst.exe`. `dist/`, `build/` and `*.spec` are gitignored. |
| `template.html` | Every page but charts. Its `?` overlay is the reference for keys and filter syntax. |
| `charts.html` | Page 6: hand-drawn SVG charts. |
| `theme.css` | FM27-style theme (violet panels, a near-black top bar, purple accent, Barlow Condensed headings, Inter text), inlined into every page; app.py's own buttons, panels and setup page use the same colours. Attribute values are coloured like FM27 (1–5 grey, 6–9 white, 10–14 yellow, 15+ green: `attrTier` in template.html); scores and vs Age stay relative (`tier`). |
| `logo.svg` | The shield icon in light colours: score.py inlines it at the left of every page's menu (`BRAND`, with the name) and as a faint watermark in the bottom-right corner (`.watermark` in theme.css). |
| `docs/logo-icon.svg`, `docs/FMAnalyst.ico`, `docs/logo.png` | The navy original of the icon, the .exe's icon (16–256 px, built with `--icon`), and the README's wordmark. |
| `out/` | Generated; gitignored. |
| `README.md` | For people: what this is and how to set up a new save. |
| `CLAUDE.md` | Just imports this file. |

## Adding screenshots

The user says something like "new screenshots".

1. **Find them:** `python3 score.py screenshots` lists images from the last 10 minutes in `screenshots_dir`
   (`--minutes N` to look further back). The OS takes a few seconds to write them, so if they're missing wait ~5s and look again.
   Open each and skip any that aren't FM.
2. **Read from each screenshot:**
   - **Game date**, top right, day first: `30/6/2025` → `2025-06-30`.
   - **Managed club**: the crest at FM's far top-left.
   - **View**: if the columns are goalkeeping attributes (Handling, Reflexes…) it's `gk`, otherwise `outfield`.
   - **Club**: on a squad screen, the name above the table; on search results, the Club column
     (`-` = `Free agent`).

   If the date or club isn't visible (cropped), ask; don't guess.
3. **Stop and ask** if the crest doesn't match `managed_club`, or the date is earlier than the latest
   in `obs/`. Either means a different save.
4. **Transcribe** to `obs/<date>_<source>_<view>.csv`, where source is the club slug (`my-club`) or
   `search-<what>` (`search-cb`). Copy the header from an existing file of the same view.
   - Columns: `Name`, `Club`, `Age`, `Left Foot`, `Wage`, `Transfer Value`, `Loan`, then attributes as integers.
     If the view shows `Minutes`, `Rating` (average rating) and `Division`, add them after `Loan`, division in full
     (`Sky Bet League One`); they feed page 6. One file per division: `search-league-one`, `search-championship`.
     Copy `Minutes` exactly as shown, even when it breaks FM's sort order. Ignore `Height`.
   - An attribute you can't read (covered by something): leave it empty; the previous value carries forward.
   - A `gk` file holds only goalkeepers (everyone in one is scored as a keeper). If FM's GK view lists outfielders too, skip them.
     Likewise leave keepers out of an `outfield` file (they show 1–3 for Finishing/Tackling) and ask for the GK view.
   - `Loan` (squad screens): `in` if the name is blue (on loan from another club), `out` if red (out on loan), `trial` if yellow (on trial), else empty.
     Wage and value exactly as shown (`£1.5K p/w`, `N/A`, `£16K - £150K`), empty if not shown.
   - `Left Foot` in full: `Very Weak` … `Very Strong`. Expand truncations (`Rea…` → `Reasonable`);
     `Ve…` is ambiguous, so leave it empty.
   - Names exactly as shown, truncation included (`Antony Papadopoul…`): the name is how a player is
     matched over time. When the full name is known (e.g. from a transfer message), add it to `aliases` in save.toml
     (`"Antony Papadopoul…" = "Antony Papadopoulos"`) and use the full name everywhere else in save.toml. Map truncated headers to full attribute names.
   - Merge scrolled screenshots of one list into one file, de-duplicated. More screenshots of a list
     that already has a file (same date, source, view) merge into it.
   - Spot-check rows against the image; misreads are the main risk.
5. Set `last_batch` in `save.toml` to the file(s) from this update, and remove any `last_names`.
   If the screenshot is just a list of names of players already observed (e.g. a shortlist), set
   `last_names = [...]` instead (names exactly as in the obs files): page 4 then shows only them.
6. Run `python3 score.py --open last` and commit the save data: `git -C saves add -A && git -C saves commit -m "<save>: <what> <date>"`.
7. Report notable players: top of the new batch, big changes since a player's previous observation.

## Adding staff screenshots

The user's in-game staff view (`staff_everything`) shows Person, Preferred Job, Club, Personality, then FM's abbreviated
attributes. Same steps as players (date, crest check, `score.py screenshots`), but:

- Write `staff/<date>_search-<job>.csv`: `Name`, `Club` (blank = `Free agent`), `Age` (empty if not shown), `Job`
  (Preferred Job, expanded: `Fitness …` → `Fitness Coach`), `Personality` (expanded: `Fairly Dete…` → `Fairly Determined`),
  then attributes by full name with FM's word in full (`Compe…` → `Competent`, `Very G…` → `Very Good`). Copy the header from an existing file.
- Column map: Det Determination · Mot Motivating · **JCA Judging Player Ability** · Mgm People Management · Auth Authority ·
  TCo Tactical Knowledge · **JPA Judging Player Potential** · Tec Technical · Yng Working With Youngsters · Pos Possession ·
  Tct Tactical · Att Attacking · Def Defending · Phy Physiotherapy · Fit Fitness · SpS Sports Science · Ana D Analysing Data ·
  Set Set Pieces · GK Goalkeeping. The text is small: crop the screenshot to read it, with whatever's installed: `sips -c H W --cropOffset Y X in --out crop.png`
  (macOS), `magick in -crop WxH+X+Y crop.png` (ImageMagick, any OS), or Pillow if it's there. Write crops to a temp folder, not the repo.
- Set `last_staff_batch` in `save.toml` and remove any `last_staff_names` (the user's "add X to last staff": names exactly as in the staff files), run `python3 score.py --open last_staff`, commit.
- Scoring: words → range midpoints (`[staff_words]`: Good = 13, Average = 10.5…), role score = Σ(value × weight) / Σ(weights).
  Each `[staff.<label>]` table in the weights file is a role: the label is its column, and `[staff_names]` gives its
  full name (shown on page 9). A new role needs both.

## Starting a new save

The user says "help me set up a new save", wants another save, or there's no `config.toml` yet: follow
`.claude/skills/new-save/SKILL.md`.

"Switch to my <club> save" = set `active_save`, run `score.py --open squad`.

If FM renames an attribute, use the new name in the CSV header and in the weights file.

## Scoring

- **Position score** = Σ(attribute × weight) / Σ(weights), on FM's 1–20 scale. Outfield players get
  every `[outfield.*]` position, keepers (anyone in a `gk` observation) only `GK`. **Best** = the highest.
  A position is scored when attributes carrying 85% of its weight are known (`MIN_KNOWN_WEIGHT`), over those known;
  `import_export.py` leaves out players it couldn't score at any position (and says so), and spots keepers from scouted ranges too.
- **FM's positions:** an export with FM's Position column (`ST (C)`, `D/WB (R), M (RC)`) is kept (obs column `Position`);
  `fm_positions` turns it into the weights' groups (`plays` on the row). Best is then the highest score among those, and
  the Lineup and Formations place a player only where FM says they play. Without it: his best position and its
  neighbours (`NEAR`), so a midfielder isn't put at full-back. Formations rank complete shapes first (`short`), then XI.
- **vs Age** = best ÷ `age_benchmark` for their age (clamped to the table's ends); 100% = on track.
- **Over time:** each attribute is its latest known value; club, age, wage and value come from the latest
  observation. Enter/double-click a player for their history by date.
- **Foot** = Left when the left foot is Fairly Strong or better (`LEFT_FOOTED` in `score.py`), else Right (the export only
  shows the left foot); filter `foot=left`. The row keeps `left` (yes/no) for the Lineup's left-footed slots.
- **Left the club:** a managed-club player missing from the latest squad screen (non-`search-` file) moves to
  elsewhere as `left <club>`. A youth or reserve squad screen (source ending `-u18s`, `-u21s`, `-reserves`: `PARTIAL_SQUAD`)
  shows only part of the club, so it updates its players but doesn't count as the squad screen. The user saying a player's gone ("sold X", "released X") before the next squad screen: add them
  to `released = [...]` in save.toml (name as in the obs files); don't edit obs files.
  Likewise a signing ("signed X") goes in `signed = [...]`: they count as the managed club's until a newer squad screen. A player back from loan ("recalled X") goes in `recalled = [...]`: clears their `out` flag.
  When a new squad screen comes in, empty `released`, `signed` and `recalled`.
- **Loan** flags show next to names, from the latest observation that has a `Loan` column. Filter with `loan-in` / `loan-out` / `trial`.
- **Rating / minutes / division:** from the latest observation only (they're per season); rating and minutes show on elsewhere and last.
- **Charts page:** players grouped by best role. "compare with" picks the baseline (blue): Last player import (the last page's
  players, the default), All imported players, or a division with 20+ players rated in their latest observation (a
  league search); the managed club's squad is always highlighted (orange). The two
  average-rating charts only show with rated players.
  r = correlation over the league players; the dashed line is their least-squares fit (on log wage for wage).
- **Lineup (on the squad page):** players are placed best first (by Score), each at their best position in the formation: its first
  free Starter, else Backup, else Third. Third holds any number, spread over that position's slots: players to sell or
  loan out. Then an empty Starter or Backup slot (a position nobody is best at: DM, MC, AMC…) is filled from Third by
  whoever scores best at that slot's position (best pair first, Starters before Backups; pinned players stay), shown
  with ↑ and the position they came from. A left-footer prefers the left-footed slot. Senior = older than `youth_age` (default 18), by position score; youth
  (one Youth column, 18 and under) placed by that position's score ÷ age benchmark. Every entry shows score and age. Out-on-loan players aren't placed.
  The latest youth intake (players in the newest obs files whose source contains `intake`, e.g. `search-youth-intake`)
  gets its own Intake columns, placed like the youth, until a squad screen shows them (then they're youth, or gone) or they're in `grid_youth` (the user's "keep X": youth columns now). With no intake, those columns are hidden.
  `grid_pins` in save.toml (`"Full Name" = "slot label"`) overrides a player's slot: the user's own calls on who plays where. `{ slot = "M C", col = "third" }` also fixes the column, and `row = 2` picks the second slot of that label (rows counted down the grid)
  (starter / backup / third); those players are placed first. `{ col = "third" }` alone: their best position. A pinned player out
  on loan is placed too. `grid_exclude` leaves players off the grid altogether (e.g. contracts running out). Everyone still goes in by score, but unpinned
  players avoid (at the same depth) slots a pinned player is still to fill.
- **Filters page:** the squad's top 3 per position (top 1 for GK). **Search ≥** = their rounded average, for the fewest
  top-weighted attributes making up 85% of the position's weight, 3 to 8 of them (`search_attrs`, `SEARCH_WEIGHT_SHARE`),
  for use as minimums in FM's search; the rest are dimmed.
- **Staff filters page:** the same per staff role (the same `search_attrs` rule): the top 3 of all imported staff (any
  club, shown with their clubs) by role score, so it rates what staff searches found and gives FM's staff search its
  minimums. Values and **Search ≥** show as the `[staff_words]` word nearest the value. With no staff, the page says how to
  import them.

## Conventions

- Two git repos. The code repo (this folder) is public on GitHub and ignores `saves/`, `config.toml` and `out/`.
  `saves/` is its own git repo, local only, never pushed: the user's save data stays private. Commit data there
  (`git -C saves …`) and code here, separately. Never add personal details (email, real save names) to the code repo.
- Display: scores `0.0`, vs Age `xx%`.
- In `weights/*.toml`, quote keys with `/` or spaces: `[outfield."WB/6"]`, `"First Touch" = 1`.
- `score.py` and `import_export.py`: stdlib Python, no dependencies. `app.py` needs pywebview; `build.py` needs PyInstaller.
