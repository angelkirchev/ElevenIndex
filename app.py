"""ElevenIndex: reads your FM27 save games and shows the pages.

    python app.py

Open Menu → "Read save game…" (under the logo on every page), choose an FM27 save (.fm), and it's read straight away:
your club (players, staff, finances), every other player and staff member in the game. Then score.py runs and the
Lineup shows. Needs pywebview (pip install pywebview) and Python 3.14+ (zstd).
"""
import glob
import html
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import unquote, urlparse

import webview

import import_export
import score

VERSION = score.VERSION  # shown in the window title (set in score.py, which the guide uses too)


def window_title(save=None):
    """The window title: ElevenIndex 1.2.0 — Save: my-save (or "set up" on the setup page)."""
    return f"ElevenIndex {VERSION} — " + (f"Save: {save}" if save else "set up")



START_PAGE = "club.html"  # the page the app opens on, after start and after reading a save: the first tab


FM_TAIL = Path("Sports Interactive", "Football Manager 27", "games")


def fm_saves_in(folder):
    """The FM save games (.fm) in a folder, newest first (FM's own backup copy left out); [] when there's none."""
    try:
        found = [f for f in Path(folder).iterdir() if f.is_file() and f.suffix.lower() == ".fm"
                 and not f.stem.lower().startswith("last save overwrite backup")]
    except OSError:
        return []
    return sorted(found, key=lambda f: f.stat().st_mtime, reverse=True)


def steam_folders():
    """Steam Cloud's copies of FM's saves: Steam\\userdata\\<account>\\<game>\\remote(\\games), where FM writes
    a save game when it's saved to the cloud."""
    roots = []
    if sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
        roots += [Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam", Path(r"C:\Steam")]
    out = []
    for root in dict.fromkeys(roots):
        for remote in (root / "userdata").glob("*/*/remote") if (root / "userdata").is_dir() else []:
            out += [remote / "games", remote]
    return out


def fm_folders(config=None):
    """Where FM27's save games can be: config.toml's games_dir, Documents (wherever Windows has put it, OneDrive's
    too), the Public Documents, and Steam Cloud's folders."""
    docs = [score.documents(), Path.home() / "OneDrive" / "Documents", Path.home() / "Documents",
            Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Documents"]
    picked = [Path(config["games_dir"]).expanduser()] if (config or {}).get("games_dir") else []
    return list(dict.fromkeys(picked + [d / FM_TAIL for d in docs] + steam_folders()))


def games_dir(config):
    """FM27's save games folder: config.toml's games_dir when it holds save games; else the folder found with the
    newest save game (Documents, OneDrive, Public Documents, Steam Cloud); else the chosen one, or FM's usual one."""
    folders = fm_folders(config)
    if config.get("games_dir") and fm_saves_in(folders[0]):
        return folders[0]
    with_saves = [(fm_saves_in(f)[0].stat().st_mtime, f) for f in folders if fm_saves_in(f)]
    if with_saves:
        return max(with_saves)[1]
    if config.get("games_dir"):
        return Path(config["games_dir"]).expanduser()
    return next((f for f in folders if f.is_dir()), score.documents() / FM_TAIL)


_SUMMARIES = {}


def save_summary(path):
    """{path, file, club, manager, date} from a save game's summary (a fraction of a second; cached), or None."""
    path = Path(path)
    try:
        key = (str(path), path.stat().st_mtime)
        if key not in _SUMMARIES:
            import fm_save
            data, sections = fm_save.open_save(path)
            info = fm_save.summary(data, sections)
            _SUMMARIES[key] = {"path": str(path), "file": path.stem, "club": info["club"], "manager": info["manager"],
                               "date": info["date"].strftime("%d %b %Y")}
        return _SUMMARIES[key]
    except Exception:  # not an FM27 save, or one we can't read: left out
        return None


def detect_clubs(folder, newest=8):
    """The clubs managed in a folder's newest save games, one entry a club (its newest save), newest first."""
    out, seen = [], set()
    for f in fm_saves_in(folder)[:newest]:
        info = save_summary(f)
        if info and info["club"] not in seen:
            seen.add(info["club"])
            out.append(info)
    return out


def folder_report(folder):
    """The setup page's line under the folder: how many save games it holds, or where else they were found."""
    folder = Path(folder.strip()).expanduser() if folder and folder.strip() else None
    found = fm_saves_in(folder) if folder else []
    if found:
        return {"ok": True, "text": f"Found {len(found)} save game{'s' if len(found) != 1 else ''} here, the newest "
                                    f"{found[0].stem} ({time.strftime('%d %b %H:%M', time.localtime(found[0].stat().st_mtime))})."}
    elsewhere = [f for f in fm_folders() if fm_saves_in(f) and f != folder]
    if elsewhere:
        return {"ok": False, "text": f"No save games in this folder, but there are some in {elsewhere[0]}.", "suggest": str(elsewhere[0])}
    return {"ok": False, "text": "No save games found yet. Save your game in FM first (with cloud saving on, FM puts it in "
                                 "Steam's folder: Steam › userdata › … › remote), then Browse… to the folder holding the .fm file."}


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


class App:
    def __init__(self):
        self.window = None
        self.config, self.save, self.save_dir = {}, None, None
        if score.CONFIG.exists():  # else: the setup page asks for the club and settings first
            self.config = score.load_config()
            if (score.ROOT / "saves" / self.config.get("active_save", "") / "save.toml").is_file():
                self.save = self.config["active_save"]
                self.save_dir = score.ROOT / "saves" / self.save
        self.games = games_dir(self.config)
        self.notice = None  # (text, is_error) to show once the page has reloaded
        self.lock = threading.Lock()  # one import at a time
        try:
            self.purge_deleted()  # saves deleted more than 14 days ago
        except OSError:
            pass

    # --- importing ------------------------------------------------------------------------------------------------

    def pick_file(self):
        """Menu → Read save game…: choose an FM27 save (.fm) and read it straight away."""
        if self.save is None:  # still on the setup page
            return
        folder = self.games if self.games.is_dir() else Path.home()
        chosen = self.window.create_file_dialog(webview.FileDialog.OPEN, directory=str(folder),
                                                file_types=("FM save games (*.fm)", "All files (*.*)"))
        if chosen:
            path = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
            threading.Thread(target=self.import_now, args=(path,), daemon=True).start()

    pick_save_game = pick_file

    def profile_for(self, path):
        """The game profile a save game belongs to, by the club its manager runs: this one when it's that club's (or
        still empty), else that club's profile, made when there's none. Switches to it; returns a word for the notice."""
        info = save_summary(path)
        if not info:
            return ""
        club = info["club"]
        here = score.ROOT / "saves" / (self.save or "")
        empty = not any((here / "obs").glob("*.csv"))
        if self.save and (empty or import_export.same_club(self.club_name() or "", club)):
            return ""
        match = next((s["name"] for s in self.saves() if import_export.same_club(s["club"] or "", club)), None)
        made = match is None
        if made:
            name, n = slug(club) or "save", 2
            while (score.ROOT / "saves" / name).exists():
                name, n = f"{slug(club)}-{n}", n + 1
            score.new_save(name, club, "")
            match = name
        score.set_active_save(match)
        self.config = score.load_config()
        self.save, self.save_dir = match, score.ROOT / "saves" / match
        self.window.set_title(window_title(match))
        return f" It's a {club} save, so it went to {'a new' if made else 'your'} {club} profile."

    def import_now(self, path):
        """Read an FM27 save game: your club, everyone else, the club's money (import_export.import_save)."""
        self.show_notice(f"Reading {path.name}… Be patient: your save will be loaded soon.")
        moved = ""
        try:
            with self.lock:
                moved = self.profile_for(path)
                save, written, skipped, _ = import_export.import_save(path, save=self.save)
                cfg, pages = score.build(save, self.config)
                out = score.write_pages(save, self.config, cfg, pages)
                self.commit(f"{save}: {path.stem} (save game)")
        except (Exception, SystemExit) as e:  # score.py stops with SystemExit on bad data
            log_error(f"reading the save game {path.name}")
            return self.show_notice(f"Couldn't read {path.name}: {e}", error=True)
        mine = sum(n for name, n in written if "search-save-game" not in name and "-staff" not in name)
        staff = sum(n for name, n in written if name.endswith("-staff.csv") and "search-save-game" not in name)
        rest = sum(n for name, n in written if "search-save-game" in name and "-staff" not in name)
        rest_staff = sum(n for name, n in written if "search-save-game-staff" in name)
        text = (f"Read {path.name}: your {mine} players and {staff} staff"
                + (f", and {rest:,} other players and {rest_staff:,} other staff in the game." if rest else ".") + moved)
        self.notice = (text, False)
        self.mark_imported(path)
        url = (out / START_PAGE).resolve().as_uri()
        try:
            here = (self.window.get_current_url() or "").split("?")[0].split("#")[0]
        except Exception:
            here = ""
        if unquote(here).lower() == unquote(url).lower():  # already on it: read the new file in place
            self.window.evaluate_js("location.reload()")
        else:
            self.open_url(url)

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

    def switch_save(self, name, notice=None, title="Save loaded"):
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
                self.notice = (notice or f"Now on {self.club_name() or name} ({name}).", False, title)
                self.show_soon(url=(out / START_PAGE).resolve().as_uri())
            else:
                self.notice = (notice or f"Now on {self.club_name() or name} ({name}): no players yet.", False, title)
                self.show_soon(html=screen("start", self.club_name(), saves=self.save_files(), folder=self.games))

    # A deleted save waits in saves/_deleted, where Saves… can restore it, for KEEP_DELETED_DAYS; then it's gone.
    KEEP_DELETED_DAYS = 14
    DELETED_MARK = "deleted.txt"  # in a deleted save's folder: when it was deleted (seconds since 1970)

    @staticmethod
    def _plain_name(name):
        return bool(name) and not any(c in name for c in "/\\:") and name not in (".", "..")

    def delete_save(self, name):
        """Take a save off the list: its folder moves to saves/_deleted (Saves… can restore it for 14 days), its
        pages go (they're rebuilt from the data). The open save too: then the app moves to another save, or, with
        none left, to the setup page."""
        folder = score.ROOT / "saves" / name
        if not self._plain_name(name) or not (folder / "save.toml").is_file():
            raise ValueError(f"There's no save called {name}.")
        was_open = name == self.save
        with self.lock:
            bin_ = score.ROOT / "saves" / "_deleted"
            bin_.mkdir(exist_ok=True)
            target, n = bin_ / name, 2
            while target.exists():
                target, n = bin_ / f"{name}-{n}", n + 1
            folder.rename(target)
            (target / self.DELETED_MARK).write_text(str(int(time.time())), encoding="utf-8")
            shutil.rmtree(score.ROOT / "out" / name, ignore_errors=True)
            self.commit(f"{name}: deleted (moved to _deleted)")
        if not was_open:
            return
        others = [s["name"] for s in self.saves()]
        note = f"{name} is deleted. You can restore it from Menu → Manage… for 14 days."
        if others:  # the one with the newest data
            newest = max(others, key=lambda n: max((f.name[:10] for f in (score.ROOT / "saves" / n / "obs").glob("*.csv")), default=""))
            self.switch_save(newest, notice=f"{note} Now on {newest}.", title="Save deleted")
        else:  # none left: the setup page, as on the first start
            self.save, self.save_dir = None, None
            self.window.set_title(window_title())
            self.show_soon(html=self.setup_page())

    def deleted_saves(self):
        """Saves… 's Deleted list: [{folder, name, club, deleted, gone}], newest first. Clears out the old ones first."""
        self.purge_deleted()
        found = []
        for toml in (score.ROOT / "saves" / "_deleted").glob("*/save.toml"):
            when = self._deleted_at(toml.parent)
            try:
                club = score.load_toml(toml).get("managed_club", "")
            except Exception:
                club = ""
            found.append({"folder": toml.parent.name, "name": toml.parent.name, "club": club, "when": when,
                          "deleted": time.strftime("%Y-%m-%d", time.localtime(when)),
                          "gone": time.strftime("%Y-%m-%d", time.localtime(when + self.KEEP_DELETED_DAYS * 86400))})
        return sorted(found, key=lambda s: -s["when"])

    def _deleted_at(self, folder):
        try:
            return int((folder / self.DELETED_MARK).read_text(encoding="utf-8").strip())
        except (OSError, ValueError):  # no mark (moved there by hand): from when the folder was last changed
            return int(folder.stat().st_mtime)

    def purge_deleted(self):
        """Delete for good the saves deleted more than KEEP_DELETED_DAYS ago. Runs at start and when Saves… opens."""
        bin_ = score.ROOT / "saves" / "_deleted"
        if not bin_.is_dir():
            return
        cutoff = time.time() - self.KEEP_DELETED_DAYS * 86400
        gone = [f for f in bin_.iterdir() if f.is_dir() and self._deleted_at(f) < cutoff]
        for f in gone:
            shutil.rmtree(f, ignore_errors=True)
        if gone:
            self.commit(f"deleted for good after {self.KEEP_DELETED_DAYS} days: {', '.join(f.name for f in gone)}")

    def restore_save(self, folder):
        """Put a deleted save back in the list, under its own name (name-2… when that's taken meanwhile)."""
        src = score.ROOT / "saves" / "_deleted" / folder
        if not self._plain_name(folder) or not (src / "save.toml").is_file():
            raise ValueError(f"There's no deleted save called {folder}.")
        with self.lock:
            target, n = score.ROOT / "saves" / folder, 2
            while target.exists():
                target, n = score.ROOT / "saves" / f"{folder}-{n}", n + 1
            (src / self.DELETED_MARK).unlink(missing_ok=True)
            src.rename(target)
            self.commit(f"{target.name}: restored")
        return target.name

    def remove_deleted(self, folder):
        """Saves… → Deleted saves → Delete now (clicked twice): remove a deleted save for good, without the 14 days."""
        src = score.ROOT / "saves" / "_deleted" / folder
        if not self._plain_name(folder) or not (src / "save.toml").is_file():
            raise ValueError(f"There's no deleted save called {folder}.")
        def writable(func, path, _):  # Windows: a read-only file can't be removed until it's writable
            os.chmod(path, 0o666)
            func(path)
        with self.lock:
            shutil.rmtree(src, onerror=writable)
            self.commit(f"{folder}: deleted for good (Delete now)")

    # --- the menu's quick actions: recent saves, a watcher for new ones, drag and drop, backups, the update check ---

    IMPORTABLE = (".fm",)

    @staticmethod
    def save_key(path):
        """A save game as read: its name and the time it was saved (FM writes the same file again on each save)."""
        f = Path(path)
        return f"{f.stem}@{int(f.stat().st_mtime)}" if f.exists() else f.stem

    def imported_list(self):
        f = self.save_dir / "imported.txt" if self.save_dir else None
        return f, (set(f.read_text(encoding="utf-8").splitlines()) - {""} if f and f.exists() else set())

    def mark_imported(self, path):
        """Note a save game as read into this save (its name and when FM saved it)."""
        f, done = self.imported_list()
        if f:
            f.write_text("".join(f"{k}\n" for k in sorted(done | {self.save_key(path)})), encoding="utf-8")

    def save_files(self):
        """FM's save games, newest first (FM's own backup copy left out)."""
        return fm_saves_in(self.games)

    def menu_data(self):
        """What the menu shows: this save, the others, the newest save games, a newer version."""
        _, done = self.imported_list()
        recent = [{"name": f.stem, "path": str(f), "imported": self.save_key(f) in done,
                   "when": time.strftime("%d %b %H:%M", time.localtime(f.stat().st_mtime))} for f in self.save_files()[:5]]
        return {"current": self.club_name() or self.save, "save": self.save, "saves": self.saves(), "recent": recent,
                "update": getattr(self, "update", None), "auto": self.auto_import(),
                "folder": str(self.games), "count": len(self.save_files())}

    def import_path(self, path):
        """Read a save game picked in the menu, offered by the watcher or dropped on the window."""
        f = Path(path)
        if f.suffix.lower() not in self.IMPORTABLE or not f.is_file():
            raise ValueError(f"Not an FM27 save game: {f.name}")
        threading.Thread(target=self.import_now, args=(f,), daemon=True).start()

    def auto_import(self):
        return bool(self.config.get("auto_import", True))  # on unless turned off

    def set_auto_import(self, on):
        """Menu → Auto-read: on, every time FM saves your game it's read here by itself. config.toml keeps it."""
        score.set_setting("auto_import", bool(on))
        self.config["auto_import"] = bool(on)

    def watch_exports(self):
        """Every few seconds: when FM saves a game (a new or newer .fm), it's read straight away with Auto-read on,
        else it gets a notice with a Read button. Waits until FM has finished writing the file."""
        seen = {f: f.stat().st_mtime for f in self.save_files()}
        while True:
            time.sleep(5)
            try:
                if self.save is None or self.window is None:
                    continue
                for f in self.save_files():
                    mtime = f.stat().st_mtime
                    if seen.get(f) == mtime:
                        continue
                    size = f.stat().st_size
                    time.sleep(3)  # FM writes the save in parts: wait until it stops growing
                    if f.stat().st_size != size or f.stat().st_mtime != mtime:
                        continue
                    seen[f] = mtime
                    if self.auto_import():
                        self.import_now(f)  # one after another: each read waits for the one before
                    else:
                        self.window.evaluate_js(f"window.fmNewExport && fmNewExport({json.dumps(f.stem)}, {json.dumps(str(f))})")
            except Exception:
                log_error("watching the save games folder")

    def on_drop(self, e):
        """A save game dropped on the window: read it (the page shows "Drop to read" while dragging)."""
        try:
            files = (e.get("dataTransfer") or {}).get("files") or []
            path = next((f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")), None)
        except Exception:
            path = None
        if not path or Path(path).suffix.lower() not in self.IMPORTABLE:
            return self.show_notice("Drop an FM27 save game (.fm) to read it.", True)
        last, when = getattr(self, "last_drop", (None, 0))
        if last == path and time.time() - when < 3:  # the same drop, reported twice
            return
        self.last_drop = (path, time.time())
        threading.Thread(target=self.import_now, args=(Path(path),), daemon=True).start()

    def latest_release(self):
        """The latest version, from the website's version.json: (version, page). Only that small file is fetched;
        nothing is sent."""
        req = urllib.request.Request(score.PROJECT_URL + "/version.json", headers={"User-Agent": "ElevenIndex"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        return str(data.get("version", "")).lstrip("v"), data.get("url") or score.PROJECT_URL + "/#download"

    @staticmethod
    def newer(tag):
        ver = lambda v: tuple(int(x) for x in re.findall(r"\d+", v)[:3])
        return bool(tag) and ver(tag) > ver(VERSION)

    def check_update(self):
        """Once at start: a newer release gets a notice (offline or no answer: nothing)."""
        time.sleep(4)
        try:
            tag, url = self.latest_release()
        except Exception:
            return
        if self.newer(tag):
            self.update = {"version": tag, "url": url}
            if self.window is not None and self.save is not None:
                self.update_shown = True
                self.window.evaluate_js(f"window.fmUpdate && fmUpdate({json.dumps(self.update)}, true)")

    def check_now(self):
        """Menu → Check for updates: {current, latest, newer, url}."""
        try:
            tag, url = self.latest_release()
        except Exception:
            raise ValueError("Couldn't reach GitHub to check. Are you online? Try again later.")
        if self.newer(tag):
            self.update = {"version": tag, "url": url}
        return {"current": VERSION, "latest": tag, "newer": self.newer(tag), "url": url}

    def backup_save(self):
        """Menu → Back up this save…: the save's folder as one zip (to keep, or to move to another computer)."""
        if not self.save_dir:
            raise ValueError("No save open.")
        name = f"ElevenIndex {self.save} {time.strftime('%Y-%m-%d')}.zip"
        chosen = self.window.create_file_dialog(webview.FileDialog.SAVE, directory=str(score.documents()),
                                                save_filename=name, file_types=("Zip files (*.zip)",))
        if not chosen:
            return None
        dest = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
        if dest.suffix.lower() != ".zip":
            dest = dest.with_suffix(".zip")
        with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
            for f in self.save_dir.rglob("*"):
                if f.is_file() and ".git" not in f.parts:
                    z.write(f, (Path(self.save) / f.relative_to(self.save_dir)).as_posix())
        return str(dest)

    def restore_backup(self):
        """Saves… → Restore a backup…: a zip from Back up this save… becomes a save again (name-2… when taken)."""
        chosen = self.window.create_file_dialog(webview.FileDialog.OPEN, directory=str(score.documents()),
                                                file_types=("Zip files (*.zip)",))
        if not chosen:
            return None
        src = Path(chosen[0] if isinstance(chosen, (list, tuple)) else chosen)
        with zipfile.ZipFile(src) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            tops = {n.split("/")[0] for n in names}
            top = next(iter(tops)) if len(tops) == 1 else ""
            if not top or f"{top}/save.toml" not in names or any(".." in n.split("/") or ":" in n or n.startswith("/") for n in names):
                raise ValueError(f"{src.name} isn't an ElevenIndex save backup.")
            base = slug(top) or "restored"
            target, k = score.ROOT / "saves" / base, 2
            while target.exists():
                target, k = score.ROOT / "saves" / f"{base}-{k}", k + 1
            with self.lock:
                for n in names:
                    out = target / Path(*n.split("/")[1:])
                    out.parent.mkdir(parents=True, exist_ok=True)
                    out.write_bytes(z.read(n))
                self.commit(f"{target.name}: restored from {src.name}")
        return target.name

    # --- theme (Menu → Theme…) -------------------------------------------------------------------------------------

    def themes(self):
        return {"current": score.theme_of(self.config),
                "themes": [{"name": n, "label": l, "bar": b, "accent": a} for n, l, b, a in score.THEMES]}

    def set_theme(self, name):
        """Use a colour theme from now on: config.toml remembers it (for every save), this save's pages change now,
        another save's when you switch to it (its pages are rebuilt then)."""
        if name not in {t[0] for t in score.THEMES}:
            raise ValueError(f"There's no theme called {name}.")
        with self.lock:
            score.set_setting("theme", name)
            self.config["theme"] = name
            if self.save:
                score.stamp_theme(self.save, name)

    def new_save(self, club, division, name):
        club, division = club.strip(), division.strip()
        name = slug(name) or slug(club)  # the club comes from the first save game read into it
        if not name:
            raise ValueError("Give the new game profile a name, e.g. Man Utd.")
        if (score.ROOT / "saves" / name).exists():
            raise ValueError(f"There's already a save called {name}: give this one another name, e.g. {name}-2.")
        score.new_save(name, club, division)
        self.commit(f"{name}: new save")
        self.switch_save(name, f"{name} is ready. Now read your game: pick a save game here; the club comes from it.",
                         title="Profile created")

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
        if self.games.is_dir() and sys.platform == "win32":
            os.startfile(self.games)

    def change_games_dir(self):
        """Menu → Change FM's saves folder…: pick the folder FM saves its games in; kept in config.toml."""
        chosen = self.browse_folder(str(self.games))
        if not chosen:
            return
        score.set_setting("games_dir", str(chosen))
        self.config["games_dir"] = str(chosen)
        self.games = games_dir(self.config)
        report = folder_report(str(chosen))
        if self.save and not (score.ROOT / "out" / self.save / START_PAGE).exists():  # still on the start screen: list them
            self.window.load_html(screen("start", self.club_name(), saves=self.save_files(), folder=self.games))
        self.window.evaluate_js(f"fmNotice({json.dumps(report['text'])}, {json.dumps(not report['ok'])}, "
                                f"{json.dumps({'title': 'Saves folder changed'})})")

    # --- the window -------------------------------------------------------------------------------------------------

    def show_soon(self, url=None, html=None):
        """Load a page a moment from now: called from the page, the reply has to reach it before it's replaced."""
        load = (lambda: self.open_url(url)) if url else (lambda: self.window.load_html(html))
        threading.Timer(0.3, load).start()

    def open_url(self, url):
        """Open one of our pages; if its file is missing, the error screen instead of the browser's "File not found"."""
        path = Path(unquote(urlparse(url).path).lstrip("/")) if url.startswith("file:") else None
        if path is not None and not path.exists():
            log_error(f"missing page {path}")
            self.window.load_html(screen("error", detail=f"Missing page: {path}"))
        else:
            self.window.load_url(url)

    def club_name(self):
        try:
            return score.load_toml(self.save_dir / "save.toml").get("managed_club", "") if self.save_dir else ""
        except Exception:
            return ""

    def show_notice(self, text, error=False):
        """A message in the corner of the current page."""
        self.window.evaluate_js(f"window.fmNotice && fmNotice({json.dumps(text)}, {json.dumps(error)})")

    def on_closing(self):
        """The window's X: stop the close and ask on the page (CLOSE_JS); its Close button calls quit(). When the page
        can't show the question (broken, still loading, or not answering within 2 s), the app just closes."""
        if getattr(self, "quitting", False):
            return True
        answer = []
        def ask():
            try:
                answer.append(self.window.evaluate_js(CLOSE_JS) == "shown")
            except Exception:
                answer.append(False)
        def wait():
            asker = threading.Thread(target=ask, daemon=True)
            asker.start()
            asker.join(2)  # a page that doesn't answer (a blank or stuck window) mustn't keep the app open
            if not (answer and answer[0]):
                self.quit()
        threading.Thread(target=wait, daemon=True).start()
        return False

    def quit(self):
        """Close at once: hide the window, let an import that's still writing finish (every write holds self.lock), then
        end the process. Waiting for WebView2 to tear the window down (window.destroy) took 2-3 s for nothing; its
        browser processes end by themselves when the app does."""
        self.quitting = True
        def hide():
            try:
                self.window.hide()
            except Exception:
                pass
        hider = threading.Thread(target=hide, daemon=True)
        hider.start()
        def end():
            hider.join(1)  # gone from the screen first: ending the process stops the window's thread, and Windows then
            # takes ~2 s to unload WebView2 with the window still showing
            self.lock.acquire(timeout=15)  # an import mid-write finishes first (15 s at most)
            os._exit(0)
        threading.Thread(target=end, daemon=True).start()

    def rescore_on_start(self, behind=False):
        """Score the save and write its pages. behind: the window is already showing last time's pages, so reload the
        page you're on when they changed (the same data gives the same pages: nothing reloads then)."""
        with self.lock:  # an import waits for it
            out = score.ROOT / "out" / self.save
            before = {p.name: p.read_bytes() for p in out.glob("*.html")} if behind else {}
            try:
                cfg, pages = score.build(self.save, self.config)
                score.write_pages(self.save, self.config, cfg, pages)
            except (Exception, SystemExit) as e:
                log_error("rescoring on start")
                if not behind:
                    self.notice = (f"Couldn't update the pages: {e}", True)
                    return
                self.wait_loaded()
                self.show_notice(f"Couldn't update the pages: {e}", True)
                return
            changed = behind and before != {p.name: p.read_bytes() for p in out.glob("*.html")}
        if changed:
            self.wait_loaded()
            self.window.evaluate_js("location.reload()")

    def wait_loaded(self, timeout=15):
        """Until the window's first page has loaded (on_load), at most timeout seconds."""
        end = time.time() + timeout
        while not getattr(self, "page_loaded", False) and time.time() < end:
            time.sleep(0.05)

    def watch_load(self):
        """A start that never shows its page (a rare WebView2 hang: a blank, black window): after 10 s reload it once,
        and note it in app.log."""
        time.sleep(10)
        if getattr(self, "page_loaded", False) or getattr(self, "quitting", False):
            return
        log_error("the page hadn't loaded 10 s after start: reloading it")
        try:
            page = score.ROOT / "out" / self.save / START_PAGE if self.save else None
            if page is not None and page.exists():
                self.window.load_url(page.resolve().as_uri())
            else:
                self.window.load_html(self.setup_page() if self.save is None else screen("start", self.club_name(), saves=self.save_files(), folder=self.games))
        except Exception:
            log_error("reloading the page")

    def on_load(self):
        """Every page, after each reload: add the toolbar, and show a pending notice."""
        self.page_loaded = True
        if self.save is None:  # the setup page
            return
        self.window.evaluate_js(TOOLBAR_JS)
        try:  # drag and drop: the dropped file's full path comes with the event
            from webview.dom import DOMEventHandler
            self.window.dom.document.events.dragover += DOMEventHandler(lambda e: None, True, True)
            self.window.dom.document.events.drop += DOMEventHandler(self.on_drop, True, True)
        except Exception:
            log_error("drag and drop")
        if getattr(self, "update", None):  # a newer release: in the menu, and as a notice once
            show = not getattr(self, "update_shown", False)
            self.update_shown = True
            self.window.evaluate_js(f"window.fmUpdate && fmUpdate({json.dumps(self.update)}, {json.dumps(show and not self.notice)})")
        if self.notice:
            text, error, *title = self.notice  # an optional title: "Save loaded", "Save created"…
            self.notice = None
            extra = f", {json.dumps({'title': title[0]})}" if title else ""
            self.window.evaluate_js(f"fmNotice({json.dumps(text)}, {json.dumps(error)}{extra})")

    # --- first run: the setup page ------------------------------------------------------------------------------

    def setup_page(self):
        data = {"exports": str(self.games), "home": str(score.ROOT), "shortcut": bool(getattr(sys, "frozen", False))}
        body = SETUP_HTML.replace("__DATA__", json.dumps(data)).replace("__PROJECT__", score.PROJECT_URL)
        return screen("setup", body=body)

    def setup(self, club, division, currency, exports, name, shortcut, read=""):
        """The setup page's Start: write config.toml, create the save, and open it; with read (a save game picked
        there), read it straight away."""
        club, division, currency, exports = club.strip(), division.strip(), currency.strip() or "£", exports.strip()
        name = slug(name) or slug(club)  # the club comes from the first save game read (import_save)
        if not name:
            raise ValueError("Give this game profile a name, e.g. Man Utd.")
        if not (score.ROOT / "saves" / name / "save.toml").is_file():
            score.new_save(name, club, division)
        score.write_config(name, currency, games_dir=exports or None)
        if shortcut:
            desktop_shortcut()
        self.config = score.load_config()
        self.games = games_dir(self.config)
        self.save, self.save_dir = name, score.ROOT / "saves" / name
        self.commit(f"{name}: new save")
        self.window.set_title(window_title(name))
        if read and Path(read).is_file():
            self.show_soon(html=screen("start", self.club_name(), saves=self.save_files(), folder=self.games))
            threading.Timer(0.8, lambda: self.import_now(Path(read))).start()
            return
        self.notice = (f"All set up. Now read your game: pick a save game here (or save it in FM first): the club comes from it.", False, "Profile created")
        self.show_soon(html=screen("start", self.club_name(), saves=self.save_files(), folder=self.games))

    def browse_folder(self, current):
        start = current if current and Path(current).is_dir() else str(Path.home())
        chosen = self.window.create_file_dialog(webview.FileDialog.FOLDER, directory=start)
        return (chosen[0] if isinstance(chosen, (list, tuple)) else chosen) if chosen else None

    def start(self):
        if self.save is None:
            where, title = {"html": self.setup_page()}, window_title()
        else:
            # rescore on every start, so a change of weights (or a newer version of the app) shows straight away. With
            # last time's pages there, the window opens on them at once and the rescoring runs behind it (a big save
            # takes seconds); the page reloads if anything changed. The first time, the pages are made before it opens.
            page = score.ROOT / "out" / self.save / START_PAGE  # the app opens on its first tab (Club › Overview)
            behind = page.exists()
            if not behind:
                self.rescore_on_start()
            where = {"url": page.resolve().as_uri()} if page.exists() else {"html": screen("start", self.club_name(), saves=self.save_files(), folder=self.games)}
            title = window_title(self.save)
        self.window = webview.create_window(title, js_api=MainApi(self), width=1400, height=900, **where)
        self.window.events.loaded += self.on_load
        self.window.events.closing += self.on_closing  # closing asks first, in the app's own style
        threading.Thread(target=self.watch_exports, daemon=True).start()
        threading.Thread(target=self.check_update, daemon=True).start()
        threading.Thread(target=self.watch_load, daemon=True).start()
        if self.save is not None and behind:
            threading.Thread(target=self.rescore_on_start, args=(True,), daemon=True).start()
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
    """Called from the page's Read save game… button."""

    def __init__(self, app):
        self._app = app

    def pick_file(self):
        self._app.pick_file()

    def pick_save_game(self):
        self._app.pick_save_game()

    def saves(self):
        return self._app.saves()


    def switch_save(self, name):
        return self._call(self._app.switch_save, name)

    def new_save(self, club, division, name):
        return self._call(self._app.new_save, club, division, name)

    def delete_save(self, name):
        return self._call(self._app.delete_save, name)

    def themes(self):
        return self._app.themes()

    def quit(self):
        self._app.quit()

    def set_auto_import(self, on):
        return self._call(self._app.set_auto_import, on)

    def set_theme(self, name):
        return self._call(self._app.set_theme, name)

    def deleted_saves(self):
        try:
            return self._app.deleted_saves()
        except OSError:
            return []

    def restore_save(self, folder):
        try:
            return {"result": self._app.restore_save(folder)}
        except (Exception, SystemExit) as e:
            return {"error": str(e)}

    def remove_deleted(self, folder):
        return self._call(self._app.remove_deleted, folder)

    def menu_data(self):
        try:
            return self._app.menu_data()
        except Exception:
            log_error("menu")
            return {"current": "", "save": "", "saves": [], "recent": [], "update": None}

    def import_path(self, path):
        return self._call(self._app.import_path, path)

    def check_now(self):
        try:
            return {"result": self._app.check_now()}
        except (Exception, SystemExit) as e:
            return {"error": str(e)}

    def backup_save(self):
        try:
            return {"result": self._app.backup_save()}
        except (Exception, SystemExit) as e:
            log_error("backup")
            return {"error": str(e)}

    def restore_backup(self):
        try:
            return {"result": self._app.restore_backup()}
        except (Exception, SystemExit) as e:
            log_error("restore")
            return {"error": str(e)}

    def setup(self, club, division, currency, exports, name, shortcut, read=""):
        return self._call(self._app.setup, club, division, currency, exports, name, shortcut, read)

    def detect_clubs(self, folder):
        return detect_clubs(folder)

    def browse_folder(self, current):
        return self._app.browse_folder(current)

    def check_folder(self, folder):
        return folder_report(folder)

    def change_games_dir(self):
        return self._call(self._app.change_games_dir)

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


# The first-run setup page: the same pitch, logo and banner as screen(), a welcome title, the form on the right and
# what the app needs from FM in the bottom-left card.
# The question when the window's X is clicked (App.on_closing): in the app's style, over the page. Enter closes, Esc stays.
CLOSE_JS = r"""(() => {
  if (document.getElementById("fm-close")) return "shown";
  const shade = document.createElement("div");
  shade.id = "fm-close";
  shade.style.cssText = "position:fixed;inset:0;z-index:100000;display:flex;align-items:center;justify-content:center;background:#0009;backdrop-filter:blur(2px)";
  shade.innerHTML = `<div style="width:400px;max-width:90vw;padding:22px 24px 20px;background:#121815;color:#f2efe7;border:1px solid #2a332f;
      border-radius:12px;box-shadow:0 14px 40px #000c;font:14px/1.5 Inter,'Segoe UI',system-ui,sans-serif">
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:10px">
      <span style="flex:none;display:flex;align-items:center;justify-content:center;width:40px;height:40px;border-radius:9px;background:#0f1412;border:1px solid #2a332f;
        font:700 22px/1 'Bahnschrift SemiCondensed','Barlow Condensed','Arial Narrow',sans-serif"><span style="color:#f2efe7">X</span><span style="color:#c5f04f">I</span></span>
      <div style="font:600 22px/1.1 'Bahnschrift SemiCondensed','Barlow Condensed','Arial Narrow',sans-serif;letter-spacing:.02em">Close ElevenIndex?</div>
    </div>
    <div style="color:#cfd6d1">Your saves and imports are kept: everything is here when you open it again.</div>
    <div style="display:flex;justify-content:flex-end;gap:8px;margin-top:18px">
      <button data-a="stay" style="padding:8px 18px;border:1px solid #2f3a35;border-radius:7px;background:transparent;color:#f2efe7;font:600 14px Inter,'Segoe UI',sans-serif;cursor:pointer">Stay</button>
      <button data-a="close" style="padding:8px 20px;border:0;border-radius:7px;background:#c5f04f;color:#0f1412;font:700 14px Inter,'Segoe UI',sans-serif;cursor:pointer">Close</button>
    </div></div>`;
  const stay = () => { shade.remove(); document.removeEventListener("keydown", key, true); };
  const close = () => { window.pywebview && pywebview.api.quit(); };
  const key = e => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); stay(); }
                     else if (e.key === "Enter") { e.preventDefault(); e.stopPropagation(); close(); } };
  shade.addEventListener("click", e => { if (e.target === shade) stay(); });
  shade.querySelector('[data-a="stay"]').onclick = stay;
  shade.querySelector('[data-a="close"]').onclick = close;
  document.addEventListener("keydown", key, true);
  document.body.append(shade);
  shade.querySelector('[data-a="close"]').focus();
  return "shown";
})()"""

SETUP_HTML = """<style>
.setup { position: absolute; right: 40px; top: 50%; transform: translateY(-42%); width: 440px; max-width: calc(100% - 80px);
  padding: 20px 22px 18px; background: #121815f0; border: 1px solid #2a332f; border-radius: 12px; box-shadow: 0 10px 30px #0009; }
.setup h2 { margin: 0 0 4px; font: 600 18px/1 'Bahnschrift SemiCondensed', 'Barlow Condensed', 'Arial Narrow', sans-serif;
  letter-spacing: .04em; text-transform: uppercase; color: #c5f04f; }
.setup label { display: block; margin: 12px 0 5px; color: #e6e9e7; font-size: 14px; font-weight: 600; }
.setup .hint { color: #9aa49f; font-size: 12.5px; margin-top: 4px; line-height: 1.45; }
.setup input, .setup select { width: 100%; padding: 8px 10px; border: 1px solid #2f3a35; border-radius: 6px;
  background: #1a221e; color: #f2efe7; font: inherit; }
.setup input:focus, .setup select:focus { outline: none; border-color: #c5f04f; }
.setup .row { display: flex; gap: 8px; } .setup .row input { flex: 1; min-width: 0; }
.setup button { padding: 8px 14px; border: 0; border-radius: 6px; font: 600 14px Inter, 'Segoe UI', system-ui, sans-serif;
  cursor: pointer; background: #2a332f; color: #f2efe7; }
.setup button.go { width: 100%; margin-top: 18px; padding: 11px; background: #c5f04f; color: #0f1412; font-size: 15px; }
.setup button.go:disabled { opacity: .6; cursor: default; }
.setup .check { display: flex; gap: 8px; align-items: center; margin-top: 14px; font-size: 14px; }
.setup .check input { width: auto; accent-color: #c5f04f; } .setup .check label { margin: 0; font-weight: 400; }
.setup .error { color: #f87171; min-height: 1.2em; margin-top: 8px; font-size: 13px; }
.setup .home { color: #7d8883; font-size: 12px; overflow-wrap: anywhere; }
.card a { color: #8bd3f7; text-decoration: none; } .card a:hover { text-decoration: underline; }
@media (max-width: 1080px), (max-height: 740px) {  /* a small window: one column that scrolls */
  body { overflow: auto; padding: 150px 0 30px; }
  .title, .card, .setup { position: static; transform: none; margin: 0 40px 20px; max-width: 560px; width: auto; }
}
</style>
<div class="title"><span class="tag">Welcome</span>
<h1>Welcome to ElevenIndex</h1><p>Every player scored at every position, every staff member for every job, and your best
eleven: read straight from your Football Manager 27 save games. All on your computer.</p></div>
<div class="setup"><h2>Your save</h2>
<div class="hint">A few details and you're ready. More clubs later: Menu → <b>Manage…</b></div>
<label for="pname">Profile name: your club's name</label>
<input id="pname" placeholder="e.g. Man Utd" autofocus>
<div class="hint">One profile a club you manage. The club itself is read from your save game, so a typo does no harm.</div>
<label for="currency">Currency FM shows wages in</label>
<select id="currency"><option>£</option><option>€</option><option>$</option></select>
<label for="exports">FM27's save games folder</label>
<div class="row"><input id="exports"><button id="browse">Browse…</button></div>
<div class="hint" id="found">Where FM keeps your saves (Documents › Sports Interactive › Football Manager 27 › games, or Steam's
folder with cloud saves). It's usually found for you. A folder picker shows folders only, not the save games in them.</div>
<div class="check" id="shortcut-row"><input type="checkbox" id="shortcut" checked><label for="shortcut">Put a shortcut on the desktop</label></div>
<div class="error" id="error"></div>
<button class="go" id="go">Start</button>
<div class="home" id="home"></div>
</div>
<div class="card"><h2>What it needs from FM</h2><ol>
<li>Fill in <b>Your save</b> and press <b>Start</b>.</li>
<li>In FM, <b>save your game</b>.</li>
<li>Here: Menu → <b>Read save game…</b> and pick it (or drop the .fm file on the window): your club, every player
and every staff member in the game come in at once.</li>
<li><b>Auto-read</b> is on: every later save in FM is read by itself (Menu → Auto-read).</li>
</ol><div class="more"><b>Private:</b> nothing leaves your computer; your saves and pages stay in the folder shown under Start.</div></div>
<script>
const d = __DATA__, $ = id => document.getElementById(id);
$("exports").value = d.exports;
$("home").textContent = "Kept in " + d.home;
if (!d.shortcut) $("shortcut-row").style.display = "none";
// under the folder: how many save games it holds, or where they were found instead (a click there uses that folder)
async function check() {
  if (!window.pywebview) return setTimeout(check, 200);
  const r = await pywebview.api.check_folder($("exports").value), el = $("found");
  el.textContent = r.text; el.style.color = r.ok ? "#c5f04f" : "#f0c24f";
  if (r.suggest) { const a = document.createElement("a"); a.href = "#"; a.textContent = " Use that folder";
    a.style.color = "#8bd3f7"; a.onclick = e => { e.preventDefault(); $("exports").value = r.suggest; check(); }; el.append(a); }
}
$("browse").onclick = async () => { const f = await pywebview.api.browse_folder($("exports").value); if (f) { $("exports").value = f; check(); } };
$("exports").onchange = check;
window.addEventListener("pywebviewready", check); check();
async function go() {
  $("go").disabled = true; $("error").textContent = "Setting up…";
  const r = await pywebview.api.setup("", "", $("currency").value, $("exports").value, $("pname").value,
                                      d.shortcut && $("shortcut").checked, "");
  if (r && r.error) { $("error").textContent = r.error; $("go").disabled = false; }
}
$("go").onclick = go;
document.addEventListener("keydown", e => { if (e.key === "Enter") go(); });
</script>"""

# The new-save and error screens (screen()): a dark pitch, the XI INDEX logo, a title, instructions in the bottom-left
# corner (notices show bottom right).
SCREEN_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { position: relative; overflow: hidden; color: #f2efe7; font: 15px/1.55 Inter, 'Segoe UI', system-ui, sans-serif;
  background: radial-gradient(ellipse 70% 80% at 72% 42%, #1d4a30 0%, #142a1d 38%, #0f1412 72%), #0f1412; }
svg.pitch { position: absolute; inset: 0; width: 100%; height: 100%; }
svg.pitch .l { fill: none; stroke: #f2efe7; stroke-opacity: .07; stroke-width: 2; }
svg.pitch .d { fill: #c5f04f; fill-opacity: .16; } svg.pitch .gk { fill: #f2efe7; fill-opacity: .14; }
:root { --body: Inter, 'Segoe UI', system-ui, sans-serif; }
.bar.corner { position: absolute; top: 26px; right: 40px; display: flex; }
.bar.corner .promo { transform: scale(1.25); transform-origin: top right; }
.screen-strip { position: absolute; top: 96px; left: 40px; display: flex; padding: 0 !important; }  /* the Menu button, under the logo */
__PROMO_CSS__
.brand { position: absolute; top: 34px; left: 40px; display: flex; gap: 12px; align-items: center; }
.brand svg { width: 46px; height: 46px; }
.brand b { display: block; font: 600 26px/1 'Bahnschrift SemiCondensed', 'Barlow Condensed', 'Arial Narrow', sans-serif; letter-spacing: .32em; }
.brand small { display: block; margin-top: 4px; color: #c5f04f; font: 700 9px/1 Inter, 'Segoe UI', sans-serif; letter-spacing: .1em; text-transform: uppercase; }
.title { position: absolute; top: 24%; left: 40px; max-width: 640px; }
.title .tag { display: inline-block; margin-bottom: 12px; padding: 3px 10px; border-radius: 999px; font-size: 12px; font-weight: 600;
  letter-spacing: .06em; text-transform: uppercase; color: #0f1412; background: #c5f04f; }
.title.error .tag { color: #fff; background: #d9534f; }
.title h1 { margin: 0 0 10px; font: 600 48px/1.02 'Bahnschrift SemiCondensed', 'Barlow Condensed', 'Arial Narrow', sans-serif; letter-spacing: .01em; }
.title p { margin: 0; color: #cfd6d1; font-size: 17px; }
.title code { display: block; margin-top: 12px; padding: 8px 12px; color: #f6c9a6; background: #00000040; border-left: 3px solid #e9a066;
  border-radius: 0 6px 6px 0; font: 13px/1.5 Consolas, monospace; white-space: pre-wrap; overflow-wrap: anywhere; }
.card { position: absolute; left: 40px; bottom: 36px; width: 460px; max-width: calc(100% - 80px); padding: 18px 20px 16px;
  background: #121815e8; border: 1px solid #2a332f; border-radius: 12px; box-shadow: 0 10px 30px #0009; }
.card h2 { margin: 0 0 12px; font: 600 18px/1 'Bahnschrift SemiCondensed', 'Barlow Condensed', 'Arial Narrow', sans-serif;
  letter-spacing: .04em; text-transform: uppercase; color: #c5f04f; }
.card ol { margin: 0; padding: 0; list-style: none; counter-reset: s; }
.card li { position: relative; margin: 0 0 10px; padding-left: 34px; color: #e6e9e7; font-size: 14px; }
.card li::before { counter-increment: s; content: counter(s); position: absolute; left: 0; top: 1px; width: 22px; height: 22px;
  border-radius: 50%; color: #0f1412; background: #c5f04f; font: 700 12px/22px Inter, 'Segoe UI', sans-serif; text-align: center; }
.card b { color: #fff; } .card .k { padding: 0 5px; color: #0f1412; background: #e6e9e7; border-radius: 3px; font: 700 12px/1.5 Inter, sans-serif; }
.card .more { margin-top: 12px; padding-top: 10px; border-top: 1px solid #2a332f; color: #9aa49f; font-size: 13px; }
.card .more b { color: #cfd6d1; }
</style></head><body>
<svg class="pitch" viewBox="0 0 1600 1000" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
  <rect class="l" x="60" y="60" width="1480" height="880" rx="6"/><path class="l" d="M800 60v880"/>
  <circle class="l" cx="800" cy="500" r="120"/><circle class="l" cx="800" cy="500" r="4"/>
  <path class="l" d="M60 300h220v400H60M60 400h80v200H60M1540 300h-220v400h220M1540 400h-80v200h80"/>
  <path class="l" d="M280 420a110 110 0 0 1 0 160M1320 420a110 110 0 0 0 0 160"/>
  <circle class="gk" cx="1470" cy="500" r="11"/>
  <circle class="d" cx="1290" cy="230" r="11"/><circle class="d" cx="1310" cy="410" r="11"/><circle class="d" cx="1310" cy="590" r="11"/><circle class="d" cx="1290" cy="770" r="11"/>
  <circle class="d" cx="1090" cy="300" r="11"/><circle class="d" cx="1060" cy="500" r="11"/><circle class="d" cx="1090" cy="700" r="11"/>
  <circle class="d" cx="900" cy="250" r="11"/><circle class="d" cx="870" cy="500" r="11"/><circle class="d" cx="900" cy="750" r="11"/>
</svg>
<div class="brand">__LOGO__<span><b>INDEX</b><small>Rate smarter. Build better.</small></span></div>
<div class="bar corner">__PROMO__</div>
<div class="subnav screen-strip"></div>
__BODY__
</body></html>"""


def screen(kind, club="", detail="", body="", saves=None, folder=None):
    """The new-save screen ("start"), the error screen ("error") or the first-run setup ("setup", its body given), as HTML."""
    logo = (score.APP / "logo.svg").read_text(encoding="utf-8") if (score.APP / "logo.svg").exists() else ""
    esc = lambda t: html.escape(str(t))
    if kind == "setup":
        pass
    elif kind == "error":
        body = f"""<div class="title error"><span class="tag">Something went wrong</span>
<h1>This page couldn't open</h1><p>Your saves and imports are safe; only the page that shows them is missing or broken.</p>
{f'<code>{esc(detail)}</code>' if detail else ''}</div>
<div class="card"><h2>What to do</h2><ol>
<li><b>Open the profile again:</b> Menu → <b>Manage…</b>, pick it: every page is rebuilt from your data.</li>
<li><b>Read your save again</b> if it happened after reading one: Menu → <b>Read save game…</b>.</li>
<li><b>Restart ElevenIndex.</b> It rescores everything when it starts.</li>
<li><b>Still wrong?</b> Report it on the project page (Menu → <b>About</b>) and attach <b>app.log</b> from <b>{esc(score.ROOT)}</b>.</li>
</ol><div class="more">Nothing is deleted when this happens: your FM saves are only ever read, and your data stays in <b>{esc(score.ROOT)}</b>.</div></div>"""
    else:
        body = f"""<div class="title"><span class="tag">New save</span>
<h1>{esc(club) + ': n' if club else 'N'}o players yet</h1><p>Read your save game and every player gets a score at every position, your best eleven, and what to do next.</p></div>
<div class="card"><h2>Read your game</h2><ol>
<li>In FM, <b>save your game</b>.</li>
<li>Here: Menu → <b>Read save game…</b> and pick it, or drop the <b>.fm</b> file on this window.</li>
<li>Your club, every player and every staff member in the game come in at once. Be patient: it's loaded soon.</li>
</ol><div class="more"><b>Auto-read</b> is on: every later save in FM is read by itself (turn it off in the Menu).</div></div>"""
        # the save games found, newest first, each one a click away; and where they were looked for
        rows = "".join(
            f'<li class="sg"><button data-r="Reading…" onclick="this.disabled=true;this.textContent=this.dataset.r;window.pywebview&&pywebview.api.import_path({esc(json.dumps(str(f)))})">Read</button>'
            f'<div><b title="{esc(f.name)}">{esc(f.stem)}</b><span>{time.strftime("%d %b %Y %H:%M", time.localtime(f.stat().st_mtime))} · {f.stat().st_size / 1e6:.0f} MB</span></div></li>'
            for f in (saves or [])[:6])
        where = esc(folder) if folder else "FM's games folder"
        body += f"""<div class="card saves"><h2>Your save games</h2>
<div class="more" style="margin:0 0 8px">In <b>{where}</b> <button class="chg" onclick="window.pywebview&&pywebview.api.change_games_dir()">Change…</button></div>
{f'<ul class="sgl">{rows}</ul>' if rows else '<p>None found here yet. Save your game in FM, or press <b>Change…</b> and pick the folder holding your .fm files.</p>'}
</div>"""
        body += """<style>.card.saves{left:auto;right:40px;width:520px;max-height:calc(100% - 200px);overflow:auto}
.sgl{list-style:none;margin:0;padding:0}.card li.sg{display:flex;align-items:center;gap:12px;margin:0;padding:8px 0;border-top:1px solid #2a332f}
.card li.sg::before{content:none}.sg div{flex:1;min-width:0}.sg b{display:block;overflow-wrap:anywhere;line-height:1.3}
.sg span{display:block;margin-top:2px;color:#9aa49f;font-size:12px}
.sg button,.chg{padding:4px 12px;border:0;border-radius:6px;background:#c5f04f;color:#0f1412;font:700 12px Inter,'Segoe UI',sans-serif;cursor:pointer}
.chg{background:transparent;color:#f2efe7;border:1px solid #3a4540;margin-left:6px}</style>"""
    theme = (score.APP / "theme.css").read_text(encoding="utf-8") if (score.APP / "theme.css").exists() else ""
    i, j = theme.find("  /* the banner at the right of the top bar"), theme.find("  .bar #filter {")
    promo_css = theme[i:j] if 0 <= i < j else ""  # the banner's own rules (score.PROMO), as on every page
    return (SCREEN_HTML.replace("__PROMO_CSS__", promo_css).replace("__PROMO__", score.promo_html())
            .replace("__LOGO__", logo).replace("__BODY__", body))

TOOLBAR_JS = """(() => {
  if (document.getElementById("fm-import")) return;
  const bar = document.createElement("div");
  bar.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:9999;display:flex;gap:6px;align-items:center;" +
    "padding:6px;border-radius:8px;background:#121815;box-shadow:0 2px 8px #0008;font:14px Inter,'Segoe UI',system-ui,sans-serif";
  const BUTTON = "padding:7px 16px;border:0;border-radius:6px;font:600 14px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer;";
  // Menu: a button at the left of the strip under the top bar (where the sub-tabs are; pages without sub-tabs get an
  // empty strip), its list opening downwards: this save, the newest save games, Read save game…, FM's saves folder, your
  // saves, Theme…, Back up this save…, About, and a newer version when there is one. Keys: M opens it, I reads a save.
  let strip = document.querySelector(".subnav");
  if (!strip) {
    strip = document.createElement("div"); strip.className = "subnav";
    const top = document.querySelector(".bar");
    if (top) top.after(strip); else document.body.prepend(strip);
  }
  strip.style.paddingLeft = "14px";
  const m = document.createElement("button");
  m.id = "fm-import"; m.title = "Menu (M): read a save game, recent saves, saves, theme, backup, about";
  m.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">' +
    '<path d="M2 3.5h12M2 8h12M2 12.5h12" stroke="#0f1412" stroke-width="2" stroke-linecap="round"/></svg>Menu';
  m.style.cssText = "flex:none;align-self:center;display:flex;gap:7px;align-items:center;margin:4px 16px 4px 0;padding:4px 12px;border:0;" +
    "border-radius:6px;background:var(--accent-strong,#a9d93b);color:#0f1412;font:700 13px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer";
  strip.prepend(m);
  const list = document.createElement("div");
  list.id = "fm-menu";
  list.style.cssText = "position:fixed;display:none;z-index:10000;width:320px;max-height:calc(100vh - 110px);overflow:auto;padding:6px;" +
    "border:2px solid color-mix(in srgb, var(--blue,#c5f04f) 45%, var(--line,#2a332f));border-radius:10px;" +
    "box-shadow:0 18px 44px #000e, 0 0 0 1px #0008";
  // dark and solid; its parts set apart by clear lines and borders (EDGE: the theme's line colour, lighter)
  const menuBg = "var(--pop,#121815)";
  // the logo, large and faint, in the menu's corner
  const mark = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 70"><text x="2" y="62" font-family="Arial Black,Arial" font-weight="900" font-size="72" fill="#f2efe7" fill-opacity=".05">X</text><text x="62" y="62" font-family="Arial Black,Arial" font-weight="900" font-size="72" fill="#c5f04f" fill-opacity=".07">I</text></svg>';
  list.style.background = `${menuBg} url("data:image/svg+xml,${encodeURIComponent(mark)}") no-repeat right -14px bottom -10px / 170px auto`;
  document.body.append(list);
  // main: the one used most (Read save game…), in the accent, lighter than the rest
  // keep: the menu stays open after the click (a switch, like Auto-read)
  const item = (text, hint, run, main, keep) => {
    const it = document.createElement("button");
    const rest = main ? "color-mix(in srgb, var(--blue,#c5f04f) 16%, transparent)" : "transparent";
    const over = main ? "color-mix(in srgb, var(--blue,#c5f04f) 28%, transparent)" : "var(--hover,#232c29)";
    it.style.cssText = "display:block;width:100%;padding:7px 12px;border:0;border-radius:6px;background:" + rest + ";" +
      "color:" + (main ? "var(--blue,#c5f04f)" : "#f2efe7") + ";font:" + (main ? 600 : 500) + " 14px Inter,'Segoe UI',system-ui,sans-serif;text-align:left;cursor:pointer";
    it.innerHTML = text + (hint ? `<div style="color:${main ? "#cfd6d1" : "#9aa49f"};font-size:12px;font-weight:400">${hint}</div>` : "");
    it.onmouseenter = () => it.style.background = over;
    it.onmouseleave = () => it.style.background = rest;
    it.onclick = e => { if (keep) { e.stopPropagation(); run(it); } else { list.style.display = "none"; run(); } };
    return it;
  };
  const EDGE = "color-mix(in srgb, var(--line,#2a332f) 72%, #ffffff)";
  const line = () => { const l = document.createElement("div"); l.style.cssText = "height:2px;margin:8px 4px;border-radius:1px;background:" + EDGE; return l; };
  const label = t => { const l = document.createElement("div"); l.textContent = t;
    l.style.cssText = "padding:6px 12px 3px;color:#9aa49f;font:700 10px/1 Inter,'Segoe UI',sans-serif;letter-spacing:.08em;text-transform:uppercase"; return l; };
  const escH = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  // FM's saves folder: where it is and how many save games it holds, with Open and Change… beside it
  const folderCard = d => {
    const box = document.createElement("div");
    const short = String(d.folder || "").split(/[\\\\/]/).filter(Boolean).slice(-2).join(" › ");
    const btn = (t, run) => { const b = document.createElement("button"); b.textContent = t;
      b.style.cssText = "padding:4px 10px;border:1px solid " + EDGE + ";border-radius:6px;background:transparent;color:#f2efe7;font:600 12px Inter,'Segoe UI',sans-serif;cursor:pointer";
      b.onmouseenter = () => b.style.borderColor = "var(--blue,#c5f04f)"; b.onmouseleave = () => b.style.borderColor = EDGE;
      b.onclick = e => { e.stopPropagation(); list.style.display = "none"; run(); }; return b; };
    box.style.cssText = "margin:4px 2px 2px;padding:8px 10px 8px 12px;border:1px solid " + EDGE + ";border-radius:8px;display:flex;align-items:center;gap:8px";
    box.innerHTML = `<div style="flex:1;min-width:0"><div style="color:#9aa49f;font:700 10px/1 Inter,'Segoe UI',sans-serif;letter-spacing:.08em;text-transform:uppercase">FM's saves folder</div>
      <div title="${escH(d.folder || "")}" style="margin-top:4px;color:#f2efe7;font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escH(short || "not found")}</div>
      <div style="margin-top:2px;font-size:12px;color:${d.count ? "#9aa49f" : "#f0c24f"}">${d.count ? `${d.count} save game${d.count === 1 ? "" : "s"}` : "no save games here: Change…"}</div></div>`;
    box.append(btn("Open", () => window.pywebview && pywebview.api.open_exports()), btn("Change…", () => window.pywebview && pywebview.api.change_games_dir()));
    return box;
  };
  const small = (t, run, tip) => { const b = document.createElement("button"); b.textContent = t; if (tip) b.title = tip;
    b.style.cssText = "flex:none;padding:4px 10px;border:1px solid " + EDGE + ";border-radius:6px;background:transparent;color:#f2efe7;font:600 12px Inter,'Segoe UI',sans-serif;cursor:pointer";
    b.onmouseenter = () => b.style.borderColor = "var(--blue,#c5f04f)"; b.onmouseleave = () => b.style.borderColor = EDGE;
    b.onclick = e => { e.stopPropagation(); list.style.display = "none"; run(); }; return b; };
  const tile = (t, hint, run) => { const b = document.createElement("button");
    b.innerHTML = `${t}<div style="margin-top:2px;color:#9aa49f;font-size:11px;font-weight:400">${hint}</div>`;
    b.style.cssText = "padding:8px 6px;border:1px solid " + EDGE + ";border-radius:8px;background:transparent;color:#f2efe7;font:600 13px Inter,'Segoe UI',sans-serif;cursor:pointer;text-align:center";
    b.onmouseenter = () => b.style.background = "var(--hover,#232c29)"; b.onmouseleave = () => b.style.background = "transparent";
    b.onclick = e => { e.stopPropagation(); list.style.display = "none"; run(); }; return b; };
  // Auto-read: one line with a switch; a click flips it, the menu stays open
  const autoRow = d => {
    const row = document.createElement("button");
    const draw = () => { row.innerHTML = `<div style="flex:1"><div>Auto-read</div><div style="color:#9aa49f;font-size:12px;font-weight:400">${d.auto ? "each save in FM is read by itself" : "off: read each save yourself"}</div></div>` +
      `<span style="flex:none;width:34px;height:18px;border-radius:999px;position:relative;background:${d.auto ? "#c5f04f" : "#2a332f"}">` +
      `<span style="position:absolute;top:2px;${d.auto ? "right:2px" : "left:2px"};width:14px;height:14px;border-radius:50%;background:${d.auto ? "#0f1412" : "#9aa49f"}"></span></span>`; };
    row.style.cssText = "display:flex;align-items:center;gap:10px;width:100%;padding:7px 12px;border:0;border-radius:6px;background:transparent;color:#f2efe7;font:500 14px Inter,'Segoe UI',sans-serif;text-align:left;cursor:pointer";
    row.onmouseenter = () => row.style.background = "var(--hover,#232c29)"; row.onmouseleave = () => row.style.background = "transparent";
    row.onclick = async e => { e.stopPropagation(); const r = await pywebview.api.set_auto_import(!d.auto);
      if (r && r.error) return window.fmNotice(r.error, true); d.auto = !d.auto; draw(); };
    draw(); return row;
  };
  const fill = async () => {
    const d = window.pywebview ? await pywebview.api.menu_data() : { recent: [], saves: [] };
    list.innerHTML = "";
    // 1. the save you're in, with Manage… (Game profiles: switch, start, delete, back up, restore)
    const cur = document.createElement("div");
    cur.style.cssText = "margin:2px 2px 8px;padding:10px 10px 10px 12px;border-radius:8px;border-left:3px solid var(--blue,#c5f04f);display:flex;align-items:center;gap:8px;" +
      "background:color-mix(in srgb, var(--blue,#c5f04f) 10%, transparent)";
    cur.innerHTML = `<div style="flex:1;min-width:0"><div style="color:var(--blue,#c5f04f);font:700 10px/1 Inter,'Segoe UI',sans-serif;letter-spacing:.08em;text-transform:uppercase">Game profile</div>` +
      `<div style="margin-top:4px;color:#f2efe7;font:600 22px/1 'Barlow Condensed','Bahnschrift SemiCondensed','Arial Narrow',sans-serif;letter-spacing:.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis">${escH(d.current || "none yet")}</div>` +
      `<div style="margin-top:3px;color:#9aa49f;font-size:12px">${(d.saves || []).length > 1 ? `${d.saves.length} profiles` : "your only profile"}</div></div>`;
    cur.append(small("Manage…", () => window.fmSaves(), "switch, start, delete, back up or restore a game profile"));
    list.append(cur);
    // 2. your game: read a save, read every save by itself, where FM keeps them
    list.append(line(), label("Your game"),
      item("Read save game…", "pick an FM27 save (.fm), or drop one on the window · key I", () => window.pywebview && pywebview.api.pick_file(), true),
      autoRow(d), folderCard(d));
    // 3. the app: a new version first when there is one, then Theme, Updates, About as three tiles
    if (d.update && d.update.version) list.append(item(`New version ${escH(d.update.version)}`, "download it from the website", () => window.fmOpen(d.update.url), true));
    const tiles = document.createElement("div");
    tiles.style.cssText = "display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin:8px 2px 2px";
    tiles.append(tile("Theme", "colours", () => window.fmTheme()),
      tile("Updates", "check now", async () => {
        window.fmNotice("Checking for a new version…", false);
        const r = await pywebview.api.check_now();
        if (r.error) return window.fmNotice(r.error, true);
        const u = r.result;
        if (u.newer) window.fmNotice(`ElevenIndex ${u.latest} is out (you have ${u.current}).`, false, { title: "New version", label: "Download", run: () => window.fmOpen(u.url) });
        else window.fmNotice(`You have the latest version (${u.current}).`, false, { title: "Up to date", label: "Release notes", run: () => window.fmOpen(u.url) });
      }),
      tile("About", "settings, guide", () => window.fmAbout()));
    const foot = document.createElement("div");
    foot.style.cssText = "padding:8px 4px 2px;color:#6f7a75;font:500 11px Inter,'Segoe UI',sans-serif;text-align:center";
    foot.textContent = "ElevenIndex """ + VERSION + """ · M opens this menu";
    list.append(line(), label("ElevenIndex"), tiles, foot);
  };
  // a link out (the browser, not this window)
  window.fmOpen = url => { const a = document.createElement("a"); a.href = url; a.target = "_blank"; a.rel = "noopener"; document.body.append(a); a.click(); a.remove(); };
  const openMenu = async () => {
    await fill();
    const r = m.getBoundingClientRect();
    list.style.left = Math.max(8, r.left) + "px"; list.style.top = (r.bottom + 6) + "px"; list.style.display = "block";
  };
  m.onclick = e => { e.stopPropagation(); if (list.style.display === "block") list.style.display = "none"; else openMenu(); };
  document.addEventListener("click", e => { if (!list.contains(e.target) && e.target !== m) list.style.display = "none"; });
  document.addEventListener("keydown", e => {
    if (e.key === "Escape") { list.style.display = "none"; return; }
    if (e.ctrlKey || e.metaKey || e.altKey || document.activeElement?.matches?.("input, select, textarea")) return;
    if (document.getElementById("fm-saves") || document.getElementById("fm-theme") || document.getElementById("fm-about")) return;
    if (e.key === "m" || e.key === "M") { e.preventDefault(); e.stopImmediatePropagation(); m.click(); }
    else if (e.key === "i" || e.key === "I") { e.preventDefault(); e.stopImmediatePropagation(); window.pywebview && pywebview.api.pick_file(); }
  }, true);
  window.addEventListener("scroll", () => list.style.display = "none");

  // Drag and drop: "Drop to read" while a save game is dragged over the window; the app reads it (on_drop)
  const dropHint = document.createElement("div");
  dropHint.style.cssText = "position:fixed;inset:12px;z-index:10001;display:none;align-items:center;justify-content:center;pointer-events:none;" +
    "border:3px dashed var(--blue,#c5f04f);border-radius:16px;background:#0f1412cc;color:#f2efe7;font:600 26px 'Barlow Condensed','Bahnschrift SemiCondensed',sans-serif;letter-spacing:.04em";
  dropHint.textContent = "Drop the save game to read it";
  document.body.append(dropHint);
  let depth = 0;
  const isFile = e => [...(e.dataTransfer?.types || [])].includes("Files");
  document.addEventListener("dragenter", e => { if (isFile(e)) { depth++; dropHint.style.display = "flex"; } });
  document.addEventListener("dragleave", e => { if (isFile(e) && --depth <= 0) { depth = 0; dropHint.style.display = "none"; } });
  document.addEventListener("dragover", e => { if (isFile(e)) e.preventDefault(); });
  document.addEventListener("drop", e => { depth = 0; dropHint.style.display = "none"; if (isFile(e)) e.preventDefault(); });

  // a newer save of your game in FM's folder (the app's watcher), and a newer version on GitHub
  window.fmNewExport = (name, path) => window.fmNotice(name, false, { title: "New save game", label: "Read it", run: () => pywebview.api.import_path(path) });
  window.fmUpdate = (u, show) => { if (show) window.fmNotice(`ElevenIndex ${u.version} is out (you have """ + VERSION + """).`, false,
    { title: "New version", label: "Download", run: () => window.fmOpen(u.url) }); };

  if (bar.children.length) document.body.append(bar);

  // The Saves panel: switch to a save, or create one. Typing in it isn't a page shortcut.
  const css = (el, s) => (el.style.cssText = s, el);
  const make = (tag, text) => { const e = document.createElement(tag); if (text) e.textContent = text; return e; };
  const field = "padding:7px;border:1px solid #2a332f;border-radius:6px;background:#161c19;color:#f2efe7;font:inherit;width:100%;box-sizing:border-box";
  // a panel's × (Saves, Theme, About): its top right corner, over the box (which is position:relative)
  window.fmPanelX = shade => {
    const x = css(make("button"), "position:absolute;top:10px;right:10px;display:flex;align-items:center;justify-content:center;width:30px;height:30px;" +
      "padding:0;border:1px solid #2a332f;border-radius:7px;background:transparent;color:#9aa49f;cursor:pointer");
    x.innerHTML = '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M2 2l8 8M10 2l-8 8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';  // a drawn cross: a text × sits off-centre in most fonts
    x.title = "Close (Esc)";
    x.onmouseenter = () => { x.style.background = "var(--blue,#c5f04f)"; x.style.color = "#141414"; };
    x.onmouseleave = () => { x.style.background = "transparent"; x.style.color = "#9aa49f"; };
    x.onclick = () => shade.remove();
    return x;
  };
  window.fmSaves = async () => {
    document.getElementById("fm-saves")?.remove();
    const saves = await pywebview.api.saves();
    const shade = css(make("div"), "position:fixed;inset:0;z-index:10000;background:#000a;display:flex;align-items:flex-start;justify-content:center;padding-top:8vh;font:14px Inter,'Segoe UI',system-ui,sans-serif");
    shade.id = "fm-saves";
    shade.onclick = e => { if (e.target === shade) shade.remove(); };
    shade.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Escape") shade.remove(); });
    const box = css(make("div"), "position:relative;background:#121815;color:#f2efe7;border:1px solid #2a332f;border-radius:10px;padding:20px 22px;width:500px;max-width:92vw;max-height:85vh;overflow:auto;box-shadow:0 8px 30px #000");
    const error = css(make("div"), "color:#f87171;min-height:1.2em;margin-top:8px");
    const run = async (call) => { error.textContent = "Working…"; const r = await call(); if (r && r.error) error.textContent = r.error; else shade.remove(); };

    box.append(css(make("h3", "Game profiles"), "margin:0 0 10px;font-size:16px"));
    box.append(window.fmPanelX(shade));
    for (const s of saves) {
      const line = css(make("div"), "display:flex;gap:6px;align-items:stretch;margin:4px 0");
      const row = css(make("button"), "flex:1;display:flex;justify-content:space-between;gap:12px;min-width:0;padding:9px 12px;border-radius:6px;cursor:pointer;font:inherit;text-align:left;" +
        "border:1px solid " + (s.current ? "#c5f04f" : "#2a332f") + ";background:" + (s.current ? "#161c19" : "transparent") + ";color:#f2efe7");
      row.append(make("span", s.club + "  (" + s.name + ")"), css(make("span", s.current ? "open now" : (s.date ? "up to " + s.date : "no players yet")), "color:#9aa49f;white-space:nowrap"));
      line.append(row);
      if (!s.current) row.onclick = () => run(() => pywebview.api.switch_save(s.name));
      {
        // Delete: the first click asks, a second within 5 s deletes (the folder goes to saves\\_deleted)
        const del = css(make("button", "Delete"), "flex:none;padding:0 12px;border:0;border-radius:6px;background:#33292e;color:#f87171;font:600 13px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer");
        del.title = (s.current ? "This is the open save: the app moves to another one (or to the setup page if it's the last). " : "") + "Remove this save from the list. Its folder moves to saves\\\\_deleted, so it can be brought back.";
        let armed = null;
        del.onclick = async () => {
          if (!armed) {
            del.textContent = "Sure? Click again";
            armed = setTimeout(() => { armed = null; del.textContent = "Delete"; }, 5000);
            return;
          }
          clearTimeout(armed); armed = null;
          const r = await pywebview.api.delete_save(s.name);
          if (r && r.error) { error.textContent = r.error; del.textContent = "Delete"; return; }
          if (s.current) { shade.remove(); return; }  // the app moves on to another save (or the setup page)
          await window.fmSaves();
          window.fmNotice(`${s.club} (${s.name}) is deleted. You can restore it from Menu → Manage… for 14 days.`, false, { title: "Save deleted" });
        };
        line.append(del);
      }
      box.append(line);
    }

    // Deleted saves: restore one, for 14 days after it was deleted
    const deleted = await pywebview.api.deleted_saves();
    if (deleted.length) {
      box.append(css(make("h3", "Deleted saves"), "margin:20px 0 4px;font-size:16px"),
        css(make("div", "Kept for 14 days after they're deleted, then removed for good. Delete now removes one straight away (click it twice)."), "color:#9aa49f;margin-bottom:6px"));
      for (const s of deleted) {
        const line = css(make("div"), "display:flex;gap:8px;align-items:center;margin:6px 0;padding:8px 8px 8px 12px;border:1px dashed #2a332f;border-radius:6px");
        const who = css(make("div"), "flex:1;min-width:0;display:flex;flex-direction:column;gap:2px");
        who.append(css(make("span", (s.club || s.name) + "  (" + s.name + ")"), "color:#cfd6d1;overflow-wrap:anywhere"),
          css(make("span", "deleted " + s.deleted + " · gone " + s.gone), "color:#9aa49f;font-size:12px"));
        line.append(who);
        const restore = css(make("button", "Restore"), "flex:none;padding:6px 12px;border:0;border-radius:6px;background:#2a332f;color:#c5f04f;font:600 13px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer");
        restore.onclick = async () => {
          restore.disabled = true;
          const r = await pywebview.api.restore_save(s.folder);
          if (r && r.error) { error.textContent = r.error; restore.disabled = false; return; }
          await window.fmSaves();
          window.fmNotice(`${s.club || s.name} is back in your saves as ${r.result}.`, false, { title: "Save restored" });
        };
        // Delete now: for good, without waiting the 14 days. The first click asks, a second within 5 s deletes.
        const now = css(make("button", "Delete now"), "flex:none;padding:6px 12px;border:0;border-radius:6px;background:#33292e;color:#f87171;font:600 13px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer");
        now.title = "Remove this save for good now. It can't be restored afterwards.";
        let armed = null;
        now.onclick = async () => {
          if (!armed) {
            now.textContent = "For good? Click again";
            armed = setTimeout(() => { armed = null; now.textContent = "Delete now"; }, 5000);
            return;
          }
          clearTimeout(armed); armed = null; now.disabled = true;
          const r = await pywebview.api.remove_deleted(s.folder);
          if (r && r.error) { error.textContent = r.error; now.disabled = false; now.textContent = "Delete now"; return; }
          await window.fmSaves();
          window.fmNotice(`${s.club || s.name} (${s.name}) is deleted for good.`, false, { title: "Deleted for good" });
        };
        line.append(restore, now);
        box.append(line);
      }
    }

    const restoreBk = css(make("button", "Restore a backup…"), "margin:14px 0 0;padding:7px 14px;border:1px solid #2a332f;border-radius:6px;background:transparent;color:#f2efe7;font:600 13px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer");
    restoreBk.title = "Bring back a save from a zip made with Menu → Back up this save…";
    restoreBk.onclick = async () => {
      const r = await pywebview.api.restore_backup();
      if (r && r.error) { error.textContent = r.error; return; }
      if (r && r.result) { await window.fmSaves(); window.fmNotice(`It's in your saves as ${r.result}: click it to open it.`, false, { title: "Backup restored" }); }
    };
    const backupBk = css(make("button", "Back up this save…"), "margin:14px 8px 0 0;padding:7px 14px;border:1px solid #2a332f;border-radius:6px;background:transparent;color:#f2efe7;font:600 13px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer");
    backupBk.title = "The save you're in as one zip, to keep or move to another computer";
    backupBk.onclick = async () => {
      const r = await pywebview.api.backup_save();
      if (r && r.error) { error.textContent = r.error; return; }
      if (r && r.result) window.fmNotice("Saved to " + r.result, false, { title: "Backup saved" });
    };
    box.append(css(make("h3", "Backup"), "margin:20px 0 0;font-size:16px"), backupBk, restoreBk);
    box.append(css(make("h3", "New game profile"), "margin:20px 0 4px;font-size:16px"),
      css(make("div", "One profile a club you manage. Your other profiles stay as they are. The club itself is read from your save game, so a typo does no harm."), "color:#9aa49f;margin-bottom:8px"));
    const input = (labelText, placeholder) => {
      const l = css(make("label", labelText), "display:block;margin:8px 0 4px");
      const i = css(make("input"), field); i.placeholder = placeholder; box.append(l, i); return i;
    };
    const name = input("Profile name: your club's name", "e.g. Man Utd");
    const buttons = css(make("div"), "display:flex;gap:8px;justify-content:flex-end;margin-top:14px");
    const cancel = css(make("button", "Close"), "padding:7px 16px;border:0;border-radius:6px;background:#2a332f;color:#f2efe7;font:inherit;cursor:pointer");
    const create = css(make("button", "Create and switch to it"), "padding:7px 16px;border:0;border-radius:6px;background:#a9d93b;color:#0f1412;font:600 14px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer");
    cancel.onclick = () => shade.remove();
    create.onclick = () => run(() => pywebview.api.new_save("", "", name.value));
    buttons.append(cancel, create);
    box.append(error, buttons);
    shade.append(box);
    document.body.append(shade);
  };
  // Theme: five colour variations; a click changes the page at once and every page from then on
  window.fmTheme = async () => {
    document.getElementById("fm-theme")?.remove();
    const r = await pywebview.api.themes();
    const shade = css(make("div"), "position:fixed;inset:0;z-index:10000;background:#000a;display:flex;align-items:flex-start;justify-content:center;padding-top:8vh;font:14px Inter,'Segoe UI',system-ui,sans-serif");
    shade.id = "fm-theme";
    shade.tabIndex = -1;
    shade.onclick = e => { if (e.target === shade) shade.remove(); };
    shade.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Escape") shade.remove(); });
    const box = css(make("div"), "position:relative;background:var(--pop,#121815);color:#f2efe7;border:1px solid var(--line,#2a332f);border-radius:10px;padding:20px 22px;width:360px;max-width:90vw;box-shadow:0 8px 30px #000");
    box.append(css(make("h3", "Theme"), "margin:0 0 12px;font-size:16px"));
    box.append(window.fmPanelX(shade));
    const error = css(make("div"), "color:#f87171;min-height:1.2em;margin-top:6px");
    let current = r.current;
    const rows = r.themes.map(t => {
      const b = css(make("button"), "display:flex;align-items:center;gap:12px;width:100%;margin:5px 0;padding:8px 10px;border-radius:8px;cursor:pointer;font:500 14px Inter,'Segoe UI',system-ui,sans-serif;text-align:left;color:#f2efe7;background:transparent");
      const sw = css(make("span"), `flex:none;width:44px;height:26px;border-radius:5px;background:linear-gradient(90deg, ${t.bar} 0 68%, ${t.accent} 68%);box-shadow:inset 0 0 0 1px #ffffff22`);
      const tick = css(make("span"), "margin-left:auto;font-size:12px;color:#9aa49f");
      b.append(sw, make("span", t.label), tick);
      b.mark = () => { const on = t.name === current; b.style.border = "1px solid " + (on ? t.accent : "var(--line,#2a332f)"); tick.textContent = on ? "in use" : ""; };
      b.onclick = async () => {
        document.documentElement.dataset.theme = t.name;
        current = t.name; rows.forEach(x => x.mark());
        const res = await pywebview.api.set_theme(t.name);
        error.textContent = res && res.error ? res.error : "";
        if (!(res && res.error)) window.fmNotice(`${t.label}, for every save.`, false, { title: "Theme changed" });
      };
      return b;
    });
    rows.forEach(b => { b.mark(); box.append(b); });
    const close = css(make("button", "Close"), "display:block;margin:10px 0 0 auto;padding:7px 16px;border:0;border-radius:6px;background:var(--line,#2a332f);color:#f2efe7;font:inherit;cursor:pointer");
    close.onclick = () => shade.remove();
    box.append(error, close);
    shade.append(box);
    document.body.append(shade);
    shade.focus();
  };
  window.fmAbout = async () => {
    document.getElementById("fm-about")?.remove();
    const r = await pywebview.api.about();
    if (!r || r.error) { window.fmNotice((r && r.error) || "Couldn't show About.", true); return; }
    const shade = css(make("div"), "position:fixed;inset:0;z-index:10000;background:#000a;display:flex;align-items:flex-start;justify-content:center;padding-top:8vh;font:14px Inter,'Segoe UI',system-ui,sans-serif");
    shade.id = "fm-about";
    shade.onclick = e => { if (e.target === shade) shade.remove(); };
    shade.tabIndex = -1;
    shade.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Escape") shade.remove(); });
    const box = css(make("div"), "position:relative;background:#121815;color:#f2efe7;border:1px solid #2a332f;border-radius:10px;padding:20px 22px;width:620px;max-width:92vw;max-height:85vh;overflow:auto;box-shadow:0 8px 30px #000;line-height:1.55");
    box.append(css(make("h3", r.result.title), "margin:0 0 10px;font-size:16px"));
    box.append(window.fmPanelX(shade));
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
  // The corner notice (an import done, an error, "Importing…"): a dark card with a coloured edge, an icon, a title,
  // the message, a close button and a bar for how long it stays (paused while the mouse is on it).
  window.fmNotice = (text, error, action) => {
    document.getElementById("fm-notice")?.remove();
    clearInterval(window.fmNoticeTimer);
    const busy = !error && /^(Importing|Reading)|…( |$)/.test(text), done = !error && !busy;
    // lighter tones than the pages' red and lime; drawn icons: a tick in a circle, a warning triangle
    const ICON_OK = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9.5" fill="currentColor" fill-opacity=".14"/><path d="M7.5 12.3l3 3 6-6.3"/></svg>';
    const ICON_ERR = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3.2 2.6 19.6h18.8Z" fill="currentColor" fill-opacity=".14"/><path d="M12 9.5v4.6M12 17.2v.1"/></svg>';
    const ICON_INFO = '<svg viewBox="0 0 24 24" width="26" height="26" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="9.5" fill="currentColor" fill-opacity=".14"/><path d="M12 11v5.5M12 7.6v.1"/></svg>';
    const offer = action && action.label;  // a notice with a button (Import, Download): the info look
    const kind = offer ? { c: "#a9dcf8", t: action.title || "", i: ICON_INFO } : error ? { c: "#ff9b9b", t: "Something went wrong", i: ICON_ERR }
      : busy ? { c: "#a9dcf8", t: "Working on it", i: "" } : { c: "#d6f57e", t: (action && action.title) || (/^Imported/.test(text) ? "Import done" : "Done"), i: ICON_OK };
    const n = document.createElement("div");
    n.id = "fm-notice";
    n.setAttribute("role", error ? "alert" : "status");
    n.style.cssText = "position:fixed;right:16px;bottom:16px;z-index:9999;width:420px;max-width:calc(100vw - 32px);overflow:hidden;" +
      "display:flex;gap:12px;align-items:flex-start;padding:12px 12px 14px 14px;color:#f2efe7;background:#1d2522d9;backdrop-filter:blur(6px);" +
      "border:1px solid #2a332f;border-left:4px solid " + kind.c + ";border-radius:10px;box-shadow:0 10px 30px #000a;" +
      "font:14px/1.45 Inter,'Segoe UI',system-ui,sans-serif;animation:fmIn .18s ease-out";
    const icon = document.createElement("div");
    icon.style.cssText = "flex:none;width:26px;height:26px;display:flex;align-items:center;justify-content:center;color:" + kind.c;
    if (busy) icon.style.cssText += ";width:22px;height:22px;margin:2px;border-radius:50%;border:2.5px solid #a9dcf840;border-top-color:#a9dcf8;animation:fmSpin .8s linear infinite";
    icon.innerHTML = kind.i;
    const body = document.createElement("div");
    body.style.cssText = "flex:1;min-width:0";
    const title = document.createElement("div");
    title.style.cssText = "margin:2px 0 3px;color:" + kind.c + ";font:600 17px/1 'Barlow Condensed','Bahnschrift SemiCondensed','Arial Narrow',sans-serif;" +
      "letter-spacing:.04em;text-transform:uppercase";  // the app's heading font, as on the pages' titles
    title.textContent = kind.t;
    const msg = document.createElement("div");
    msg.style.cssText = "color:#e6e9e7;overflow-wrap:anywhere";
    msg.textContent = text;
    body.append(title, msg);
    if (offer) {
      const go = document.createElement("button");
      go.textContent = action.label;
      go.style.cssText = "margin-top:8px;padding:5px 14px;border:0;border-radius:6px;background:" + kind.c + ";color:#0f1412;font:700 13px Inter,'Segoe UI',sans-serif;cursor:pointer";
      go.onclick = () => { clearInterval(window.fmNoticeTimer); n.remove(); action.run(); };
      body.append(go);
    }
    if (error) {
      const hint = document.createElement("div");
      hint.style.cssText = "margin-top:6px;color:#9aa49f;font-size:12px";
      hint.textContent = "Your data is safe. If it keeps happening: Menu → About → project page, with app.log.";
      body.append(hint);
    }
    const close = document.createElement("button");
    close.textContent = "×"; close.title = "Close";
    close.style.cssText = "flex:none;margin:-4px -2px 0 0;padding:0 6px;border:0;background:transparent;color:#9aa49f;font:20px/1 Inter,sans-serif;cursor:pointer";
    close.onclick = () => { clearInterval(window.fmNoticeTimer); n.remove(); };
    n.append(icon, body, close);
    if (!document.getElementById("fm-notice-css")) {
      const st = document.createElement("style"); st.id = "fm-notice-css";
      st.textContent = "@keyframes fmIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}@keyframes fmSpin{to{transform:rotate(360deg)}}";
      document.head.append(st);
    }
    document.body.append(n);
    if (busy) return;  // stays until the import's own notice replaces it
    // on screen: 3 s for a done notice, 8 s for an error, 10 s for one with a button; paused while the pointer is on it
    const total = offer ? 10000 : error ? 8000 : 3000; let left = total, paused = false;
    n.onmouseenter = () => paused = true; n.onmouseleave = () => paused = false;
    window.fmNoticeTimer = setInterval(() => {
      if (paused) return;
      left -= 100;  // no countdown bar: it just goes when its time is up
      if (left <= 0) { clearInterval(window.fmNoticeTimer); n.style.transition = "opacity .25s"; n.style.opacity = "0"; setTimeout(() => n.remove(), 260); }
    }, 100);
  };
})()"""


if __name__ == "__main__":
    App().start()
