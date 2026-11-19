<p align="center">
  <img src="docs/elevenindex-banner.png" width="380" alt="XI INDEX: ElevenIndex">
</p>

<p align="center">
  <b>ElevenIndex</b> · Squad &amp; staff scoring for Football Manager 27<br>
  Reads your FM27 save game and scores every player at every position and every staff member for every job:<br>
  plan your lineup, find better signings, hire better staff, train and develop your players.
</p>

<p align="center">
  <a href="https://elevenindex.pages.dev"><img alt="Download ElevenIndex" src="https://img.shields.io/badge/Download-ElevenIndex-c5f04f?style=for-the-badge&logo=windows&logoColor=0f1412&labelColor=0f1412"></a>
</p>

<p align="center">
  <img alt="Windows 10/11" src="https://img.shields.io/badge/Windows-10%20%7C%2011-1b6b3a">
  <img alt="Free" src="https://img.shields.io/badge/price-free-1b6b3a">
  <img alt="Local" src="https://img.shields.io/badge/your%20data-stays%20on%20your%20PC-1b6b3a">
  <img alt="No mods" src="https://img.shields.io/badge/mods-none-1b6b3a">
</p>

<p align="center">
  Save your game in FM, then <b>Menu → Read save game…</b> in the app (or drop the <b>.fm</b> file on its window).<br>
  No mods, no exports, no typing. FM's save is only ever read, never changed.
</p>

---

<p align="center">
  <img src="docs/screenshots/squad.png" alt="Squad › Lineup">
  <br><sub><b>Lineup</b>: your best eleven for any formation; click a position for everyone who plays it and who in the game would beat them</sub>
</p>

<table>
  <tr>
    <td width="50%" valign="top"><img src="docs/screenshots/profile.png" alt="A player's profile"><br><sub><b>Profiles</b>: personal details, contract, attributes like FM's, a scout report, CA/PA, personality and hidden attributes</sub></td>
    <td width="50%" valign="top"><img src="docs/screenshots/filter.png" alt="Filter"><br><sub><b>Players database</b>: every player in the game, with a Filter for anything: position, contract, ability, attributes, personality</sub></td>
  </tr>
  <tr>
    <td valign="top"><img src="docs/screenshots/club.png" alt="Club overview"><br><sub><b>Club</b>: budgets, squad, best eleven, injuries, contracts ending, youngsters; plus Contracts, Medical and Transfers</sub></td>
    <td valign="top"><img src="docs/screenshots/formations.png" alt="Formations"><br><sub><b>Formations</b>: 26 shapes ranked by how well your squad fits them, with a scout's view of the good and the bad</sub></td>
  </tr>
  <tr>
    <td valign="top"><img src="docs/screenshots/depth.png" alt="Depth"><br><sub><b>Depth</b>: starter, backup and third choice at every position, and who in the game would do better</sub></td>
    <td valign="top"><img src="docs/screenshots/training.png" alt="Training"><br><sub><b>Training</b>: each player's next individual training routine in FM, from his weak points where he plays</sub></td>
  </tr>
  <tr>
    <td valign="top"><img src="docs/screenshots/development.png" alt="Development"><br><sub><b>Development</b>: how your players changed between checkpoints (31 Dec, 30 Jun, now)</sub></td>
    <td valign="top"><img src="docs/screenshots/toplists.png" alt="Top lists"><br><sub><b>Top lists</b>: the best, the most promising and the bargains in the game, by position</sub></td>
  </tr>
  <tr>
    <td colspan="2" valign="top"><img src="docs/screenshots/staff.png" alt="Your staff"><br><sub><b>Staff</b>: your staff, one person per job, against every staff member in the game, with a Staff plan</sub></td>
  </tr>
</table>

<p align="center"><sub>Screens from a test save.</sub></p>

---

## Get started

1. Download **ElevenIndex.exe** from **[elevenindex.pages.dev](https://elevenindex.pages.dev)**, put it somewhere permanent
   (e.g. **Documents**) and double-click it. If Windows says *Windows protected your PC*, click **More info → Run anyway**.
2. The first time it asks for the **club you manage** and the **currency** FM shows wages in, then **Start**.
3. In FM, **save your game**. In the app, **Menu → Read save game…** (or drop the `.fm` file on the window). About a minute
   later every page is up to date. **Auto-read** (on by default) reads each new save by itself.

Everything else is explained in the app's **Guide** (key **G**).

<details>
<summary>Run it from source, with Python</summary>

Python 3.14+ (it reads the save with the standard library's `compression.zstd`). Double-click **`ElevenIndex.bat`**:
the first time it installs pywebview, which the window needs. `python build.py` builds the .exe (PyInstaller).

</details>

<details>
<summary><b>Good to know</b></summary>

- **Your data** is in `Documents\ElevenIndex`. To update, replace `ElevenIndex.exe` with the newest one: your data stays.
- **Another club:** **Menu → Game profiles… → New** (one game profile per club you manage).
- **The window stays blank:** install Microsoft's **WebView2 Evergreen Runtime**
  ([WebView2 page](https://developer.microsoft.com/microsoft-edge/webview2/)).
- **Something else:** `app.log` in your data folder says what happened.

</details>

<details>
<summary><b>Where the scores come from</b></summary>

A position score is the weighted average of a player's attributes on FM's 1–20 scale. Each attribute has one weight
(1–20), multiplied at each position by FM's own CA cost there. The default weights come from **harvestgreen22**'s FM27
attribute test, measuring how much each attribute wins matches
([sortitoutsi](https://sortitoutsi.net/content/78219/fm27-attribute-test-tactical-understanding-training-system-preset-tactics-ai-reverse-engineering-engine-for-test#comment_910309),
tabled by squirrel_plays on [FM-Arena](https://fm-arena.com/threads/player-attributes.59/)). Change them on the
**Attributes** page. The save game reader (`fm_save.py`) is our own, written from the file itself.

</details>

---

<p align="center">
  <sub>© 2026 Angel Kirchev, all rights reserved: free to download and use, not to copy, modify or republish (see <a href="LICENSE">LICENSE</a>) ·
  not affiliated with Sports Interactive or SEGA</sub>
</p>
