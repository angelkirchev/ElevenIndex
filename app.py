"""ElevenIndex: imports FM27 Exporter files and shows the pages.

    python app.py

Open Menu → "Import file…" (bottom right of every page), choose an export, and it's imported straight away: dated with
the date in the exporter's file name (export_date), as your squad if the file name
starts with "First Team"/"Squad", else as a search named after the file. Then score.py runs and the 'last' page shows.
Needs pywebview (pip install pywebview).
"""
import csv
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

import webview

import import_export
import score

VERSION = score.VERSION  # shown in the window title (set in score.py, which the guide uses too)


def window_title(save=None):
    """The window title: ElevenIndex 1.2.0 — Save: my-save (or "set up" on the setup page)."""
    return f"ElevenIndex {VERSION} — " + (f"Save: {save}" if save else "set up")



def exports_dir(config):
    """FM27 Exporter's folder: config.toml's exports_dir, else found under Documents (OneDrive's too)."""
    if config.get("exports_dir"):
        return Path(config["exports_dir"]).expanduser()
    tail = Path("Sports Interactive", "Football Manager 27", "exports")
    for docs in (score.documents(), Path.home() / "OneDrive" / "Documents", Path.home() / "Documents"):
        if (docs / tail).is_dir():
            return docs / tail
    return Path.home() / "Documents" / tail


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def guess_source(path):
    """None (your squad) for squad-like export names, else a search named after the file.

    Each export is its own list: the exporter's time stamp ("Player Search 2026-11-13 10-35-28") goes into the name
    (search-player-search-10-35-28), so a second search on the same day doesn't merge into the first, and the
    'last' page shows just the list imported. Re-importing the same file updates that list."""
    m = re.match(r"(.*?)\s*\d{4}-\d{2}-\d{2}[ _](\d{2}-\d{2}-\d{2})$", path.stem)
    name, stamp = (m.group(1), m.group(2)) if m else (path.stem, "")
    if re.match(r"(first team|squad)\b", name, re.I):
        return None
    return "search-" + "-".join(filter(None, (slug(name) or "file", stamp)))


def export_date(path):
    """The date an export is filed under: the one in FM27 Exporter's file name ("Player Search 2026-11-13 10-35-28"),
    else the file's own date. It's the real-world date, not FM's (an export doesn't say FM's), but it keeps imports in
    the order they were made, which is what tracking players over time needs."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})[ _]\d{2}-\d{2}-\d{2}$", path.stem)
    return m.group(1) if m else time.strftime("%Y-%m-%d", time.localtime(path.stat().st_mtime))


class App:
    def __init__(self):
        self.window = None
        self.config, self.save, self.save_dir = {}, None, None
        if score.CONFIG.exists():  # else: the setup page asks for the club and settings first
            self.config = score.load_config()
            if (score.ROOT / "saves" / self.config.get("active_save", "") / "save.toml").is_file():
                self.save = self.config["active_save"]
                self.save_dir = score.ROOT / "saves" / self.save
        self.exports = exports_dir(self.config)
        self.notice = None  # (text, is_error) to show once the page has reloaded
        self.lock = threading.Lock()  # one import at a time

    # --- importing ------------------------------------------------------------------------------------------------

    def pick_file(self):
        """Menu → Import file…: choose an export and import it straight away."""
        if self.save is None:  # still on the setup page
            return
        folder = self.exports if self.exports.is_dir() else Path.home()
        chosen = self.window.create_file_dialog(webview.FileDialog.OPEN, directory=str(folder),
                                                file_types=("Exports (*.csv;*.html;*.htm)", "All files (*.*)"))
        if chosen:
            path = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
            threading.Thread(target=self.import_now, args=(path,), daemon=True).start()

    def import_now(self, path):
        date = export_date(path)
        source = guess_source(path)
        if source is None and (m := re.search(r"[ _](\d{2})-(\d{2})-\d{2}$", path.stem)):
            date += f"T{m.group(1)}{m.group(2)}"  # a squad export: its own snapshot, even beside another from the same day
        self.show_notice(f"Importing {path.name}…")
        try:
            with self.lock:
                save, written, skipped, kind = import_export.import_file(path, date, source, save=self.save)
                cfg, pages = score.build(save, self.config)
                out = score.write_pages(save, self.config, cfg, pages)
                # the list's name as saved (a staff search is renamed search-staff-…), without date and _gk/_outfield
                listed = re.sub(r"^\d{4}-\d{2}-\d{2}(T\d{4})?_|(_gk|_outfield)?\.csv$", "", written[0][0])
                self.commit(f"{save}: {listed} {date} (export)")
        except (Exception, SystemExit) as e:  # score.py stops with SystemExit on bad data
            return self.show_notice(f"Couldn't import {path.name}: {e}", error=True)
        who = "staff" if kind == "staff" else "players"
        label = f"your {'staff' if kind == 'staff' else 'squad'}" if source is None else listed
        text = f"Imported {sum(n for _, n in written)} {who} from {path.name} as {label}."
        if skipped:
            text += (f" {len(skipped)} skipped, not enough attributes to score: "
                     + ", ".join(skipped[:5]) + (f" and {len(skipped) - 5} more" if len(skipped) > 5 else "") + ".")
        self.notice = (text, False)  # shown by on_load after the reload
        page = "last_staff.html" if kind == "staff" else "last.html"
        self.window.load_url((out / page).resolve().as_uri())

    # --- clearing old lists --------------------------------------------------------------------------------------

    def clearable(self, kind):
        """Old search lists the Clear button would move to the archive: players' (obs) or staff's. Kept: the squad,
        the latest import (last_batch), youth intakes (the grid uses them), and lists holding anyone save.toml names."""
        folder = self.save_dir / ("staff" if kind == "staff" else "obs")
        cfg = score.load_toml(self.save_dir / "save.toml")
        keep = set(cfg.get("last_staff_batch" if kind == "staff" else "last_batch", []))
        names = set(cfg.get("last_staff_names", [])) if kind == "staff" else \
            {n for k in ("last_names", "released", "signed", "recalled") for n in cfg.get(k, [])}
        names |= {cut for cut, full in cfg.get("aliases", {}).items() if full in names}  # files may hold the cut-off name
        found = []
        for p in sorted(folder.glob("*.csv")):
            source = score.obs_parts(p.stem)[1] if folder.name == "obs" else p.stem.split("_", 1)[-1]
            if not source.startswith("search-") or "intake" in source or p.name in keep:
                continue
            with open(p, newline="", encoding="utf-8") as f:
                if names & {r["Name"] for r in csv.DictReader(f)}:
                    continue
            found.append(p)
        return folder, found

    def count_clearable(self, kind):
        return len(self.clearable(kind)[1])

    def clear_lists(self, kind):
        """Move old search lists to <folder>/archive (not deleted), rescore, and reload the page."""
        with self.lock:
            folder, files = self.clearable(kind)
            if not files:
                return self.show_notice("Nothing to clear: only your squad and latest list are left.")
            archive = folder / "archive"
            archive.mkdir(exist_ok=True)
            moved = []
            try:
                for p in files:
                    p.rename(archive / p.name)
                    moved.append(p)
                cfg, pages = score.build(self.save, self.config)
                out = score.write_pages(self.save, self.config, cfg, pages)
            except (Exception, SystemExit) as e:  # put everything back as it was
                for p in moved:
                    (archive / p.name).rename(p)
                return self.show_notice(f"Couldn't clear the lists, so nothing was moved: {e}", error=True)
            self.commit(f"{self.save}: archived {len(moved)} old {kind} lists")
        where = f"saves\\{self.save}\\{folder.name}\\archive"
        self.notice = (f"Cleared {len(moved)} old list{'s' if len(moved) > 1 else ''}. They're not deleted: they're in {where}, "
                       "and moving them back brings them back.", False)
        page = "staff.html" if kind == "staff" else "elsewhere.html"
        self.window.load_url((out / page).resolve().as_uri())

    def commit(self, message):
        """Commit the save data, as the screenshot workflow does. Silently skipped without git."""
        saves = score.ROOT / "saves"
        if not (saves / ".git").exists():
            return
        flags = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        try:
            for args in (["add", "-A"], ["commit", "-m", message]):
                subprocess.run(["git", "-C", str(saves), *args], capture_output=True, stdin=subprocess.DEVNULL, **flags)
        except OSError:
            pass

    # --- saves ----------------------------------------------------------------------------------------------------

    def saves(self):
        """Every save, for the Saves panel: [{name, club, date, current}], newest data first."""
        found = []
        for toml in (score.ROOT / "saves").glob("*/save.toml"):
            try:
                club = score.load_toml(toml).get("managed_club", "")
            except Exception:
                continue
            dates = [p.name[:10] for p in (toml.parent / "obs").glob("*.csv") if re.match(r"\d{4}-\d{2}-\d{2}_", p.name)]
            found.append({"name": toml.parent.name, "club": club, "date": max(dates, default=""),
                          "current": toml.parent.name == self.save})
        return sorted(found, key=lambda s: (not s["current"], s["name"]))

    def switch_save(self, name, notice=None):
        if not (score.ROOT / "saves" / name / "save.toml").is_file():
            raise ValueError(f"There's no save called {name}.")
        with self.lock:
            score.set_active_save(name)
            self.config = score.load_config()
            self.save, self.save_dir = name, score.ROOT / "saves" / name
            self.window.set_title(window_title(name))
            if any((self.save_dir / "obs").glob("*.csv")):
                cfg, pages = score.build(name, self.config)
                out = score.write_pages(name, self.config, cfg, pages)
                self.notice = (notice or f"Switched to {name}.", False)
                self.show_soon(url=(out / "squad.html").resolve().as_uri())
            else:
                self.notice = (notice or f"Switched to {name}: no players yet.", False)
                self.show_soon(html=START_HTML)

    def new_save(self, club, division, name):
        club, division = club.strip(), division.strip()
        if not club:
            raise ValueError("Type the club you manage, exactly as FM shows it in its player lists.")
        name = slug(name) or slug(club)
        if (score.ROOT / "saves" / name).exists():
            raise ValueError(f"There's already a save called {name}: give this one another name, e.g. {name}-2.")
        score.new_save(name, club, division)
        self.commit(f"{name}: new save")
        self.switch_save(name, f"New save {name} for {club}. Now import your squad: press F8 on it in FM, then Menu → Import file…")

    # --- weights (the Attributes page) ----------------------------------------------------------------------------

    def rescore_with(self, change, notice):
        """Change the attribute weights (change() writes saves/attributes.toml), rescore this save and reload the
        Attributes page; every other save is rescored when you switch to it. If scoring fails, the file goes back."""
        own = score.ATTRIBUTES_FILE
        before = own.read_text(encoding="utf-8") if own.exists() else None
        with self.lock:
            try:
                change()
                cfg, pages = score.build(self.save, self.config)
                out = score.write_pages(self.save, self.config, cfg, pages)
            except (Exception, SystemExit):
                if before is None:
                    own.unlink(missing_ok=True)
                else:
                    own.write_text(before, encoding="utf-8")
                raise
            self.commit(f"attribute weights: {notice.lower()}")
        self.notice = (notice + ". This save is rescored; the others are when you open them.", False)
        self.show_soon(url=(out / "attributes.html").resolve().as_uri())

    def current_weights(self):
        return score.load_weights()

    def save_weights(self, values):
        """Save the page's attribute weights (1-20, one table for every position) as your own, for every save
        (saves/attributes.toml); the same as the default = back to the default (the file goes)."""
        clean = score.check_values(values)
        default = score.check_values(score.load_toml(score.weights_file(score.DEFAULT))["attributes"])

        def change():
            if clean == default:
                score.ATTRIBUTES_FILE.unlink(missing_ok=True)
                return
            score.ATTRIBUTES_FILE.write_text(score.weights_toml({"attributes": clean}, (
                f"Your own attribute weights, saved on ElevenIndex's Attributes page ({time.strftime('%Y-%m-%d')}). Every save\n"
                "scores with them. 1-20; not listed = doesn't count. An attribute's weight at a position = its CA cost there\n"
                "x this value (see weights/default.toml).")), encoding="utf-8")
        self.rescore_with(change, "Back to the default attribute weights" if clean == default else "Saved your attribute weights")

    def export_weights(self, attrs=()):
        """Write the attribute weights (your own if you've saved some, else the default; the same for every save) as one
        list to edit in Notepad: "Acceleration 16, Pace 20, ...". attrs: every attribute the page lists, so the ones
        that don't count are named too."""
        values = score.attribute_values(self.current_weights())
        chosen = self.window.create_file_dialog(webview.FileDialog.SAVE, directory=str(Path.home() / "Documents"),
                                                save_filename="ElevenIndex attributes.txt",
                                                file_types=("Text file (*.txt)",))  # pywebview takes only letters and spaces in a name
        if not chosen:
            return None
        path = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
        path = path if path.suffix.lower() == ".txt" else path.with_suffix(".txt")
        which = "your own" if score.ATTRIBUTES_FILE.is_file() else "the default"
        header = (f"ElevenIndex attribute weights ({which}), exported on {time.strftime('%Y-%m-%d')}. One list for every save.\n"
                  "Each attribute and its weight, from 1 to 20, separated by commas: Pace 20, Acceleration 16, ...\n"
                  "At each position it counts by this weight x its CA cost there. Change the numbers, add or remove attributes\n"
                  "(one left out doesn't count), then Import it on the Attributes page.\n"
                  "Decimals: use a dot, like Pace 12.25 (a comma also works, as long as there's no space after it: 12,25).")
        path.write_text(score.attributes_text(values, [str(a) for a in attrs or ()], header), encoding="utf-8")
        return str(path)

    def import_weights(self):
        """Read attribute weights you choose (a list as Export writes: "Pace 20, Acceleration 16, ...", or an earlier
        version's table), for the page to show: they're not saved until Save."""
        chosen = self.window.create_file_dialog(webview.FileDialog.OPEN, directory=str(Path.home() / "Documents"),
                                                file_types=("Attribute weights (*.txt;*.csv)", "All files (*.*)"))
        if not chosen:
            return None
        path = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeDecodeError:
            text = path.read_text(encoding="cp1252")  # saved in Windows' own encoding
        known = {a for t in score.load_costs(score.COSTS_FILE).values() for a in t}
        try:
            values = score.check_values(score.read_attributes_text(text, known))
        except ValueError as e:
            raise ValueError(f"Couldn't read {path.name}: {e}")
        return {"values": values, "file": path.name}

    def about(self):
        """Menu → About: the version, this save's settings and the project page, as the page shows them."""
        tile = score.settings_tile(self.config, score.load_toml(self.save_dir / "save.toml") if self.save_dir else {}, self.save or "–")
        return {"title": f"ElevenIndex {score.VERSION}", "html": tile["text"]}

    def open_exports(self):
        if self.exports.is_dir() and sys.platform == "win32":
            os.startfile(self.exports)

    # --- the window -------------------------------------------------------------------------------------------------

    def show_soon(self, url=None, html=None):
        """Load a page a moment from now: called from the page, the reply has to reach it before it's replaced."""
        load = (lambda: self.window.load_url(url)) if url else (lambda: self.window.load_html(html))
        threading.Timer(0.3, load).start()

    def show_notice(self, text, error=False):
        """A message in the corner of the current page."""
        self.window.evaluate_js(f"window.fmNotice && fmNotice({json.dumps(text)}, {json.dumps(error)})")

    def on_load(self):
        """Every page, after each reload: add the toolbar, and show a pending notice."""
        if self.save is None:  # the setup page
            return
        self.window.evaluate_js(TOOLBAR_JS)
        if self.notice:
            text, error = self.notice
            self.notice = None
            self.window.evaluate_js(f"fmNotice({json.dumps(text)}, {json.dumps(error)})")

    # --- first run: the setup page ------------------------------------------------------------------------------

    def setup_page(self):
        data = {"exports": str(self.exports), "home": str(score.ROOT), "shortcut": bool(getattr(sys, "frozen", False))}
        return SETUP_HTML.replace("__DATA__", json.dumps(data))

    def setup(self, club, division, currency, exports, name, shortcut):
        """The setup page's Start: write config.toml, create the save, and open it."""
        club, division, currency, exports = club.strip(), division.strip(), currency.strip() or "£", exports.strip()
        if not club:
            raise ValueError("Type the club you manage, exactly as FM writes it in its player lists.")
        name = slug(name) or slug(club)
        if not (score.ROOT / "saves" / name / "save.toml").is_file():
            score.new_save(name, club, division)
        score.write_config(name, currency, exports_dir=exports or None)
        if shortcut:
            desktop_shortcut()
        self.config = score.load_config()
        self.exports = exports_dir(self.config)
        self.save, self.save_dir = name, score.ROOT / "saves" / name
        self.commit(f"{name}: new save")
        self.window.set_title(window_title(name))
        self.notice = (f"All set up for {club}. Now import your squad: press F8 on it in FM, then Menu → Import file…", False)
        self.show_soon(html=START_HTML)

    def browse_folder(self, current):
        chosen = self.window.create_file_dialog(webview.FileDialog.FOLDER, directory=current or str(Path.home()))
        return (chosen[0] if isinstance(chosen, (list, tuple)) else chosen) if chosen else None

    def start(self):
        if self.save is None:
            where, title = {"html": self.setup_page()}, window_title()
        else:
            # rescore on every start, so a change of weights (or a newer version of the app) shows straight away
            try:
                cfg, pages = score.build(self.save, self.config)
                score.write_pages(self.save, self.config, cfg, pages)
            except (Exception, SystemExit) as e:
                self.notice = (f"Couldn't update the pages: {e}", True)
                log_error("rescoring on start")
            page = score.ROOT / "out" / self.save / "squad.html"  # the app opens on Squad › Lineup
            where = {"url": page.resolve().as_uri()} if page.exists() else {"html": START_HTML}
            title = window_title(self.save)
        self.window = webview.create_window(title, js_api=MainApi(self), width=1400, height=900, **where)
        self.window.events.loaded += self.on_load
        self.window.events.closed += lambda: os._exit(0)  # closing the window ends the app
        # The XI mark in the title bar and taskbar, from source too (not Python's icon). Its own app ID keeps
        # Windows from grouping the window with other Python programs on the taskbar.
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ElevenIndex")
            except (AttributeError, OSError):
                pass
        icon = score.APP / "docs" / "ElevenIndex.ico"
        webview.start(icon=str(icon) if icon.exists() else None)  # no menu bar: the page's Menu button has it all


def log_error(what):
    """Append the current exception to app.log in the data folder: the packaged app has no console to show it."""
    import traceback
    try:
        score.ROOT.mkdir(parents=True, exist_ok=True)
        with open(score.ROOT / "app.log", "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {what}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def desktop_shortcut():
    """A desktop shortcut to the .exe (the packaged app only). Best effort: nothing happens if Windows refuses."""
    target = Path(sys.executable)
    script = ("$d = [Environment]::GetFolderPath('Desktop'); $s = (New-Object -ComObject WScript.Shell)"
              f".CreateShortcut(\"$d\\ElevenIndex.lnk\"); $s.TargetPath = '{target}'; "
              f"$s.WorkingDirectory = '{target.parent}'; $s.Save()")
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                       stdin=subprocess.DEVNULL, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


class MainApi:
    """Called from the page's Import file… button."""

    def __init__(self, app):
        self._app = app

    def pick_file(self):
        self._app.pick_file()

    def saves(self):
        return self._app.saves()

    def count_clearable(self, kind):
        return self._app.count_clearable(kind)

    def clear_lists(self, kind):
        # in the background: the page reloads before a reply could reach it
        threading.Thread(target=self._app.clear_lists, args=(kind,), daemon=True).start()

    def switch_save(self, name):
        return self._call(self._app.switch_save, name)

    def new_save(self, club, division, name):
        return self._call(self._app.new_save, club, division, name)

    def setup(self, club, division, currency, exports, name, shortcut):
        return self._call(self._app.setup, club, division, currency, exports, name, shortcut)

    def browse_folder(self, current):
        return self._app.browse_folder(current)

    def save_weights(self, values):
        return self._call(self._app.save_weights, values)

    def export_weights(self, attrs=()):
        return self._reply(self._app.export_weights, attrs)

    def about(self):
        return self._reply(self._app.about)

    def open_exports(self):
        return self._call(self._app.open_exports)

    def import_weights(self):
        return self._reply(self._app.import_weights)

    @staticmethod
    def _reply(f, *args):
        """{"result": what f returned} when it worked, {"error": message} when it didn't."""
        try:
            return {"result": f(*args)}
        except (Exception, SystemExit) as e:
            log_error("weights")
            return {"error": str(e)}

    @staticmethod
    def _call(f, *args):
        """{} when it worked, {"error": message} for the Saves panel to show."""
        try:
            f(*args)
            return {}
        except (Exception, SystemExit) as e:
            return {"error": str(e)}


SETUP_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
body { font: 15px Inter, 'Segoe UI', system-ui, sans-serif; background: #121815; color: #f2efe7; margin: 0; display: flex; justify-content: center; }
.box { width: 560px; max-width: 92vw; margin: 48px 0; }
h1 { font-size: 22px; margin: 0 0 6px; } .lead { color: #cfd6d1; margin: 0 0 22px; line-height: 1.5; }
label { display: block; margin: 14px 0 5px; } .hint { color: #9aa49f; font-size: 13px; margin-top: 4px; }
input, select { width: 100%; box-sizing: border-box; padding: 8px; border: 1px solid #2a332f; border-radius: 6px;
  background: #161c19; color: #f2efe7; font: inherit; }
.row { display: flex; gap: 8px; } .row input { flex: 1; }
button { padding: 8px 16px; border: 0; border-radius: 6px; font: 600 14px Inter, 'Segoe UI', system-ui, sans-serif; cursor: pointer;
  background: #2a332f; color: #f2efe7; }
button.go { background: #a9d93b; color: #0f1412; padding: 10px 22px; font-size: 15px; margin-top: 22px; }
.check { display: flex; gap: 8px; align-items: center; margin-top: 16px; } .check input { width: auto; }
.error { color: #f87171; min-height: 1.3em; margin-top: 12px; } .home { color: #9aa49f; font-size: 13px; margin-top: 26px; }
</style></head><body><div class="box">
<h1>Welcome to ElevenIndex</h1>
<p class="lead">A few details about your Football Manager save, and you're ready. You can add more saves later with
<b>Menu → Saves</b> (bottom right).</p>
<label for="club">Club you manage</label>
<input id="club" placeholder="e.g. Man Utd" autofocus>
<div class="hint">Exactly as FM writes it in its player lists: "Man Utd", not "Manchester United".</div>
<label for="currency">Currency FM shows wages in</label>
<select id="currency"><option>£</option><option>€</option><option>$</option></select>
<label for="exports">FM27 Exporter's folder</label>
<div class="row"><input id="exports"><button id="browse">Browse…</button></div>
<div class="hint">Where FM27 Exporter saves the lists you export with F8. It's usually found for you.</div>
<div class="check" id="shortcut-row"><input type="checkbox" id="shortcut" checked><label for="shortcut" style="margin:0">Put a shortcut on the desktop</label></div>
<div class="error" id="error"></div>
<button class="go" id="go">Start</button>
<div class="home" id="home"></div>
</div><script>
const d = __DATA__, $ = id => document.getElementById(id);
$("exports").value = d.exports;
$("home").textContent = "Your saves and pages are kept in " + d.home;
if (!d.shortcut) $("shortcut-row").style.display = "none";
$("browse").onclick = async () => { const f = await pywebview.api.browse_folder($("exports").value); if (f) $("exports").value = f; };
async function go() {
  $("go").disabled = true; $("error").textContent = "Setting up…";
  const r = await pywebview.api.setup($("club").value, "", $("currency").value, $("exports").value, "",
                                      d.shortcut && $("shortcut").checked);
  if (r && r.error) { $("error").textContent = r.error; $("go").disabled = false; }
}
$("go").onclick = go;
document.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
</script></body></html>"""

START_HTML = """<!doctype html><html><body style="font: 15px Inter, 'Segoe UI', system-ui; background: #121815; color: #f2efe7; padding: 40px">
<h2>No players yet</h2><p>In FM, point at a player list and press F8 (Ctrl+F8 first, to add every attribute).</p>
<p>Then open <b>Menu</b> at the bottom right, click <b>Import file…</b> and choose the export.</p></body></html>"""

TOOLBAR_JS = """(() => {
  if (document.getElementById("fm-import")) return;
  const bar = document.createElement("div");
  bar.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:9999;display:flex;gap:6px;align-items:center;" +
    "padding:6px;border-radius:8px;background:#121815;box-shadow:0 2px 8px #0008;font:14px Inter,'Segoe UI',system-ui,sans-serif";
  const BUTTON = "padding:7px 16px;border:0;border-radius:6px;font:600 14px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer;";
  // Menu: Import file…, Open the exports folder, Saves…, About (it opens upwards, above the button)
  const m = document.createElement("button");
  m.id = "fm-import"; m.title = "Import a file, the exports folder, your saves, about";
  m.innerHTML = '<svg width="16" height="16" viewBox="0 0 16 16" style="vertical-align:-3px;margin-right:8px" aria-hidden="true">' +
    '<path d="M2 3.5h12M2 8h12M2 12.5h12" stroke="#0f1412" stroke-width="2" stroke-linecap="round"/></svg>Menu';
  m.style.cssText = BUTTON + "background:#a9d93b;color:#0f1412";
  const list = document.createElement("div");
  list.id = "fm-menu";
  list.style.cssText = "position:absolute;right:6px;bottom:52px;display:none;min-width:240px;padding:6px;border:1px solid #2a332f;" +
    "border-radius:10px;background:#121815;box-shadow:0 8px 24px #000a";
  const item = (text, hint, run) => {
    const it = document.createElement("button");
    it.style.cssText = "display:block;width:100%;padding:8px 12px;border:0;border-radius:6px;background:transparent;color:#f2efe7;" +
      "font:500 14px Inter,'Segoe UI',system-ui,sans-serif;text-align:left;cursor:pointer";
    it.innerHTML = text + (hint ? `<div style="color:#9aa49f;font-size:12px;font-weight:400">${hint}</div>` : "");
    it.onmouseenter = () => it.style.background = "#232c29";
    it.onmouseleave = () => it.style.background = "transparent";
    it.onclick = () => { list.style.display = "none"; run(); };
    return it;
  };
  const line = () => { const l = document.createElement("div"); l.style.cssText = "height:1px;margin:4px 6px;background:#2a332f"; return l; };
  list.append(
    item("Import file…", "an FM27 Exporter file (F8 in FM)", () => window.pywebview && pywebview.api.pick_file()),
    item("Open the exports folder", "where FM27 Exporter saves its files", () => window.pywebview && pywebview.api.open_exports()),
    line(),
    item("Saves…", "switch to another save, or start one", () => window.fmSaves()),
    item("About", "version, your settings, project page", () => window.fmAbout()));
  m.onclick = e => { e.stopPropagation(); list.style.display = list.style.display === "none" ? "block" : "none"; };
  document.addEventListener("click", e => { if (!list.contains(e.target)) list.style.display = "none"; });
  document.addEventListener("keydown", e => { if (e.key === "Escape") list.style.display = "none"; });
  bar.style.position = "fixed";
  bar.append(m, list);

  // Elsewhere and Staff pages: Clear old lists. First click says how many, a second click within 5 s clears.
  const page = location.href.split(/[\\\\/]/).pop().split(/[?#]/)[0];
  const kind = {"elsewhere.html": "players", "staff.html": "staff"}[page];
  if (kind) {
    const c = document.createElement("button");
    c.id = "fm-clear"; c.title = "Move old search lists to the archive (not deleted). Keeps your squad and your latest list.";
    const idle = "Clear old lists";
    c.textContent = idle;
    c.style.cssText = BUTTON + "background:#33292e;color:#f87171";
    let armed = null;
    c.onclick = async () => {
      if (armed) { clearTimeout(armed); armed = null; c.textContent = "Clearing…"; return pywebview.api.clear_lists(kind); }
      const n = await pywebview.api.count_clearable(kind);
      if (!n) { window.fmNotice("Nothing to clear: only your squad and latest list are left.", false); return; }
      c.textContent = `Clear ${n} old list${n > 1 ? "s" : ""}? Click again`;
      armed = setTimeout(() => { armed = null; c.textContent = idle; }, 5000);
    };
    bar.prepend(c);
  }
  document.body.append(bar);

  // The Saves panel: switch to a save, or create one. Typing in it isn't a page shortcut.
  const css = (el, s) => (el.style.cssText = s, el);
  const make = (tag, text) => { const e = document.createElement(tag); if (text) e.textContent = text; return e; };
  const field = "padding:7px;border:1px solid #2a332f;border-radius:6px;background:#161c19;color:#f2efe7;font:inherit;width:100%;box-sizing:border-box";
  window.fmSaves = async () => {
    document.getElementById("fm-saves")?.remove();
    const saves = await pywebview.api.saves();
    const shade = css(make("div"), "position:fixed;inset:0;z-index:10000;background:#000a;display:flex;align-items:center;justify-content:center;font:14px Inter,'Segoe UI',system-ui,sans-serif");
    shade.id = "fm-saves";
    shade.onclick = e => { if (e.target === shade) shade.remove(); };
    shade.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Escape") shade.remove(); });
    const box = css(make("div"), "background:#121815;color:#f2efe7;border:1px solid #2a332f;border-radius:10px;padding:20px 22px;width:440px;max-width:90vw;max-height:85vh;overflow:auto;box-shadow:0 8px 30px #000");
    const error = css(make("div"), "color:#f87171;min-height:1.2em;margin-top:8px");
    const run = async (call) => { error.textContent = "Working…"; const r = await call(); if (r && r.error) error.textContent = r.error; else shade.remove(); };

    box.append(css(make("h3", "Your saves"), "margin:0 0 10px;font-size:16px"));
    for (const s of saves) {
      const row = css(make("button"), "display:flex;justify-content:space-between;gap:12px;width:100%;margin:4px 0;padding:9px 12px;border-radius:6px;cursor:pointer;font:inherit;text-align:left;" +
        "border:1px solid " + (s.current ? "#c5f04f" : "#2a332f") + ";background:" + (s.current ? "#161c19" : "transparent") + ";color:#f2efe7");
      row.append(make("span", s.club + "  (" + s.name + ")"), css(make("span", s.current ? "open now" : (s.date ? "up to " + s.date : "no players yet")), "color:#9aa49f;white-space:nowrap"));
      if (!s.current) row.onclick = () => run(() => pywebview.api.switch_save(s.name));
      box.append(row);
    }

    box.append(css(make("h3", "New save"), "margin:20px 0 4px;font-size:16px"),
      css(make("div", "Your old saves stay as they are: switch back to them here any time."), "color:#9aa49f;margin-bottom:8px"));
    const input = (labelText, placeholder) => {
      const l = css(make("label", labelText), "display:block;margin:8px 0 4px");
      const i = css(make("input"), field); i.placeholder = placeholder; box.append(l, i); return i;
    };
    const club = input("Club you manage, exactly as FM writes it in player lists", "e.g. Man Utd");
    const name = input("Name for this save (optional)", "made from the club's name");
    club.oninput = () => { name.placeholder = club.value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || "made from the club's name"; };
    const buttons = css(make("div"), "display:flex;gap:8px;justify-content:flex-end;margin-top:14px");
    const cancel = css(make("button", "Close"), "padding:7px 16px;border:0;border-radius:6px;background:#2a332f;color:#f2efe7;font:inherit;cursor:pointer");
    const create = css(make("button", "Create and switch to it"), "padding:7px 16px;border:0;border-radius:6px;background:#a9d93b;color:#0f1412;font:600 14px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer");
    cancel.onclick = () => shade.remove();
    create.onclick = () => run(() => pywebview.api.new_save(club.value, "", name.value));
    buttons.append(cancel, create);
    box.append(error, buttons);
    shade.append(box);
    document.body.append(shade);
  };
  window.fmAbout = async () => {
    document.getElementById("fm-about")?.remove();
    const r = await pywebview.api.about();
    if (!r || r.error) { window.fmNotice((r && r.error) || "Couldn't show About.", true); return; }
    const shade = css(make("div"), "position:fixed;inset:0;z-index:10000;background:#000a;display:flex;align-items:center;justify-content:center;font:14px Inter,'Segoe UI',system-ui,sans-serif");
    shade.id = "fm-about";
    shade.onclick = e => { if (e.target === shade) shade.remove(); };
    shade.tabIndex = -1;
    shade.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Escape") shade.remove(); });
    const box = css(make("div"), "background:#121815;color:#f2efe7;border:1px solid #2a332f;border-radius:10px;padding:20px 22px;width:620px;max-width:92vw;max-height:85vh;overflow:auto;box-shadow:0 8px 30px #000;line-height:1.55");
    box.append(css(make("h3", r.result.title), "margin:0 0 10px;font-size:16px"));
    const body = make("div");
    body.innerHTML = r.result.html;
    body.querySelectorAll("table").forEach(t => t.style.cssText = "width:100%;table-layout:fixed;border-collapse:collapse;margin-bottom:10px");
    body.querySelectorAll("td").forEach((td, i) => td.style.cssText = "padding:5px 8px;border-bottom:1px solid #232c29;vertical-align:top;white-space:normal;overflow-wrap:anywhere;text-align:left" +
      (i % 2 ? "" : ";width:150px"));  // long folder paths wrap instead of scrolling
    body.querySelectorAll("td.text").forEach(td => td.style.color = "#9aa49f");
    body.querySelectorAll("a").forEach(a => a.style.color = "#c5f04f");
    const close = css(make("button", "Close"), "display:block;margin:14px 0 0 auto;padding:7px 16px;border:0;border-radius:6px;background:#2a332f;color:#f2efe7;font:inherit;cursor:pointer");
    close.onclick = () => shade.remove();
    box.append(body, close);
    shade.append(box);
    document.body.append(shade);
    shade.focus();
  };
  window.fmNotice = (text, error) => {
    let n = document.getElementById("fm-notice");
    if (!n) { n = document.createElement("div"); n.id = "fm-notice"; document.body.append(n); }
    n.textContent = text;
    n.style.cssText = "position:fixed;right:16px;bottom:68px;z-index:9999;max-width:440px;padding:10px 14px;" +
      "border-radius:6px;font:14px Inter,'Segoe UI',system-ui,sans-serif;box-shadow:0 2px 8px #0008;color:#121815;" +
      "background:" + (error ? "#f87171" : "#4ade80");
    clearTimeout(window.fmNoticeTimer);
    window.fmNoticeTimer = setTimeout(() => n.remove(), error ? 15000 : 6000);
  };
})()"""


if __name__ == "__main__":
    App().start()
