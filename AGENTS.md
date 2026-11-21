# ElevenIndex: notes for working on it

Brand: the app is **ElevenIndex**; its mark is **XI INDEX** (a cream X and a lime I, `logo.svg`), slogan
"Rate smarter. Build better.", description "Squad & staff scoring for Football Manager 27". Palette in `theme.css`:
near-black #0f1412, cream #f2efe7, lime #c5f04f (accent), sky blue #8bd3f7 (second accent), green bar #1b6b3a.

ElevenIndex is a Windows app for Football Manager 2027. It reads the user's FM27 save games (.fm) with our own reader
(`fm_save.py`), and gets pages that score every player at each position and every staff member for each job, and track
them over time. No mods, no exports. Everything runs locally; the user's data never leaves their computer.

## How it fits together

- `app.py`: the window (pywebview). Read save game… (`pick_file`, key I, or a dropped .fm) → `import_now` →
  `import_export.import_save` → `score.build` + `score.write_pages` → shows the first tab, Club › Overview (`START_PAGE`). Also the first-run setup screen, the new-save and error screens (`screen()`; a missing page opens the error one), the Saves panel (switch, new, delete: a deleted save waits in `saves/_deleted` for 14 days, restorable, then
  `purge_deleted` removes it), the Menu (under the logo, key M; recent save games from FM's games folder `games_dir` (found by `fm_folders`: config.toml's `games_dir`, Documents, OneDrive, Public Documents, Steam Cloud's `userdata\…emote`; the newest save wins; setup shows what it found, `folder_report`; Menu → Change FM's saves folder…), switch save, Back up / Restore a backup, the update check `check_update` (the website's version.json, `PROJECT_URL`)), the save watcher (`watch_exports`: a newly saved .fm offers Read it, or is read by itself with Menu → Auto-read on, config.toml's `auto_import`), drag and drop (`on_drop`, .fm only), Menu → Theme… (five colour themes: `THEMES` in score.py, `:root[data-theme]` in
  theme.css, config.toml's `theme`, stamped on each page's `<html>`), and the Attributes page's
  save / switch / import / export. Its title is `ElevenIndex <VERSION> — Save: <save>`.
  On start it opens last time's pages at once and rescores behind them (`rescore_on_start`, under `self.lock`), reloading
  the page only when the pages changed; `write_pages` writes each page to a .tmp and swaps it in, and the same data gives
  byte-identical pages. Closing hides the window, waits for `self.lock`, then ends the process (`quit`).
- `import_export.py`: `import_save`: a save game → observation CSVs in the save. Your players go to
  `saves/<save>/obs/<file time>_<club>_<gk|outfield>.csv`, everyone else to `<file time>_search-save-game_*.csv` (the
  newest save's only), your staff to `staff/<file time>_<club>-staff.csv`, other staff to
  `staff/<file time>_search-save-game-staff.csv`, the budgets to `finances.json`. Exporter files are no longer read in
  (old ones on disk still count).
- `fm_save.py`: our own FM27 save reader (Python 3.14+ `compression.zstd`, standard library, read-only), written from
  the file itself, no third-party code. Menu → Import save game… (`pick_save_game`, or drop a `.fm`) →
  `import_export.import_save`: the human manager's club (from `save_game_summary`, must match the open save's club),
  its players (first team, youth teams, loans: `Loan` in/out) with exact attributes, wage, value, position, foot,
  `Height` (cm) and `Contract` (end date; both shown only when some row has them, `OPTIONAL_COLS`), and
  its staff with attributes as FM's words and `Job` (the board left out: `BOARD_CODES`, a staff record's byte 31 = his
  preferred job, for those out of work), filed as `<file time>_<club>_gk|outfield.csv` and
  `<file time>_<club>-staff.csv` (scouting knowledge isn't decoded: everything is read exactly). Also read (checked against Genie Scout on the same save): CA/PA (u16s at
  record+25/+27 after the three reputations at +19/21/23; staff at base+27/29), the 5 hidden attributes (`HIDDEN`),
  the 8 personality traits after the person block (`TRAITS`), the club's budgets (`finances` → `finances.json`, shown
  on Squad's info line). Hidden values are shown (CA/PA columns, "Hidden in FM" on a player's details), never scored.
  Records: x+18 = 1 staff, 2 player, 3 a player who also carries staff data (player-coaches, older players: a player's
  layout); a contract's job low byte 1 = player, 3 = player-coach (`PLAYER_JOBS`). A player-coach (record 3, contract job 3)
  is also read as staff, Job "Player / Coach": his staff data sits inside the player record, laid out like a staff
  record (`staff_block` finds it by its shape), with his contract and name from the player side (`add_staff`). An end year of 1900 = no end date
  (amateur, non-contract: no guessing then). A club's professional status is the byte after its short name (1
  professional, 2 semi-pro, 3 amateur): each deal's "Deal" column (`deal_kind`) is Full-time at a professional club,
  else Part-time, or with no end and no wage Amateur at an amateur club, else Non-contract (a loanee's: his own club's). Doctors
  (`DOCTOR_JOBS`) get no wage, end or type, as FM shows N/A for them.
  Profiles show it as Type; older imports without it fall back on `AMATEUR_CLUBS` (SITE.amateur, `dealText`).
  Staff "Tct" = Tactical Knowledge, "TCo" = Tactical (coaching). With config.toml's `save_everyone` (on by default)
  a save import also files every other men's player in the save as one list (`search-save-game`, the newest save's
  only, from the manager's own football: men's or women's, by `womens_clubs`; namesakes get " (Club)"), plus fitness, match exercise and
  jadedness, the 15 position ratings (`Familiarity`) and staff tactical styles (`STAFF_PREFS`). ~15 s for a big save.
- `score.py`: reads a save's observations, scores them, and writes `out/<save>/<page>.html` from `template.html`
  (every page but Research) and `charts.html` (Research), with `theme.css`, `logo.svg` and the fonts inlined
  (`fonts/`: Inter and Barlow Condensed, Latin + Latin Extended, OFL; `font_css`), so pages need no internet. Standard library only.
  `python score.py [--save NAME] [--open PAGE]` rebuilds a save's pages.
- `build.py`: `dist/ElevenIndex.exe` with PyInstaller (one file, windowed, `docs/ElevenIndex.ico`), holding app.py,
  fm_save.py, the two templates, the theme, the logo, `fonts/` and `weights/`. A release publishes `ElevenIndex.exe`,
  which README's download link points to.
- `ElevenIndex.bat`: runs the app from source (installs pywebview when it's missing). CRLF line endings.

Two folders: `score.APP` holds the program's files (PyInstaller's unpack folder in the .exe), `score.ROOT` the user's
data: `Documents\ElevenIndex` in the .exe (`documents()` finds Documents, OneDrive included), the source folder otherwise,
and `ELEVENINDEX_HOME` when set (tests). Data: `config.toml`, `saves/`, `out/`, `app.log`.

## Pages

The top menu is `MENU` in score.py: keys 1-4, then A and G. **Club** (1), **Squad** (2), **Players** (3) and **Staff** (4) are groups
whose pages show as sub-tabs under the bar (`SUB_LABELS`); a group's key opens it on its first page (Overview, Lineup, Database, Your staff)
and, pressed again, the next sub-tab (`NAV_SCRIPT`, `fmNavTarget`). `PAGE_LABELS` holds each page's full name. Each list
page has a line about its list at the strip's right (`with_info`).

| Key | File | Menu | What it shows |
|---|---|---|---|
| 1 | club | Club › Overview | budgets, squad numbers, best XI, injured, contracts ending, best youngsters, staff cover (`club_page`) |
| 1 | contracts | Club › Contracts | budgets (`finances.json`), wage bill, contracts soonest to end first, wage per score point |
| 1 | medical | Club › Medical | injured, short of fitness, match practice, jadedness, injury-prone, everyone's condition (`renderMedical`) |
| 1 | transfers | Club › Transfers | budgets, who could go, each slot's best upgrade in the game and within budget (`renderTransfers`), bargains, signing ideas by category (`signingIdeas`: upgrade, affordable, successor, value, free; within reach, `reachable`) |
| 2 | squad | Squad › Lineup | a pitch for a formation (`pitchSlots`); click a position for everyone who plays it and the best in the game (`targets`); the squad table below |
| 2 | formations | Squad › Formations | 26 formations ranked (`formations`); one on a pitch with its XI, backups and numbers |
| 2 | depth | Squad › Depth | the two elevens, cover by line, ages, versatile players; each slot's starter, backup, drop, third choice, a better name in the game (`squad_tabs`) |
| 2 | analysis | Squad › Analysis | the squad position by position against the game, weak spot, age profile, the Squad plan |
| 2 | development | Squad › Development | two seasons compared (CA too); 3+ seasons side by side (`seasonTable`) |
| 2 | training | Squad › Training | each player's next FM27 individual routines (`ROUTINES`, `routinesFor`: weak attributes that count at his position, physical ones a little); the Team schedule (`teamSchedule`, `TRAIN_TEST`: harvestgreen22's training test ver5, `docs/training_schedule_test.csv`) |
| 1 | season | Club › Season | your players' match stats this season by competition (`season_page`, saves/<save>/stats/<season>.json from `fm_save.match_stats`) |
| 2 | youth | Squad › Youth | players 21 and under: CA, PA, room to grow, development factor; the intakes by year and what became of them (`youth_intakes`, ordered by FM's date) |
| 3 | elsewhere | Players › Database | every player in the game but yours (best `SHOW_MAX`) |
| 3 | toplists | Players › Top lists | the best, the most promising, bargains, by position group, with the score spread (`top_lists`) |
| 3 | charts | Players › Research | per position, the game against your best; then one graph at a time |
| 3 | compare | Players › Compare | two players side by side (`compare_page`) |
| 4 | team_staff | Staff › Your staff | your best at each job and the Staff plan (`staffPlanHtml`) |
| 4 | staff | Staff › Database | every staff member in the game (yours + the best `STAFF_SHOW_MAX`), scored for each job |
| 4 | staff_research | Staff › Research | per job, the staff in the game against who covers it (`staff_cover`) |
| 4 | staff_compare | Staff › Compare | two staff side by side (`staffCompare`) |
| a | attributes | Attributes | the attribute weights (1-20), editable, exported and imported as a list |
| g | guide | Guide | how to use everything (`GUIDE`; Menu → About shows `settings_tile`) |

A profile (`showDetail`) opens with a header card (`profileHeader`). A player's tabs: Profile (`playerProfile`: boxes
(`box`) for Personal, Contract, Condition, the attributes like FM's with key ones for a picked position, the Scout report
`scoutReportFull`, Ability with his rank `scout_marks`, Personality, Hidden attributes, Preferred moves), Analysis,
Positions (FM's 15 ratings `fmRatingsHtml` + our scores), History (`historyHtml`: by season, two picked). A staff
member's: Profile (`staffProfile`: attributes and tactical style as tables), Jobs, History.

Search ignores accents (`fold`: Lourenco = Lourenço); `reach` (keyword and Filter) = a free agent, or a fee within the transfer budget and a wage no higher than your best-paid player's (`SITE.reach`). Every list of players or staff has **Filter** by the search box (`filterInit`): a pop-up with tabs (`filterSpec`; players:
General, Contract & value, Ability, Attributes, Personality; staff: General, Ability, Attributes, Tactical style; both
Presets), kept per page, shown as chips under the bar. `notify()` shows a notice for what you do (Compare, Filter,
formation picked): the app's `fmNotice`, else its own.

**Checkpoints**: each save game read records FM's date (`fm_dates.json`, by the snapshot's stamp; older stamps are
looked up once in FM's games folder, `fm_dates`). `checkpoints`: 31 Dec (mid-season) and 30 Jun (season end), each the
first save read on or after it within a month, plus Now (the newest); Development compares any two, a profile's History
shows season ends and Now (`by_season`). The manager's own job (`YOU_ROLE`) is left out of Staff › Research and Your staff.
The app Menu: Your game (Read save game…, FM's saves folder, Auto-read, on by default), Game profiles…, ElevenIndex.

A list page's DATA is written small (`data_script`): floats to 3 decimals (`compact`), and attributes, hidden attributes,
FM's ratings and personal details as lists in one shared order, rebuilt in the page (`UNPACK`). `write_pages` deletes page files it no longer writes. The version (`VERSION`) and project link (`PROJECT_URL`) are in
score.py; bump `VERSION` with each release (tag `v<VERSION>`).

## Imports

- **A save game** (`import_save`): the club comes from the save. A save of the profile's club with another manager (save.toml's `manager`) or an FM date before the newest read asks first (`career_warning`: another career?; Read it anyway). The lists of everyone keep the newest game by FM's date. A new profile has only a name (setup, Game profiles → New); its first save game read sets its club. A save of another club goes to that club's profile, made when there's none (`profile_for`); `same_club` takes partial names ("Tottenham" = "Tottenham Hotspur"). The start screen lists the newest save games with Read buttons. Snapshot time = the .fm file's
  time. The newest save's list of everyone replaces the older one; a save with an older FM date doesn't replace the
  budgets. Namesakes get " (Club)". Men's and women's club saves are read; the list of everyone holds the same football as the managed club (`womens_clubs`). A national team's save stops with `NotSupported` (`NATIONS`), a notice in the app.
- Old exporter imports already on disk (First Team…, search-…) still count as snapshots and lists.

## Scoring

- **Position score** = Σ(attribute × weight) ÷ Σ(weights) over the attributes known, on the 1–20 scale (`weighted`), when
  those carry at least 85% of the position's weight (`MIN_KNOWN_WEIGHT`). Outfield players get every outfield
  position, keepers only GK.
- **Over time** (`carry_forward`, in FM's date order: `by_fm`, `FM_ORDER`; a save game read later can be an older game): an attribute keeps its newest known value; club, age, wage, value and loan come from the
  newest observation that shows them.
- **Best**: the highest score, among the positions FM says he plays when the Position column was exported
  (`fm_positions`), else among all.
- **vs Age** = the best score ÷ the expected score at his age (`age_benchmark`, clamped to the table's ends). Women's football
  (save.toml's `womens`, set by each read) scales the table by the save's median score at 24-30 against the men's (`womens_vs_age`).
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
- **Formations** (`FORMATIONS`, `formations`, `best_xi`): for each of 26 shapes (back four, three, five), the best XI and a second XI (greedy:
  best player-position pair first, each player once, where he plays), their averages and the weakest spot. Complete
  shapes first.
- Who's at the club: the newest full squad export (not a `-u18s`/`-u21s`/`-reserves` one, `PARTIAL_SQUAD`) decides;
  save.toml's `released`, `signed` and `recalled` cover moves since (`squad_moves`).

## Conventions

- Two git repositories: this one (public, on GitHub) and `saves/` (local only, never pushed: the user's data). Commit
  data with `git -C saves …`. Never put personal details or real save names in this repository.
- score.py and import_export.py use only Python's standard library; app.py needs pywebview, build.py PyInstaller.
- In TOML, quote keys with `/` or spaces: `[outfield."DL/DR"]`, `"First Touch" = 1`.
- Shown as: scores on a 1-100 scale `61.5` (inside, everything stays 1-20: `SC = 5`, `s1()` in template.html, charts.html scales its data at load; a typed or filtered score is read back ÷5), vs Age `95%`.
- Test changes on a copy of a save (`ELEVENINDEX_HOME` pointing at a copy of the data folder).
