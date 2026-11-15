<p align="center">
  <img src="docs/logo.png" width="120" alt="FMAnalyst">
</p>

<h1 align="center">FMAnalyst</h1>

<p align="center">
  <b>Squad, player and staff analysis for Football Manager 2027</b><br>
  Every player you look at, scored at every position: plan your lineup, find better signings, hire better staff<br>
  and follow how your players develop.
</p>

<p align="center">
  <a href="https://github.com/angelkirchev/FMAnalyst/releases/latest/download/FMAnalyst.exe"><img alt="Download FMAnalyst.exe" src="https://img.shields.io/badge/Download-FMAnalyst.exe-e0187a?style=for-the-badge&logo=windows&logoColor=white"></a>
</p>

<p align="center">
  <a href="https://github.com/angelkirchev/FMAnalyst/releases/latest"><img alt="Latest release" src="https://img.shields.io/github/v/release/angelkirchev/FMAnalyst?color=2b7a73&label=version"></a>
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-2b7a73">
  <img alt="Free" src="https://img.shields.io/badge/price-free-2b7a73">
  <img alt="Local" src="https://img.shields.io/badge/your%20data-stays%20on%20your%20PC-2b7a73">
</p>

<p align="center">
  Press <b>F8</b> on a list in FM, choose <b>Menu → Import file…</b> in the app, and every page updates.<br>
  No typing, no screenshots. Everything is explained in the app's own <b>Guide</b> (key <b>G</b>).
</p>

---

<p align="center">
  <img src="docs/screenshots/squad.png" alt="Squad › Lineup: your best eleven on a pitch; click a position for everyone who plays it">
  <br><sub><b>Lineup</b>: your best eleven for any formation; click a position for everyone who plays it and who on your lists would beat them</sub>
</p>

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/formations.png" alt="Formations"><br><sub><b>Formations</b>: 20 shapes ranked by how well your squad fits them, each with its numbers</sub></td>
    <td width="50%"><img src="docs/screenshots/profile.png" alt="A player's profile"><br><sub><b>Profiles</b>: what he's good for, strengths, weaknesses, what to train first</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/compare.png" alt="Compare players"><br><sub><b>Compare</b>: two players side by side, at any position</sub></td>
    <td><img src="docs/screenshots/research.png" alt="Research"><br><sub><b>Research</b>: your best at each position against everyone you've scouted, or your squad by age</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/analysis.png" alt="Analysis"><br><sub><b>Analysis</b>: your team position by position, ranked by score or any attribute</sub></td>
    <td><img src="docs/screenshots/development.png" alt="Development"><br><sub><b>Development</b>: how your players changed between two squad exports</sub></td>
  </tr>
  <tr>
    <td><img src="docs/screenshots/filters.png" alt="Player search"><br><sub><b>Player search</b>: the minimums to type into FM's search to beat your players</sub></td>
    <td><img src="docs/screenshots/staff.png" alt="Staff"><br><sub><b>Staff</b>: every staff member scored for every job</sub></td>
  </tr>
  <tr>
    <td colspan="2" align="center"><img src="docs/screenshots/attributes.png" width="50%" alt="Attributes"><br><sub><b>Attributes</b>: one weight per attribute (1–20) for every save, to tune the scoring your way</sub></td>
  </tr>
</table>

<p align="center"><sub>Screens from a demo save: your club and players will differ.</sub></p>

---

## Get started

About 15 minutes, once. Nothing here changes your FM saves.

### 1 · Add the F8 export to FM

FMAnalyst reads the lists that the free **[FM27 Exporter](https://github.com/v1adicke/fm27-exporter)** mod saves when you
press **F8** in FM (it isn't made by Sports Interactive or this project).

1. Quit FM. Open FM's game folder: in Steam, right-click **Football Manager 27** → **Manage → Browse local files**.
2. Download **`FM27Exporter-1.0.0.zip`** from [FM27 Exporter's releases](https://github.com/v1adicke/fm27-exporter/releases),
   extract it, and copy everything in its **`payload`** folder into FM's game folder. That installs BepInEx 6 and the
   exporter in one go.
3. Start FM (the first start takes a few minutes; a Windows note about `winhttp.dll` is fine).

<details>
<summary>Already have BepInEx 6, or F8 does nothing?</summary>

- With BepInEx 6 already installed, download **`FM27Exporter-1.0.0-plugin-only.zip`** instead and copy the exporter
  into **`BepInEx\plugins`**.
- If F8 does nothing: FM's game folder needs a `BepInEx` folder and `winhttp.dll`, and the exporter must be in
  `BepInEx\plugins`. `BepInEx\LogOutput.log` says whether it loaded.

</details>

In FM, **F8** exports the list under the mouse (every row), and **Ctrl + F8** adds all 47 attribute columns to the view.

### 2 · Get the app and set up your save

**[Download FMAnalyst.exe](https://github.com/angelkirchev/FMAnalyst/releases/latest/download/FMAnalyst.exe)**, put it
somewhere permanent (e.g. **Documents**) and double-click it. If Windows says *Windows protected your PC*, click
**More info → Run anyway** (it asks once).

The first time, it asks for the **club you manage** (exactly as FM writes it, e.g. "Man Utd"), the **currency** FM
shows wages in, and **FM27 Exporter's folder** (usually found for you). Click **Start**.

<details>
<summary>Or run it from source, with Python</summary>

Install [Python](https://www.python.org/downloads/) 3.11+ (tick **Add python.exe to PATH**), download this project
(green **Code** button → **Download ZIP**), extract it, and double-click **`FMAnalyst.bat`**. The first time it installs
pywebview, which the app needs.

</details>

### 3 · Add the views to FM

The views give FM's lists the columns the app reads.

1. **[Download the project ZIP](https://github.com/angelkirchev/FMAnalyst/archive/refs/heads/main.zip)** and extract it.
2. Quit FM and copy the files in its **`views`** folder into `Documents\Sports Interactive\Football Manager 27\views`
   (copy the files, not the folder; choose **Skip** if Windows asks about replacing your own).
3. In FM, use **`Squad - Outfield Players View`** on your squad, **`Search - Outfield Players View`** on player search
   and **`Search - Staff View`** on staff search. On each player view, press **Ctrl + F8** once so every attribute shows.

> **Tip:** add FM's **Position** column to your player views. Then a player's best position comes only from the
> positions FM says he plays, and wide players go to the side they play.

### 4 · Import your squad

On your squad screen in FM, point at the list and press **F8**. In the app, open **Menu → Import file…** (bottom right)
and choose the export. Done: your squad, lineup and formations are ready. Import searches, shortlists and staff the
same way whenever you're scouting.

---

<details>
<summary><b>Good to know</b></summary>

- **Your data** is in `Documents\FMAnalyst`. To update the app, replace `FMAnalyst.exe` with the newest one: your
  data stays.
- **Several saves:** **Menu → Saves** switches between them or starts a new one.
- **Players left out of an import** have too many unscouted attributes to score: scout them more and export again.
- **"The export has no attributes":** press **Ctrl + F8** on the list in FM, then **F8** again.
- **The window stays blank:** install Microsoft's **WebView2 Evergreen Runtime** from the
  [WebView2 page](https://developer.microsoft.com/microsoft-edge/webview2/).
- **Something else:** `app.log` in your data folder says what happened.

</details>

<details>
<summary><b>Where the scores come from</b></summary>

A position score is the weighted average of a player's attributes on FM's 1–20 scale. Each attribute has one weight
(1–20), multiplied at each position by FM's own CA cost there. The default weights come from **harvestgreen22**'s FM27
attribute test, measuring how much each attribute wins matches
([sortitoutsi](https://sortitoutsi.net/content/78219/fm27-attribute-test-tactical-understanding-training-system-preset-tactics-ai-reverse-engineering-engine-for-test#comment_910309),
tabled by squirrel_plays on [FM-Arena](https://fm-arena.com/threads/player-attributes.59/)), and the CA costs from
FM Scout's table. Change them on the **Attributes** page: one table for every save.

</details>

---

<p align="center">
  <sub>Imports use <a href="https://github.com/v1adicke/fm27-exporter">FM27 Exporter</a> · MIT licence, see <a href="LICENSE">LICENSE</a> ·
  not affiliated with Sports Interactive or SEGA</sub>
</p>
