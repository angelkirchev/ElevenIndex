"""ElevenIndex: imports FM27 Exporter files and shows the pages.

    python app.py

Open Menu → "Import file…" (under the logo on every page), choose an export, and it's imported straight away: dated with
the date in the exporter's file name (export_date), as your squad if the file name
starts with "First Team"/"Squad", else as a search named after the file. Then score.py runs and the 'last' page shows.
Needs pywebview (pip install pywebview).
"""
import csv
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
        try:
            self.purge_deleted()  # saves deleted more than 14 days ago
        except OSError:
            pass

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
        self.mark_imported(path)
        # where the import shows: your squad on Squad › Lineup, a player search on Players › Last import, staff on
        # Staff › Last import
        page = "last_staff.html" if kind == "staff" else "squad.html" if source is None else "last.html"
        url = (out / page).resolve().as_uri()
        try:
            here = (self.window.get_current_url() or "").split("?")[0].split("#")[0]
        except Exception:
            here = ""
        if unquote(here).lower() == unquote(url).lower():  # already on it: read the new file in place
            self.window.evaluate_js("location.reload()")
        else:
            self.open_url(url)

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
        self.open_url((out / page).resolve().as_uri())

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
                self.show_soon(url=(out / "squad.html").resolve().as_uri())
            else:
                self.notice = (notice or f"Now on {self.club_name() or name} ({name}): no players yet.", False, title)
                self.show_soon(html=screen('start', self.club_name()))

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
        note = f"{name} is deleted. You can restore it from Saves… for 14 days."
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

    # --- the menu's quick actions: recent exports, a watcher for new ones, drag and drop, backups, the update check --

    IMPORTABLE = (".csv", ".html", ".htm")

    def imported_list(self):
        f = self.save_dir / "imported.txt" if self.save_dir else None
        return f, (set(f.read_text(encoding="utf-8").split("\n")) - {""} if f and f.exists() else set())

    def mark_imported(self, path):
        """Note an export as imported into this save (by its name without extension: the exporter writes .csv and .html)."""
        f, done = self.imported_list()
        if f:
            f.write_text("\n".join(sorted(done | {Path(path).stem})) + "\n", encoding="utf-8")

    def export_files(self):
        """The exports folder's exports, one per name (its .csv, else its .html), newest first."""
        if not self.exports.is_dir():
            return []
        best = {}
        for f in self.exports.iterdir():
            if f.is_file() and f.suffix.lower() in self.IMPORTABLE:
                cur = best.get(f.stem)
                if cur is None or (f.suffix.lower() == ".csv" and cur.suffix.lower() != ".csv"):
                    best[f.stem] = f
        return sorted(best.values(), key=lambda f: f.stat().st_mtime, reverse=True)

    def menu_data(self):
        """What the menu shows: this save, the others, the newest exports, a newer version."""
        _, done = self.imported_list()
        recent = [{"name": f.stem, "path": str(f), "imported": f.stem in done,
                   "when": time.strftime("%d %b %H:%M", time.localtime(f.stat().st_mtime))} for f in self.export_files()[:5]]
        return {"current": self.club_name() or self.save, "save": self.save, "saves": self.saves(), "recent": recent,
                "update": getattr(self, "update", None), "auto": self.auto_import()}

    def import_path(self, path):
        """Import an export picked in the menu, offered by the watcher or dropped on the window."""
        f = Path(path)
        if f.suffix.lower() not in self.IMPORTABLE or not f.is_file():
            raise ValueError(f"Not an FM27 Exporter file: {f.name}")
        threading.Thread(target=self.import_now, args=(f,), daemon=True).start()

    def auto_import(self):
        return bool(self.config.get("auto_import", False))

    def set_auto_import(self, on):
        """Menu → Auto-import: on, every new export (F8 in FM) is imported as soon as it's saved. config.toml keeps it."""
        score.set_setting("auto_import", bool(on))
        self.config["auto_import"] = bool(on)

    def watch_exports(self):
        """Every few seconds: a new export in the folder (F8 in FM) is imported straight away when Auto-import is on,
        else it gets a notice with an Import button."""
        seen = {f.stem for f in self.export_files()}
        while True:
            time.sleep(3)
            try:
                if self.save is None or self.window is None:
                    continue
                new = [f for f in self.export_files() if f.stem not in seen]
                if not new:
                    continue
                time.sleep(1.5)  # the exporter writes a .csv, an .rtf and an .html: let it finish
                for f in self.export_files():
                    if f.stem not in seen:
                        seen.add(f.stem)
                        if self.auto_import():
                            self.import_now(f)  # one after another: each import waits for the one before
                            continue
                        self.window.evaluate_js(f"window.fmNewExport && fmNewExport({json.dumps(f.stem)}, {json.dumps(str(f))})")
            except Exception:
                log_error("watching the exports folder")

    def on_drop(self, e):
        """A file dropped on the window: import it (the page shows "Drop to import" while dragging)."""
        try:
            files = (e.get("dataTransfer") or {}).get("files") or []
            path = next((f.get("pywebviewFullPath") for f in files if f.get("pywebviewFullPath")), None)
        except Exception:
            path = None
        if not path or Path(path).suffix.lower() not in self.IMPORTABLE:
            return self.show_notice("Drop an FM27 Exporter file (.csv or .html) to import it.", True)
        last, when = getattr(self, "last_drop", (None, 0))
        if last == path and time.time() - when < 3:  # the same drop, reported twice
            return
        self.last_drop = (path, time.time())
        threading.Thread(target=self.import_now, args=(Path(path),), daemon=True).start()

    def latest_release(self):
        """GitHub's latest release: (version, page). Only its version number and link are fetched; nothing is sent."""
        req = urllib.request.Request("https://api.github.com/repos/angelkirchev/ElevenIndex/releases/latest",
                                     headers={"User-Agent": "ElevenIndex", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.load(r)
        return str(data.get("tag_name", "")).lstrip("v"), data.get("html_url") or score.PROJECT_URL + "/releases/latest"

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
        if not club:
            raise ValueError("Type the club you manage, exactly as FM shows it in its player lists.")
        name = slug(name) or slug(club)
        if (score.ROOT / "saves" / name).exists():
            raise ValueError(f"There's already a save called {name}: give this one another name, e.g. {name}-2.")
        score.new_save(name, club, division)
        self.commit(f"{name}: new save")
        self.switch_save(name, f"{club} ({name}) is ready. Now import your squad: press F8 on it in FM, then Menu → Import file…",
                         title="Save created")

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

    def on_load(self):
        """Every page, after each reload: add the toolbar, and show a pending notice."""
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
        data = {"exports": str(self.exports), "home": str(score.ROOT), "shortcut": bool(getattr(sys, "frozen", False))}
        body = SETUP_HTML.replace("__DATA__", json.dumps(data)).replace("__PROJECT__", score.PROJECT_URL)
        return screen("setup", body=body)

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
        self.notice = (f"All set up for {club}. Now import your squad: press F8 on it in FM, then Menu → Import file…", False, "Save created")
        self.show_soon(html=screen('start', self.club_name()))

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
            where = {"url": page.resolve().as_uri()} if page.exists() else {"html": screen("start", self.club_name())}
            title = window_title(self.save)
        self.window = webview.create_window(title, js_api=MainApi(self), width=1400, height=900,
                                            confirm_close=True, **where)  # closing asks first (localization below)
        self.window.events.loaded += self.on_load
        threading.Thread(target=self.watch_exports, daemon=True).start()
        threading.Thread(target=self.check_update, daemon=True).start()
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
        webview.start(icon=str(icon) if icon.exists() else None,  # no menu bar: the page's Menu button has it all
                      localization={"global.quitConfirmation": "Close ElevenIndex? Your saves and imports are kept."})


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

    def delete_save(self, name):
        return self._call(self._app.delete_save, name)

    def themes(self):
        return self._app.themes()

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


# The first-run setup page: the same pitch, logo and banner as screen(), a welcome title, the form on the right and
# what the app needs from FM in the bottom-left card.
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
eleven: from the lists you export in Football Manager 27. All on your computer.</p></div>
<div class="setup"><h2>Your save</h2>
<div class="hint">A few details and you're ready. More saves later: Menu → <b>Saves…</b></div>
<label for="club">Club you manage</label>
<input id="club" placeholder="e.g. Man Utd" autofocus>
<div class="hint">Exactly as FM writes it in its player lists: "Man Utd", not "Manchester United".</div>
<label for="currency">Currency FM shows wages in</label>
<select id="currency"><option>£</option><option>€</option><option>$</option></select>
<label for="exports">FM27 Exporter's folder</label>
<div class="row"><input id="exports"><button id="browse">Browse…</button></div>
<div class="hint">Where FM27 Exporter saves the lists you export with F8. It's usually found for you.</div>
<div class="check" id="shortcut-row"><input type="checkbox" id="shortcut" checked><label for="shortcut">Put a shortcut on the desktop</label></div>
<div class="error" id="error"></div>
<button class="go" id="go">Start</button>
<div class="home" id="home"></div>
</div>
<div class="card"><h2>What it needs from FM</h2><ol>
<li><b>BepInEx 6</b> and the <b>FM27 Exporter</b> plugin in FM's folder: they add the <span class="k">F8</span> export (how: the <a href="__PROJECT__" target="_blank" rel="noopener">project page</a>).</li>
<li><b>Our views and filters:</b> extract <b>ElevenIndex-FM27-views-and-filters.zip</b> into Documents\\Sports Interactive\\Football Manager 27.</li>
<li>Fill in <b>Your save</b> and press <b>Start</b>.</li>
<li>In FM, your squad with the view <b>Squad - All Players View</b>, press <span class="k">F8</span>, then here Menu → <b>Import file…</b></li>
</ol><div class="more"><b>Private:</b> nothing leaves your computer; your saves and pages stay in the folder shown under Start.</div></div>
<script>
const d = __DATA__, $ = id => document.getElementById(id);
$("exports").value = d.exports;
$("home").textContent = "Kept in " + d.home;
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


def screen(kind, club="", detail="", body=""):
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
<li><b>Open the save again:</b> Menu → <b>Saves…</b>, pick it: every page is rebuilt from your data.</li>
<li><b>Import again</b> if it happened after an import: Menu → <b>Import file…</b> (re-importing the same file just updates it).</li>
<li><b>Restart ElevenIndex.</b> It rescores everything when it starts.</li>
<li><b>Still wrong?</b> Report it on the project page (Menu → <b>About</b>) and attach <b>app.log</b> from <b>{esc(score.ROOT)}</b>.</li>
</ol><div class="more">Nothing is deleted when this happens: your exports stay in FM27 Exporter's folder and your data in <b>{esc(score.ROOT)}</b>.</div></div>"""
    else:
        body = f"""<div class="title"><span class="tag">New save</span>
<h1>{esc(club) + ': n' if club else 'N'}o players yet</h1><p>Import your squad and every player gets a score at every position, your best eleven, and what to do next.</p></div>
<div class="card"><h2>Get your squad in</h2><ol>
<li>In FM, open your squad and pick the view <b>Squad - All Players View</b> (the view menu at the top of the list).</li>
<li>Point at the list and press <span class="k">F8</span>: FM27 Exporter saves it to its exports folder.</li>
<li>Here: Menu → <b>Import file…</b> and choose the newest <b>First Team …</b> file.</li>
</ol><div class="more"><b>Scouting:</b> Player Search with <b>Search - Outfield Players View</b> (or <b>Goalkeepers</b>) and a position
filter, then F8 and import the same way. <b>No such view in FM?</b> Extract <b>ElevenIndex-FM27-views-and-filters.zip</b> into
Documents\\Sports Interactive\\Football Manager 27, then in FM's view menu: <b>Custom → Import view</b>.</div></div>"""
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
  // empty strip), its list opening downwards: this save, the newest exports, Import file…, the exports folder, your
  // saves, Theme…, Back up this save…, About, and a newer version when there is one. Keys: M opens it, I imports.
  let strip = document.querySelector(".subnav");
  if (!strip) {
    strip = document.createElement("div"); strip.className = "subnav";
    const top = document.querySelector(".bar");
    if (top) top.after(strip); else document.body.prepend(strip);
  }
  strip.style.paddingLeft = "14px";
  const m = document.createElement("button");
  m.id = "fm-import"; m.title = "Menu (M): import, recent exports, saves, theme, backup, about";
  m.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true">' +
    '<path d="M2 3.5h12M2 8h12M2 12.5h12" stroke="#0f1412" stroke-width="2" stroke-linecap="round"/></svg>Menu';
  m.style.cssText = "flex:none;align-self:center;display:flex;gap:7px;align-items:center;margin:4px 16px 4px 0;padding:4px 12px;border:0;" +
    "border-radius:6px;background:var(--accent-strong,#a9d93b);color:#0f1412;font:700 13px Inter,'Segoe UI',system-ui,sans-serif;cursor:pointer";
  strip.prepend(m);
  const list = document.createElement("div");
  list.id = "fm-menu";
  list.style.cssText = "position:fixed;display:none;z-index:10000;width:320px;max-height:calc(100vh - 110px);overflow:auto;padding:6px;" +
    "border:1px solid var(--line,#2a332f);border-radius:10px;background:var(--pop,#121815);box-shadow:0 12px 30px #000c";
  document.body.append(list);
  // main: the one used most (Import file…), in the accent, lighter than the rest
  const item = (text, hint, run, main) => {
    const it = document.createElement("button");
    const rest = main ? "color-mix(in srgb, var(--blue,#c5f04f) 16%, transparent)" : "transparent";
    const over = main ? "color-mix(in srgb, var(--blue,#c5f04f) 28%, transparent)" : "var(--hover,#232c29)";
    it.style.cssText = "display:block;width:100%;padding:7px 12px;border:0;border-radius:6px;background:" + rest + ";" +
      "color:" + (main ? "var(--blue,#c5f04f)" : "#f2efe7") + ";font:" + (main ? 600 : 500) + " 14px Inter,'Segoe UI',system-ui,sans-serif;text-align:left;cursor:pointer";
    it.innerHTML = text + (hint ? `<div style="color:${main ? "#cfd6d1" : "#9aa49f"};font-size:12px;font-weight:400">${hint}</div>` : "");
    it.onmouseenter = () => it.style.background = over;
    it.onmouseleave = () => it.style.background = rest;
    it.onclick = () => { list.style.display = "none"; run(); };
    return it;
  };
  const line = () => { const l = document.createElement("div"); l.style.cssText = "height:1px;margin:5px 6px;background:var(--line,#2a332f)"; return l; };
  const label = t => { const l = document.createElement("div"); l.textContent = t;
    l.style.cssText = "padding:6px 12px 3px;color:#9aa49f;font:700 10px/1 Inter,'Segoe UI',sans-serif;letter-spacing:.08em;text-transform:uppercase"; return l; };
  const escH = t => String(t).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  const fill = async () => {
    const d = window.pywebview ? await pywebview.api.menu_data() : { recent: [], saves: [] };
    list.innerHTML = "";
    if (d.current) {  // the save you're in, as a highlighted card
      const cur = document.createElement("div");
      cur.innerHTML = `<div style="color:var(--blue,#c5f04f);font:700 10px/1 Inter,'Segoe UI',sans-serif;letter-spacing:.08em;text-transform:uppercase">Current save</div>` +
        `<div style="margin-top:4px;color:#f2efe7;font:600 22px/1 'Barlow Condensed','Bahnschrift SemiCondensed','Arial Narrow',sans-serif;letter-spacing:.02em">${escH(d.current)}</div>` +
        `<div style="margin-top:3px;color:#9aa49f;font-size:12px">${escH(d.save || "")}</div>`;
      cur.style.cssText = "margin:2px 2px 6px;padding:10px 12px;border-radius:8px;border-left:3px solid var(--blue,#c5f04f);" +
        "background:color-mix(in srgb, var(--blue,#c5f04f) 10%, transparent)";
      list.append(cur);
    }
    if (d.recent && d.recent.length) {
      // the newest export; ▾ shows the last five, ▴ folds them up again
      const head = document.createElement("div");
      head.style.cssText = "display:flex;align-items:center;justify-content:space-between;padding-right:6px";
      head.append(label("Recent exports"));
      list.append(head);
      const exp = f => item(`<span style="display:inline-block;width:7px;height:7px;margin-right:7px;border-radius:50%;vertical-align:1px;background:${f.imported ? "#5a645f" : "var(--blue,#c5f04f)"}"></span>${escH(f.name)}`,
        `${f.when} · ${f.imported ? "imported" : "<b style='color:var(--blue,#c5f04f)'>new</b>"} · click to import`, () => pywebview.api.import_path(f.path));
      const first = document.createElement("div");
      first.style.cssText = "display:flex;align-items:center;gap:4px";
      const top = exp(d.recent[0]); top.style.flex = "1"; top.style.minWidth = "0";
      first.append(top);
      list.append(first);
      if (d.recent.length > 1) {
        const more = document.createElement("div");
        more.style.display = window.fmRecentOpen ? "block" : "none";
        d.recent.slice(1).forEach(f => more.append(exp(f)));
        const tog = document.createElement("button");
        const mark = () => { tog.textContent = window.fmRecentOpen ? "▴" : "▾"; tog.title = window.fmRecentOpen ? "Show only the newest" : `Show the last ${d.recent.length}`; };
        tog.style.cssText = "flex:none;width:30px;height:30px;border:1px solid var(--line,#2a332f);border-radius:6px;background:transparent;color:#cfd6d1;font:14px/1 Inter,sans-serif;cursor:pointer";
        tog.onclick = e => { e.stopPropagation(); window.fmRecentOpen = !window.fmRecentOpen; more.style.display = window.fmRecentOpen ? "block" : "none"; mark(); };
        mark();
        first.append(tog);
        list.append(more);
      }
    }
    list.append(item("Import file…", "an FM27 Exporter file (F8 in FM), or drop one on the window · key I", () => window.pywebview && pywebview.api.pick_file(), true),
      item("Open the exports folder", "where FM27 Exporter saves its files", () => window.pywebview && pywebview.api.open_exports()),
      item(`Auto-import <span style="margin-left:6px;padding:1px 8px;border-radius:999px;font-size:11px;font-weight:700;${d.auto
        ? 'color:#0f1412;background:#c5f04f">ON' : 'color:#cfd6d1;background:#2a332f">OFF'}</span>`,
        d.auto ? "every new export (F8 in FM) is imported by itself · click to turn off" : "import each new export (F8 in FM) by itself · click to turn on",
        async () => {
          const r = await pywebview.api.set_auto_import(!d.auto);
          if (r && r.error) return window.fmNotice(r.error, true);
          window.fmNotice(d.auto ? "New exports will offer an Import button again." : "Every new export (F8 in FM) is now imported by itself.",
            false, { title: d.auto ? "Auto-import off" : "Auto-import on" });
        }), line(),
      item("Saves…", "switch, start, delete, back up or restore a save", () => window.fmSaves()), line(),
      item("Theme…", "the app's colours", () => window.fmTheme()),
      item("Check for updates", "is there a newer ElevenIndex?", async () => {
        window.fmNotice("Checking for a new version…", false);
        const r = await pywebview.api.check_now();
        if (r.error) return window.fmNotice(r.error, true);
        const u = r.result;
        if (u.newer) window.fmNotice(`ElevenIndex ${u.latest} is out (you have ${u.current}).`, false, { title: "New version", label: "Download", run: () => window.fmOpen(u.url) });
        else window.fmNotice(`You have the latest version (${u.current}).`, false, { title: "Up to date", label: "Release notes", run: () => window.fmOpen(u.url) });
      }),
      item("About", "version, your settings, project page", () => window.fmAbout()));
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

  // Drag and drop: "Drop to import" while a file is dragged over the window; the app imports it (on_drop)
  const dropHint = document.createElement("div");
  dropHint.style.cssText = "position:fixed;inset:12px;z-index:10001;display:none;align-items:center;justify-content:center;pointer-events:none;" +
    "border:3px dashed var(--blue,#c5f04f);border-radius:16px;background:#0f1412cc;color:#f2efe7;font:600 26px 'Barlow Condensed','Bahnschrift SemiCondensed',sans-serif;letter-spacing:.04em";
  dropHint.textContent = "Drop the file to import it";
  document.body.append(dropHint);
  let depth = 0;
  const isFile = e => [...(e.dataTransfer?.types || [])].includes("Files");
  document.addEventListener("dragenter", e => { if (isFile(e)) { depth++; dropHint.style.display = "flex"; } });
  document.addEventListener("dragleave", e => { if (isFile(e) && --depth <= 0) { depth = 0; dropHint.style.display = "none"; } });
  document.addEventListener("dragover", e => { if (isFile(e)) e.preventDefault(); });
  document.addEventListener("drop", e => { depth = 0; dropHint.style.display = "none"; if (isFile(e)) e.preventDefault(); });

  // a new export in the folder (the app's watcher), and a newer version on GitHub
  window.fmNewExport = (name, path) => window.fmNotice(name, false, { title: "New export found", label: "Import", run: () => pywebview.api.import_path(path) });
  window.fmUpdate = (u, show) => { if (show) window.fmNotice(`ElevenIndex ${u.version} is out (you have """ + VERSION + """).`, false,
    { title: "New version", label: "Download", run: () => window.fmOpen(u.url) }); };

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
    bar.append(c);
  }
  if (bar.children.length) document.body.append(bar);

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
    const box = css(make("div"), "background:#121815;color:#f2efe7;border:1px solid #2a332f;border-radius:10px;padding:20px 22px;width:500px;max-width:92vw;max-height:85vh;overflow:auto;box-shadow:0 8px 30px #000");
    const error = css(make("div"), "color:#f87171;min-height:1.2em;margin-top:8px");
    const run = async (call) => { error.textContent = "Working…"; const r = await call(); if (r && r.error) error.textContent = r.error; else shade.remove(); };

    box.append(css(make("h3", "Your saves"), "margin:0 0 10px;font-size:16px"));
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
          window.fmNotice(`${s.club} (${s.name}) is deleted. You can restore it from Saves… for 14 days.`, false, { title: "Save deleted" });
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
  // Theme: five colour variations; a click changes the page at once and every page from then on
  window.fmTheme = async () => {
    document.getElementById("fm-theme")?.remove();
    const r = await pywebview.api.themes();
    const shade = css(make("div"), "position:fixed;inset:0;z-index:10000;background:#000a;display:flex;align-items:center;justify-content:center;font:14px Inter,'Segoe UI',system-ui,sans-serif");
    shade.id = "fm-theme";
    shade.tabIndex = -1;
    shade.onclick = e => { if (e.target === shade) shade.remove(); };
    shade.addEventListener("keydown", e => { e.stopPropagation(); if (e.key === "Escape") shade.remove(); });
    const box = css(make("div"), "background:var(--pop,#121815);color:#f2efe7;border:1px solid var(--line,#2a332f);border-radius:10px;padding:20px 22px;width:360px;max-width:90vw;box-shadow:0 8px 30px #000");
    box.append(css(make("h3", "Theme"), "margin:0 0 12px;font-size:16px"));
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
  // The corner notice (an import done, an error, "Importing…"): a dark card with a coloured edge, an icon, a title,
  // the message, a close button and a bar for how long it stays (paused while the mouse is on it).
  window.fmNotice = (text, error, action) => {
    document.getElementById("fm-notice")?.remove();
    clearInterval(window.fmNoticeTimer);
    const busy = !error && /^Importing|…$/.test(text), done = !error && !busy;
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
    n.style.cssText = "position:fixed;right:16px;bottom:" + (document.getElementById("fm-clear") ? 68 : 16) + "px;z-index:9999;width:420px;max-width:calc(100vw - 32px);overflow:hidden;" +
      "display:flex;gap:12px;align-items:flex-start;padding:12px 12px 14px 14px;color:#f2efe7;background:#1d2522f5;" +
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
    const bar = document.createElement("div");
    bar.style.cssText = "position:absolute;left:0;bottom:0;height:3px;width:100%;background:" + kind.c + ";opacity:.7";
    n.append(icon, body, close, bar);
    if (!document.getElementById("fm-notice-css")) {
      const st = document.createElement("style"); st.id = "fm-notice-css";
      st.textContent = "@keyframes fmIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}@keyframes fmSpin{to{transform:rotate(360deg)}}";
      document.head.append(st);
    }
    document.body.append(n);
    if (busy) { bar.remove(); return; }  // stays until the import's own notice replaces it
    const total = offer ? 20000 : error ? 15000 : 6000; let left = total, paused = false;
    n.onmouseenter = () => paused = true; n.onmouseleave = () => paused = false;
    window.fmNoticeTimer = setInterval(() => {
      if (paused) return;
      left -= 100; bar.style.width = Math.max(0, left / total * 100) + "%";
      if (left <= 0) { clearInterval(window.fmNoticeTimer); n.remove(); }
    }, 100);
  };
})()"""


if __name__ == "__main__":
    App().start()
