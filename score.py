"""Score Football Manager players and staff from transcribed screenshots, and write the pages.

    python3 score.py                  score the active save (config.toml) -> out/<save>/*.html
    python3 score.py --open [PAGE]    ...then open a page in the browser (default: last)
    python3 score.py --save NAME      score another save
    python3 score.py init             first-time setup: config.toml and a save
    python3 score.py screenshots      list the newest screenshots in config.toml's screenshots_dir

Reads saves/<save>/obs/<YYYY-MM-DD>_<source>_<view>.csv (players) and saves/<save>/staff/<YYYY-MM-DD>_<source>.csv,
scores them with weights/<save.toml's weights>.toml, and writes one page per entry in PAGES.
"""
import argparse
import base64
import csv
import html
import json
import os
import platform
import re
import sys
import time
import tomllib
import webbrowser
from pathlib import Path


def documents():
    """The user's Documents folder (wherever Windows has put it, OneDrive included)."""
    if sys.platform == "win32":
        import ctypes
        import uuid
        folder_id = (ctypes.c_ubyte * 16).from_buffer_copy(uuid.UUID("FDD39AD0-238F-46AF-ADB4-6C85480369C7").bytes_le)
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(path)) == 0:
            found = Path(path.value)
            ctypes.windll.ole32.CoTaskMemFree(path)
            return found
    return Path.home() / "Documents"


# APP: the program's own files (weights, templates, theme). ROOT: the user's data (config.toml, saves/, out/).
# Run from the source folder they're the same folder. In the packaged .exe the program files are unpacked to a
# temporary folder, so the data lives in Documents\FMAnalyst. FMANALYST_HOME overrides where the data goes.
APP = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
ROOT = Path(os.environ["FMANALYST_HOME"]) if os.environ.get("FMANALYST_HOME") else \
    documents() / "FMAnalyst" if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG = ROOT / "config.toml"
VERSION = "1.2.0"  # the app's version: the window title and the guide show it; the release tag is v<VERSION>
PROJECT_URL = "https://github.com/angelkirchev/FMAnalyst"
PAGES = ["squad", "analysis", "development", "charts", "elsewhere", "last", "filters", "staff", "last_staff", "staff_filters",
         "attributes", "guide"]  # keys 1-9, then 0, then a and g, in this order
PAGE_KEYS = {"attributes": "a", "guide": "g"}  # past the tenth page, a letter
# The menu's names, and where its groups start (players 1-6, staff 7-9, then settings and the guide)
PAGE_LABELS = {"squad": "Squad", "development": "Development", "elsewhere": "All players", "last": "Last player import", "filters": "Player search",
               "charts": "Market", "staff": "All staff", "last_staff": "Last staff import", "staff_filters": "Staff search",
               "analysis": "Analysis", "attributes": "Attributes", "guide": "Guide"}
NAV_GROUP_STARTS = {"elsewhere", "staff", "attributes"}
META = {"Name", "Club", "Age", "Position", "Left Foot", "Wage", "Transfer Value", "Loan", "Minutes", "Rating", "Division"}  # every other column is an attribute
STAFF_META = {"Name", "Club", "Age", "Job", "Personality"}
LEFT_FOOTED = {"Fairly Strong", "Strong", "Very Strong"}
MIN_KNOWN_WEIGHT = 0.85  # a player is scored at a position when attributes carrying 85% of its weight are known (FM shows "-" for unscouted ones)
PARTIAL_SQUAD = re.compile(r"-(u\d+s?|reserves)$")  # source of a youth/reserve squad screen: not the whole club
SEARCH_MIN_WEIGHT = 2  # staff filters page: attributes weighted below this get no search minimum
# Filters page (players): search minimums for the fewest top-weighted attributes making up this share of a position's
# weight, at least SEARCH_MIN_ATTRS and at most SEARCH_MAX_ATTRS: few where a handful of attributes dominate, more where
# the weight is spread out (keepers)
SEARCH_WEIGHT_SHARE, SEARCH_MIN_ATTRS, SEARCH_MAX_ATTRS = 0.85, 3, 8
CHART_ATTRS, CHART_GK_ATTRS = 8, 5  # charts page: the attribute menu lists a position's top attributes by weight
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}



def load_toml(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def weights_file(name):
    """weights/<name>.toml: the user's own copy in the data folder if there is one, else the program's."""
    for folder in (ROOT / "weights", APP / "weights"):
        if (folder / f"{name}.toml").is_file():
            return folder / f"{name}.toml"
    return APP / "weights" / f"{name}.toml"


# One built-in set, weights/default.toml. A save's own weights (changed on the Attributes page) live in its folder,
# private to it: save.toml's weights = "custom". Any other name (an older version's fm27_test…) means the default.
DEFAULT, CUSTOM = "default", "custom"


def weights_path(cfg, save_dir):
    """The weights file a save uses: its own saves/<save>/weights.toml for "custom", else the default."""
    return save_dir / "weights.toml" if cfg.get("weights") == CUSTOM else weights_file(DEFAULT)


def load_weights(cfg, save_dir):
    return load_toml(weights_path(cfg, save_dir))


def toml_key(k):
    return k if re.fullmatch(r"[A-Za-z0-9_-]+", k) else toml_str(k)


def toml_value(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return f"{v:g}" if isinstance(v, float) else str(v)
    if isinstance(v, list):
        return "[" + ", ".join(toml_value(x) for x in v) + "]"
    return toml_str(str(v))


def weights_toml(w, header):
    """A weights file's text: its tables in the usual order (positions, keeper, ages, then staff), values as given."""
    lines = [f"# {line}" if line else "#" for line in header.splitlines()]

    def table(name, d):
        lines.extend(["", f"[{name}]", *(f"{toml_key(k)} = {toml_value(v)}" for k, v in d.items() if not isinstance(v, dict))])
        for k, v in d.items():
            if isinstance(v, dict):
                table(f"{name}.{toml_key(k)}", v)

    order = ["outfield", "gk", "age_benchmark", "staff_words", "staff_names", "staff"]
    for name in [*order, *(k for k in w if k not in order)]:
        if name in w:
            v = w[name]
            if isinstance(v, dict) and all(isinstance(x, dict) for x in v.values()):  # [outfield.DC], [staff.Mgr]…
                for k, sub in v.items():
                    table(f"{name}.{toml_key(k)}", sub)
            else:
                table(name, v)
    return "\n".join(lines).lstrip("\n") + "\n"


def with_positions(base, positions):
    """base's weights with the position tables replaced by positions ({"GK": {...}, "DC": {...}…}), zero weights dropped."""
    clean = {p: {a: float(v) if float(v) % 1 else int(float(v)) for a, v in t.items() if float(v) > 0} for p, t in positions.items()}
    if bad := [p for p, t in clean.items() if not t]:
        raise ValueError(f"Every position needs at least one attribute with a weight: {', '.join(bad)} has none.")
    return {**base, "gk": clean.get("GK", base["gk"]), "outfield": {p: clean.get(p, t) for p, t in base["outfield"].items()}}


def weights_table(positions, header, sep="\t"):
    """The position weights as a plain table, for Notepad or Excel: a row per attribute (most weighted first), a column
    per position; blank = not used. Lines starting with # are notes."""
    total = {}
    for t in positions.values():
        for a, v in t.items():
            total[a] = total.get(a, 0) + v
    cols = list(positions)
    fmt = lambda v: "" if v is None else f"{v:g}"
    lines = [f"# {line}" for line in header.splitlines()]
    lines.append(sep.join(["Attribute", *cols]))
    for a in sorted(total, key=lambda a: (-total[a], a)):
        lines.append(sep.join([a, *(fmt(positions[p].get(a)) for p in cols)]))
    return "\n".join(lines) + "\n"


def read_weights_table(text):
    """weights_table's table back to {position: {attribute: weight}}, whatever it was saved with: tabs, commas or
    semicolons, and decimal commas (9,3) from Excel. Blank, 0 or "-" = not used."""
    lines = [l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#") and not l.lower().startswith("sep=")]
    if not lines:
        raise ValueError("the file is empty")
    sep = max(("\t", ";", ","), key=lambda s: lines[0].count(s))
    rows = list(csv.reader(lines, delimiter=sep))
    head = [h.strip() for h in rows[0]]
    if len(head) < 2 or head[0].lower() != "attribute":
        raise ValueError("its first row should be: Attribute, then the positions (GK, DC, DL/DR…)")
    positions = {p: {} for p in head[1:] if p}
    for row in rows[1:]:
        attr = row[0].strip() if row else ""
        for p, cell in zip(head[1:], row[1:]):
            cell = cell.strip().replace(",", ".")
            if not attr or not p or cell in ("", "-", "–"):
                continue
            try:
                v = float(cell)
            except ValueError:
                raise ValueError(f"{attr} at {p} isn't a number: {cell!r}")
            if v < 0:
                raise ValueError(f"{attr} at {p} is negative")
            if v:
                positions[p][attr] = int(v) if v.is_integer() else v
    return positions


def set_save_key(save_dir, key, value):
    """Set one line of save.toml (key = "value"), keeping the rest of the file as it is."""
    path = save_dir / "save.toml"
    text, line = path.read_text(encoding="utf-8"), f"{key} = {toml_str(value)}"
    text = re.sub(rf"^{key}\s*=.*$", line, text, flags=re.M) if re.search(rf"^{key}\s*=", text, re.M) \
        else text.rstrip("\n") + "\n" + line + "\n"
    path.write_text(text, encoding="utf-8")


def money(text):
    """'£1.5K p/w' -> 1500.0, '€2.1M' -> 2100000.0; None for N/A or blank. Any currency symbol."""
    m = re.match(r"\D*?([\d.]+)\s*([KMB]?)", text.strip(), re.I)
    return float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2).upper()] if m else None


def weighted(attrs, groups, min_known=1.0):
    """Each group's Σ(attribute × weight) / Σ(weights) over its known attributes, for the groups where those carry at
    least min_known of the total weight (1.0: every attribute must be known)."""
    scores = {}
    for key, w in groups.items():
        known = {a: x for a, x in w.items() if a in attrs}
        if known and sum(known.values()) >= min_known * sum(w.values()):
            scores[key] = sum(attrs[a] * x for a, x in known.items()) / sum(known.values())
    return scores


def timeline(dates):
    """(date, snapshot, attributes known by then), oldest first: each attribute carries forward until re-observed."""
    attrs = {}
    for date in sorted(dates):
        attrs.update(dates[date]["attrs"])
        yield date, dates[date], dict(attrs)


def read_rows(folder):
    """(path, row) for every CSV row in a folder, oldest file first."""
    for path in sorted(folder.glob("*.csv")) if folder.exists() else []:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                yield path, row


# --- players ---

# An observation file's name: <date>[T<hhmm>]_<source>_<view>.csv. A squad export carries its time too
# (2026-11-14T0032_my-club_outfield.csv), so two squad exports on one day are two snapshots, not one replacing the other.
OBS_NAME = re.compile(r"(\d{4}-\d{2}-\d{2}(?:T\d{4})?)_(.+)_([a-z]+)")


def obs_parts(stem):
    """'2026-11-14T0032_my-club_outfield' -> ('2026-11-14T0032', 'my-club', 'outfield')."""
    m = OBS_NAME.fullmatch(stem)
    return m.groups() if m else (stem[:10], stem[11:].rsplit("_", 1)[0], stem.rsplit("_", 1)[-1])


def load_players(obs_dir, aliases=None):
    """Every observation row, grouped by player name (truncated names via `aliases` to the full one) and then game date;
    plus each file's date, source and clubs."""
    players, files = {}, {}
    for path, row in read_rows(obs_dir):
        row["Name"] = (aliases or {}).get(row["Name"], row["Name"])
        date, source, view = obs_parts(path.stem)
        files.setdefault(path.name, {"date": date, "source": source, "clubs": set()})["clubs"].add(row["Club"])
        p = players.setdefault(row["Name"], {"keeper": False, "files": set(), "dates": {}})
        p["keeper"] |= view == "gk"  # anyone in a GK view is a keeper
        p["files"].add(path.name)
        snap = p["dates"].setdefault(date, {"attrs": {}})
        snap.update(club=row["Club"], age=int(row["Age"]))
        if row.get("Left Foot"):
            snap["leftFoot"] = row["Left Foot"]
        if row.get("Wage"):
            snap["wage"] = money(row["Wage"])
        if row.get("Transfer Value"):
            lo, _, hi = row["Transfer Value"].partition(" - ")
            snap["value"] = (money(lo), money(hi or lo))
        if row.get("Position"):  # FM's positions for the player (when the view shows them)
            snap["position"] = row["Position"]
        if "Loan" in row:  # squad screens only: "in" (blue name), "out" (red name), "trial" (yellow name) or blank
            snap["loan"] = row["Loan"] or None
        if row.get("Rating"):  # season minutes and average rating, from search screens that show them
            snap.update(minutes=int(row["Minutes"]), rating=float(row["Rating"]), division=row.get("Division"))
        snap["attrs"].update({k: int(v) for k, v in row.items() if k not in META and v})  # blank = unreadable
    return players, files


def player_row(name, p, w):
    """A player's latest state, plus their history: one entry per observation date."""
    positions = {"GK": w["gk"]} if p["keeper"] else w["outfield"]
    ages = sorted(int(a) for a in w["age_benchmark"])
    known = {"leftFoot": None, "wage": None, "value": (None, None), "loan": None, "position": None}
    history = []
    for date, snap, attrs in timeline(p["dates"]):
        known.update({k: v for k, v in snap.items() if k in known})
        scores = weighted(attrs, positions, MIN_KNOWN_WEIGHT)
        if not scores:  # too few attributes known by then for these weights: leave this date out
            continue
        # Best: the highest score among the positions FM says they play, when an export showed them; else any position
        plays = [p for p in fm_positions(known["position"]) if p in scores]
        best = max(plays or scores, key=scores.get)
        bench = w["age_benchmark"][str(min(max(snap["age"], ages[0]), ages[-1]))]
        history.append({"date": date, "club": snap["club"], "age": snap["age"], "leftFoot": known["leftFoot"],
                        **scores, "best": best, "score": scores[best], "vsAge": scores[best] / bench,
                        "minutes": snap.get("minutes"), "rating": snap.get("rating"), "division": snap.get("division"),
                        "attrs": attrs})
    if not history:
        return None  # not enough attributes to score any position at any date
    latest = history[-1]
    return {
        "name": name,
        **{k: v for k, v in latest.items() if k not in ("date", "leftFoot", "attrs")},  # rating: latest observation only
        "position": known["position"], "plays": [p for p in fm_positions(known["position"]) if p in positions] or None,
        "left": None if known["leftFoot"] is None else "yes" if known["leftFoot"] in LEFT_FOOTED else "no",
        "foot": None if known["leftFoot"] is None else "Left" if known["leftFoot"] in LEFT_FOOTED else "Right",
        "loan": known["loan"], "wage": known["wage"], "valueLo": known["value"][0], "value": known["value"][1],  # value sorts on the top of FM's range
        "history": history,
    }


def fm_positions(text):
    """FM's position text ("D/WB (R), M (RC)", "AM (RL), ST (C)", "GK") -> the weights' position groups, in order."""
    groups = []
    for part in (text or "").split(","):
        m = re.match(r"\s*([A-Z/]+)\s*(?:\(([RLC]+)\))?", part)
        if not m:
            continue
        sides = m.group(2) or "C"
        wide = bool(set(sides) & set("RL"))
        for role in m.group(1).split("/"):
            found = {"GK": ["GK"], "DM": ["DM"], "ST": ["ST"], "WB": ["WBL/WBR"],
                     "D": ["DC"] * ("C" in sides) + ["DL/DR"] * wide,
                     "M": ["MC"] * ("C" in sides) + ["ML/MR"] * wide,
                     "AM": ["AMC"] * ("C" in sides) + ["AML/AMR"] * wide}.get(role, [])
            groups += [g for g in found if g not in groups]
    return groups


def player_cols(w, with_club):
    wage = {"key": "wage", "label": "Wage", "num": True, "fmt": "money"}
    club = [{"key": "club", "label": "Club"}, wage, {"key": "value", "label": "Value", "num": True, "fmt": "range"}]
    return [
        {"key": "name", "label": "Player"},
        *(club if with_club else []),
        {"key": "age", "label": "Age", "num": True},
        *([] if with_club else [wage]),
        {"key": "foot", "label": "Foot"},  # Left = left foot Fairly Strong or better (the export only shows the left foot)
        *({"key": p, "label": p, "num": True, "fmt": "f1"} for p in ["GK", *w["outfield"]]),
        {"key": "position", "label": "FM position"},
        {"key": "best", "label": "Best"},
        {"key": "score", "label": "Score", "num": True, "fmt": "f1"},
        {"key": "vsAge", "label": "vs Age", "num": True, "fmt": "pct"},
        *([{"key": "rating", "label": "Rating", "num": True, "fmt": "f2"},
           {"key": "minutes", "label": "Mins", "num": True}] if with_club else []),
    ]


def search_attrs(weights):
    """The attributes worth a minimum in FM's search: the top-weighted ones making up SEARCH_WEIGHT_SHARE of the weight."""
    ranked, total, picked, share = sorted(weights.items(), key=lambda kv: -kv[1]), sum(weights.values()), [], 0
    for attr, weight in ranked:
        if len(picked) >= SEARCH_MAX_ATTRS or (share >= SEARCH_WEIGHT_SHARE * total and len(picked) >= SEARCH_MIN_ATTRS):
            break
        picked.append(attr)
        share += weight
    return set(picked)


def filters(squad, w):
    """Per position: the squad's best (top 3 outfield, top 1 GK) and suggested FM search minimums."""
    out = []
    for pos, pw in {"GK": w["gk"], **w["outfield"]}.items():
        ranked = sorted((r for r in squad if r.get(pos) is not None), key=lambda r: -r[pos])[:1 if pos == "GK" else 3]
        if not ranked:
            continue
        rows, searched = [], search_attrs(pw)
        for attr, weight in sorted(pw.items(), key=lambda kv: -kv[1]):
            values = [r["history"][-1]["attrs"].get(attr) for r in ranked]
            known = [v for v in values if v is not None]
            rows.append({"attr": attr, "weight": weight, "values": values,
                         "min": int(sum(known) / len(known) + 0.5) if attr in searched and known else None})
        out.append({"pos": pos, "rows": rows,
                    "players": [{"name": r["name"], "age": r["age"], "score": r[pos]} for r in ranked]})
    return out


def charts(rows, squad, w, cfg, last=frozenset()):
    """Every player observed, plus the squad. charts.html compares the squad with the latest import (`last`: names, as on
    the last page), everyone imported, or a division (players rated in their latest observation, from a league search)."""
    # the attribute menu: each position's top attributes by weight (CHART_ATTRS, CHART_GK_ATTRS for keepers)
    tops = {p: [a for a, _ in sorted(t.items(), key=lambda kv: -kv[1])][:CHART_GK_ATTRS if p == "GK" else CHART_ATTRS]
            for p, t in {"GK": w["gk"], **w["outfield"]}.items()}
    wanted = {a for top in tops.values() for a in top}
    out = []
    for r, mine in [*((r, False) for r in rows if r not in squad), *((r, True) for r in squad)]:
        attrs = r["history"][-1]["attrs"]
        out.append({"name": r["name"], "club": r["club"], "division": r["division"], "age": r["age"], "squad": mine,
                    "last": r["name"] in last, "wage": r["wage"],
                    "rating": r["rating"], "minutes": r["minutes"], "best": r["best"],
                    "scores": {p: r[p] for p in ["GK", *w["outfield"]] if r.get(p) is not None},
                    "attrs": {a: attrs[a] for a in wanted if a in attrs}})
    return {"positions": ["GK", *w["outfield"]], "players": out, "tops": tops,
            "managed": cfg["managed_club"], "division": cfg.get("division")}


# Common FM formations, as the weights' position groups (a wide pair counts twice: one left, one right). Roles and duties
# don't show in the scores, so shapes with the same positions (5-2-3 Wide and 5-2-2-1) rank the same.
FORMATIONS = [
    ("4-2-3-1", "The most common, usually gegenpress", {"DL/DR": 2, "DC": 2, "MC": 2, "AML/AMR": 2, "AMC": 1, "ST": 1}),
    ("4-3-3 DM Wide", "One holding midfielder, two wingers (4-1-2-3)", {"DL/DR": 2, "DC": 2, "DM": 1, "MC": 2, "AML/AMR": 2, "ST": 1}),
    ("4-3-3", "A flat midfield three", {"DL/DR": 2, "DC": 2, "MC": 3, "AML/AMR": 2, "ST": 1}),
    ("4-2-3-1 DM Wide", "Two holding midfielders behind the ten (4-2-1-3)", {"DL/DR": 2, "DC": 2, "DM": 2, "AMC": 1, "AML/AMR": 2, "ST": 1}),
    ("3-4-3", "The most popular back three", {"DC": 3, "ML/MR": 2, "MC": 2, "AML/AMR": 2, "ST": 1}),
    ("3-4-2-1", "Two tens behind the striker", {"DC": 3, "WBL/WBR": 2, "MC": 2, "AMC": 2, "ST": 1}),
    ("4-4-2", "Vertical, counter-attacking football", {"DL/DR": 2, "DC": 2, "ML/MR": 2, "MC": 2, "ST": 2}),
    ("4-2-2-2", "High press, narrow tens", {"DL/DR": 2, "DC": 2, "DM": 2, "AMC": 2, "ST": 2}),
    ("5-2-3 Wide", "Five at the back, three forwards", {"DC": 3, "WBL/WBR": 2, "MC": 2, "AML/AMR": 2, "ST": 1}),
    ("4-3-1-2", "A narrow diamond, for teams without wingers", {"DL/DR": 2, "DC": 2, "DM": 1, "MC": 2, "AMC": 1, "ST": 2}),
    ("5-4-1", "Counter-attack for underdogs", {"DC": 3, "WBL/WBR": 2, "ML/MR": 2, "MC": 2, "ST": 1}),
    ("5-2-2-1", "A more realistic defensive option", {"DC": 3, "WBL/WBR": 2, "MC": 2, "AML/AMR": 2, "ST": 1}),
    ("4-2-4", "Ultra-attacking, strong in older versions", {"DL/DR": 2, "DC": 2, "MC": 2, "AML/AMR": 2, "ST": 2}),
    ("3-5-2", "Wing-backs and two strikers", {"DC": 3, "WBL/WBR": 2, "DM": 1, "MC": 2, "ST": 2}),
    ("5-3-2", "The defensive 3-5-2", {"DC": 3, "WBL/WBR": 2, "MC": 3, "ST": 2}),
    ("4-1-4-1", "Often just the shape without the ball", {"DL/DR": 2, "DC": 2, "DM": 1, "ML/MR": 2, "MC": 2, "ST": 1}),
    ("4-4-1-1", "Compact, with a deep-lying forward", {"DL/DR": 2, "DC": 2, "ML/MR": 2, "MC": 2, "AMC": 1, "ST": 1}),
    ("3-4-1-2", "A ten behind two strikers", {"DC": 3, "WBL/WBR": 2, "MC": 2, "AMC": 1, "ST": 2}),
    ("4-3-2-1", "The Christmas tree: narrow, no wingers", {"DL/DR": 2, "DC": 2, "MC": 3, "AMC": 2, "ST": 1}),
    ("3-4-3-0", "Strikerless: niche, but big results", {"DC": 3, "WBL/WBR": 2, "MC": 2, "AML/AMR": 2, "AMC": 1}),
]
FORMATION_ORDER = ["GK", "DC", "DL/DR", "WBL/WBR", "DM", "MC", "ML/MR", "AMC", "AML/AMR", "ST"]
# Without FM's positions, a player's best position (from attributes) and the ones next to it: so a midfielder isn't put
# at full-back just because his attributes would score there
NEAR = {"GK": ["GK"], "DC": ["DC", "DL/DR", "DM"], "DL/DR": ["DL/DR", "WBL/WBR", "DC"], "WBL/WBR": ["WBL/WBR", "DL/DR", "ML/MR"],
        "DM": ["DM", "MC", "DC"], "MC": ["MC", "DM", "AMC"], "ML/MR": ["ML/MR", "AML/AMR", "WBL/WBR", "MC"],
        "AMC": ["AMC", "MC", "AML/AMR", "ST"], "AML/AMR": ["AML/AMR", "ML/MR", "AMC", "ST", "MC"], "ST": ["ST", "AMC", "AML/AMR"]}


def best_xi(players, need, plays):
    """The strongest side for need ({position: count}): the best player-position pair first, each player once, only where
    they play. Returns ([(position, player, score)], unfilled count)."""
    left = dict(need)
    pairs = sorted(((r[pos], i, pos) for i, r in enumerate(players) for pos in need
                    if r.get(pos) is not None and plays(r, pos)), reverse=True)
    used, xi = set(), []
    for v, i, pos in pairs:
        if left[pos] and i not in used:
            used.add(i); left[pos] -= 1
            xi.append((pos, players[i], v))
    return xi, sum(left.values())


def formations(players, plays):
    """How the squad fits each of FORMATIONS: its best XI's average score, a second XI's (depth), the weakest spot."""
    out = []
    for name, note, shape in FORMATIONS:
        need = {"GK": 1, **shape}
        xi, short = best_xi(players, need, plays)
        taken = {r["name"] for _, r, _ in xi}
        second, short2 = best_xi([r for r in players if r["name"] not in taken], need, plays)
        avg = lambda side, miss: sum(v for *_, v in side) / len(side) if side else 0  # over the slots filled
        outfield = [t for t in xi if t[0] != "GK"]
        weak = min(outfield, key=lambda t: t[2]) if outfield else None
        out.append({"name": name, "note": note, "xi": avg(xi, short), "depth": avg(second, short2), "short": short,
                    "weak": {"pos": weak[0], "name": weak[1]["name"], "v": weak[2]} if weak else None,
                    "players": [{"pos": pos, "name": r["name"], "v": v}
                                for pos, r, v in sorted(xi, key=lambda t: (FORMATION_ORDER.index(t[0]), -t[2]))]})
    return sorted(out, key=lambda f: (f["short"], -f["xi"], -f["depth"]))  # shapes you can't fill go last


def grid(squad, w, cfg, intake):
    """The squad by formation slot (save.toml's `grid`): senior players by position score, then youth
    (age <= youth_age) by that position's score against their age benchmark. Players are placed best first, each at
    their best position: its first free Starter, else Backup, else Third, which takes any number (players to sell or
    loan out), shared across that position's slots. A left-footer prefers a left-footed slot. Out on loan isn't placed.
    Then empty Starter and Backup slots are filled from Third by whoever scores best at that slot's position.
    save.toml's `grid_pins` (name = slot label, or { slot = label, col = "starter" | "backup" | "third" }, either
    part optional, plus row = n for the nth slot with that label; pinning places a player out on loan too) puts a
    player in that slot instead, whatever their foot; other players steer clear of slots a pinned player is still to
    fill. Players pinned to a column go first, into that column.
    The latest youth intake (`intake`: names) gets its own columns, placed like the youth, except those in `grid_youth`."""
    slots = cfg.get("grid") or [{"label": p, "pos": p} for p in ["GK", *w["outfield"]]]
    columns = ["starter", "backup", "third"]
    pins = {n: v if isinstance(v, dict) else {"slot": v} for n, v in cfg.get("grid_pins", {}).items()}
    pin_col = {n: columns.index(v["col"]) for n, v in pins.items() if v.get("col") in columns}
    pinned_any = set(pins)  # pinned to a slot, a column, or both (a column alone: their best position)
    pin_row = {n: v["row"] for n, v in pins.items() if v.get("slot") and v.get("row")}
    pins = {n: v["slot"] for n, v in pins.items() if v.get("slot")}
    check_listed({"grid_pins": list(pins)}, "grid_pins", {r["name"] for r in squad}, "names players not in the squad")
    if bad := set(pins.values()) - {s["label"] for s in slots}:
        raise SystemExit(f"save.toml grid_pins names slots not in grid: {sorted(bad)}")
    pin_fits = {}  # name -> the slots they're pinned to (every slot with that label, or just its `row`th)
    for n, label in pins.items():
        fits = [i for i, s in enumerate(slots) if s["label"] == label]
        if n in pin_row:
            if not 1 <= pin_row[n] <= len(fits):
                raise SystemExit(f"save.toml grid_pins: {n}'s row {pin_row[n]}, but grid has {len(fits)} {label!r} slot(s)")
            fits = [fits[pin_row[n] - 1]]
        pin_fits[n] = fits
    youth_age = cfg.get("youth_age", 18)  # youth: 18 and under
    promoted = set(cfg.get("grid_senior", []))  # youngsters the user has moved up to the senior squad
    check_listed(cfg, "grid_senior", {r["name"] for r in squad}, "names players not in the squad")
    joined = set(cfg.get("grid_youth", []))  # intake players the user has kept: youth columns, not intake
    check_listed(cfg, "grid_youth", {r["name"] for r in squad}, "names players not in the squad")
    intake = intake - joined
    excluded = set(cfg.get("grid_exclude", []))  # still at the club but not in the plans (e.g. contract running out)
    check_listed(cfg, "grid_exclude", {r["name"] for r in squad}, "names players not in the squad")
    squad = [r for r in squad if r["name"] not in excluded]
    here = [r for r in squad if r["loan"] != "out" or r["name"] in pinned_any]  # out on loan: only if pinned
    # Where a player may go: FM's own positions when an export showed them; else his best position and its neighbours
    plays = lambda r, pos: pos in (r.get("plays") or NEAR.get(r["best"], [r["best"]]))

    def allocate(players, value):
        cols, spare = [[[] for _ in slots] for _ in range(3)], []  # Starter, Backup, Third: lists of players per slot
        rows = {r["name"]: r for r in players}

        def entry(r, pos):
            return {"name": r["name"], "age": r["age"], "loan": r["loan"], "v": value(r, pos), "score": r[pos], "left": r["left"] == "yes"}
        waiting = {}  # slot -> pinned players not yet placed: others steer clear of those slots at the same depth
        for r in players:
            for i in range(len(slots)):
                waiting[i] = waiting.get(i, 0) + (i in pin_fits.get(r["name"], ()))
        for r in sorted(players, key=lambda r: (r["name"] not in pin_col, -value(r, r["best"]))):
            fits = (pin_fits[r["name"]] if r["name"] in pins else
                    [i for i, s in enumerate(slots) if r.get(s["pos"]) is not None and (not s.get("left") or r["left"] == "yes")
                     and plays(r, s["pos"])])
            if not fits:
                spare.append(r)
                continue
            pos = max({slots[i]["pos"] for i in fits}, key=lambda pos: value(r, pos))  # best position in the formation
            ok = [i for i in fits if slots[i]["pos"] == pos]
            pinned = r["name"] in pins
            if pinned:
                for i in fits:
                    waiting[i] -= 1
            # left-footed slots first (only left-footers reach them), then slots no pinned player is waiting for
            pref = lambda i: (not slots[i].get("left"), 0 if pinned else waiting[i], i)
            depths = (pin_col[r["name"]],) if r["name"] in pin_col else (0, 1)
            free = [(d, *pref(i), i) for d in depths if d < 2 for i in ok if not cols[d][i]]
            d, i = (min(free)[0], min(free)[-1]) if free else (2, min(ok, key=lambda i: (len(cols[2][i]), *pref(i))))
            cols[d][i].append(entry(r, pos))
        # An empty Starter or Backup slot (a position nobody is best at, e.g. DM) is filled from Third: of the players
        # there, whoever scores best at that slot's position moves up. Best pair first, Starters before Backups; players
        # pinned to a slot or column stay put. A moved player keeps "from": the position they'd otherwise play.
        movable = lambda p: p["name"] not in pins and p["name"] not in pin_col
        for d in (0, 1):
            while True:
                pairs = [(value(rows[p["name"]], slots[i]["pos"]), i, j, k) for i in range(len(slots)) if not cols[d][i]
                         for j in range(len(slots)) for k, p in enumerate(cols[2][j]) if movable(p)
                         and rows[p["name"]].get(slots[i]["pos"]) is not None and (not slots[i].get("left") or p["left"])
                         and plays(rows[p["name"]], slots[i]["pos"])]
                if not pairs:
                    break
                _, i, j, k = max(pairs, key=lambda t: (t[0], -t[1]))
                p = cols[2][j].pop(k)
                cols[d][i].append(entry(rows[p["name"]], slots[i]["pos"]) | {"from": slots[j]["pos"]})
        return cols, spare

    is_senior = lambda r: r["age"] > youth_age or r["name"] in promoted
    senior, spare = allocate([r for r in here if is_senior(r)], lambda r, pos: r[pos])
    vs_age = lambda r, pos: r[pos] * r["vsAge"] / r["score"]  # score ÷ age benchmark

    def place_young(players):
        """Youngsters in one list per slot: each at their best position in the formation for their age (pinned: their
        slot), shared out over that position's slots, a left-footer preferring a left-footed one; best for age first."""
        lists, spare_young = [[] for _ in slots], []
        for r in sorted(players, key=lambda r: -r["vsAge"]):
            fits = (pin_fits[r["name"]] if r["name"] in pins else
                    [i for i, s in enumerate(slots) if r.get(s["pos"]) is not None and (not s.get("left") or r["left"] == "yes")
                     and plays(r, s["pos"])])
            if not fits:
                spare_young.append(r)
                continue
            pos = max({slots[i]["pos"] for i in fits}, key=lambda pos: vs_age(r, pos))
            i = min((i for i in fits if slots[i]["pos"] == pos), key=lambda i: (not slots[i].get("left"), len(lists[i]), i))
            lists[i].append({"name": r["name"], "age": r["age"], "loan": r["loan"], "v": vs_age(r, pos), "score": r[pos],
                             "left": r["left"] == "yes"})
        return [sorted(l, key=lambda p: -p["v"]) for l in lists], spare_young

    young, spare_youth = place_young([r for r in here if not is_senior(r) and r["name"] not in intake])
    new, spare_new = place_young([r for r in here if not is_senior(r) and r["name"] in intake])
    # Beside Starter / Backup / Third: Youth (youth_age and under), and the latest intake while there is one
    extra = {"youth": young, "intake": new}
    extra_cols = [{"key": k, "label": label, "title": title} for k, label, title in (
        ("youth", "Youth", f"players {youth_age} and under, best for their age first"),
        ("intake", "Intake", "the latest youth intake, best for their age first")) if k != "intake" or any(extra[k])]
    brief = lambda r, v: {"name": r["name"], "age": r["age"], "pos": r["best"], "v": v}
    return {"youthAge": youth_age, "extraCols": extra_cols,
            "slots": [{"label": s["label"], "pos": s["pos"], "left": bool(s.get("left")),
                       "senior": [c[i] for c in senior], "extra": {k: extra[k][i] for k in extra}} for i, s in enumerate(slots)],
            "spare": [brief(r, r["score"]) for r in sorted(spare, key=lambda r: -r["score"])],
            "spareYouth": [brief(r, r["score"]) for r in sorted(spare_youth + spare_new, key=lambda r: -r["vsAge"])],
            "loanOut": [brief(r, r["score"]) for r in squad if r["loan"] == "out" and r["name"] not in pinned_any],
            "formations": formations([r for r in here if r["loan"] != "out"], plays)}


# --- staff ---

def load_staff(staff_dir, words):
    """Every staff observation row, grouped by name and then game date; FM's words become numbers."""
    staff = {}
    for path, row in read_rows(staff_dir):
        p = staff.setdefault(row["Name"], {"files": set(), "dates": {}})
        p["files"].add(path.name)
        snap = p["dates"].setdefault(path.stem[:10], {"attrs": {}})
        snap.update(club=row["Club"], age=int(row["Age"]) if row.get("Age") else None,
                    job=row.get("Job") or None, personality=row.get("Personality") or None)
        for k, v in row.items():
            if k not in STAFF_META and v:  # blank = unreadable
                if v not in words:
                    raise SystemExit(f"{path.name}: {row['Name']}: {k} = {v!r} isn't in the weights file's [staff_words]")
                snap["attrs"][k] = words[v]
    return staff


def staff_row(name, p, roles):
    """A staff member's latest state and role scores, plus their history by observation date."""
    history, known = [], {"job": None, "personality": None}
    for date, snap, attrs in timeline(p["dates"]):
        known.update({k: snap[k] for k in known if snap[k]})
        scores = weighted(attrs, roles)
        best = max(scores, key=scores.get, default=None)
        history.append({"date": date, "club": snap["club"], "age": snap["age"], **scores,
                        "best": best, "score": scores.get(best), "attrs": attrs})
    latest = history[-1]
    return {"name": name, **{k: v for k, v in latest.items() if k not in ("date", "attrs")}, **known, "history": history}


def staff_cols(roles):
    return [{"key": "name", "label": "Name"}, {"key": "club", "label": "Club"}, {"key": "age", "label": "Age", "num": True},
            {"key": "job", "label": "Job"}, {"key": "personality", "label": "Personality"},
            *({"key": r, "label": r, "num": True, "fmt": "f1"} for r in roles)]


def staff_filters(all_staff, roles, words, names):
    """Per role: the best staff imported for it (top 3, any club) and suggested minimums for FM's staff search, as FM's
    words, for the same attributes as the players' filters page (search_attrs)."""
    def word(v):  # the word whose value is nearest
        return min(words, key=lambda w: abs(words[w] - v))
    out = []
    for role, rw in roles.items():
        ranked = sorted((r for r in all_staff if r.get(role) is not None), key=lambda r: -r[role])[:3]
        if not ranked:
            continue
        rows, searched = [], search_attrs({a: x for a, x in rw.items() if x > 0})
        for attr, weight in sorted(rw.items(), key=lambda kv: -kv[1]):
            values = [r["history"][-1]["attrs"].get(attr) for r in ranked]
            known = [v for v in values if v is not None]
            rows.append({"attr": attr, "weight": weight, "values": values,
                         "labels": [word(v) if v is not None else "–" for v in values],
                         "min": word(sum(known) / len(known)) if attr in searched and known else None})
        out.append({"pos": role, "title": names.get(role) if names.get(role) != role else None, "rows": rows,
                    "players": [{"name": r["name"], "age": r["age"], "score": r[role], "club": r["club"]} for r in ranked]})
    return out


# --- pages ---

def check_listed(cfg, key, known, what):
    if missing := set(cfg.get(key, [])) - known:
        raise SystemExit(f"save.toml {key} {what}: {sorted(missing)}")


def analysis(squad, others, lineup, w):
    """The analysis page: your squad position by position, as the Lineup places them (so DM, MC… have players too),
    with each player's current attributes, ranked in the page by an attribute you pick. Each position also gets the
    average of every attribute over the imported players whose best position it is, to compare with."""
    positions = ["GK", *w["outfield"]]
    rows = {r["name"]: r for r in squad}
    roles = ["Starter", "Backup", "Third"]
    attrs_of = lambda r: r["history"][-1]["attrs"]  # the latest known value of each attribute
    out = []
    for pos in positions:
        seen, players = set(), []
        for s in lineup["slots"]:
            if s["pos"] != pos:
                continue
            # Starter / Backup / Third, then the Youth and intake columns
            columns = [*((c, roles[ci]) for ci, c in enumerate(s["senior"])),
                       *((s["extra"][c["key"]], c["label"]) for c in lineup["extraCols"])]
            for col, label in columns:
                for p in col:
                    if p["name"] in seen:
                        continue
                    seen.add(p["name"])
                    r = rows[p["name"]]
                    players.append({"name": r["name"], "age": r["age"], "role": label, "from": p.get("from"),
                                    "score": r.get(pos), "attrs": attrs_of(r)})
        pool = [r for r in others if r["best"] == pos]
        sums = {}
        for r in pool:
            for a, v in attrs_of(r).items():
                n, t = sums.get(a, (0, 0))
                sums[a] = (n + 1, t + v)
        out.append({"pos": pos, "players": players, "imported": len(pool),
                    "importedAvg": {a: t / n for a, (n, t) in sums.items()},
                    "importedScore": sum(r[pos] for r in pool) / len(pool) if pool else None})
    # The attributes to rank by: the most weighted (summed over the positions) first
    weight = {}
    for pw in [w["gk"], *w["outfield"].values()]:
        for a, v in pw.items():
            weight[a] = weight.get(a, 0) + v
    known = {a for r in squad for a in attrs_of(r)}
    attrs = sorted(known, key=lambda a: (-weight.get(a, 0), a))
    return {"positions": out, "attrs": attrs, "players": rows}  # players: for the history overlay


def attributes_page(cfg, w, rows, save_dir):
    """The attributes page: the weights the save scores with, to change in the page (saved by the app), plus every
    attribute that could be given a weight: those in any weights set, and any seen in the save's players."""
    outfield, gk = set(), set()
    for other in (w, load_toml(weights_file(DEFAULT))):
        gk.update(other.get("gk", {}))
        for t in other.get("outfield", {}).values():
            outfield.update(t)
    for r in rows:
        (gk if r.get("DC") is None and r.get("GK") is not None else outfield).update(r["history"][-1]["attrs"])
    own = save_dir / "weights.toml"
    return {"custom": cfg.get("weights") == CUSTOM, "hasCustom": own.is_file(),
            "saved": time.strftime("%Y-%m-%d", time.localtime(own.stat().st_mtime)) if own.is_file() else None,
            "positions": {"GK": w["gk"], **w["outfield"]},
            "defaults": (lambda dw: {"GK": dw["gk"], **dw["outfield"]})(load_toml(weights_file(DEFAULT))),  # Reset to default
            "attrs": {"outfield": sorted(outfield), "gk": sorted(gk)}}


def development(rows, players, obs_files, managed, squad):
    """The development page: the managed club's squad snapshots (each squad export, by date and time), and for everyone in
    any of them their score, best position, vs Age and attributes at each, for the page to compare two snapshots."""
    squad_files = {name: f["date"] for name, f in obs_files.items() if not f["source"].startswith("search-")
                   and not PARTIAL_SQUAD.search(f["source"]) and managed in f["clubs"]}
    snaps = sorted(set(squad_files.values()))
    out = []
    for r in rows:
        in_snaps = {squad_files[f] for f in players[r["name"]]["files"] if f in squad_files}
        if not in_snaps:
            continue
        at = {h["date"]: {"score": h["score"], "best": h["best"], "vsAge": h["vsAge"], "age": h["age"], "attrs": h["attrs"]}
              for h in r["history"] if h["date"] in in_snaps}
        out.append({"name": r["name"], "age": r["age"], "best": r["best"], "here": r["club"] == managed, "at": at})
    return {"snapshots": snaps, "players": out, "rows": {r["name"]: r for r in squad}}  # rows: for the history overlay


def settings_tile(config, cfg, save):
    """The guide's last tile: this save's main settings, and where to change them."""
    rows = [("App version", f"FMAnalyst {VERSION}"), ("Save", save), ("Club", cfg.get("managed_club", "–")), ("Weights", "your own (Attributes page)" if cfg.get("weights") == CUSTOM else "default"),
            ("Currency", config.get("currency", "–")), ("Exports folder", config.get("exports_dir") or "found under Documents"),
            ("Data folder", str(ROOT))]
    table = "".join(f"<tr><td class=\"text\">{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
    table += (f'<tr><td class="text">Project page</td><td><a href="{PROJECT_URL}" target="_blank" rel="noopener">'
              f'{PROJECT_URL.removeprefix("https://")}</a> (updates, README, report a problem)</td></tr>')
    return {"key": "", "title": "About and your settings",
            "text": f"<table>{table}</table>Switch or start saves with <b>Saves</b> at the bottom right. Everything else is in "
                    f"<b>config.toml</b> (this computer) and <b>saves\\{html.escape(save)}\\save.toml</b> (this save) in the data "
                    f"folder: edit them with Notepad, then restart the app."}


def build(save, config):
    """Every page's (columns, data)."""
    save_dir = ROOT / "saves" / save
    if not (save_dir / "save.toml").exists():
        raise SystemExit(f"No saves/{save}/save.toml: run `python3 score.py init` to set up a save")
    cfg = load_toml(save_dir / "save.toml")
    w = load_weights(cfg, save_dir)
    managed = cfg["managed_club"]

    players, obs_files = load_players(save_dir / "obs", cfg.get("aliases"))
    rows = [r for n, p in players.items() if (r := player_row(n, p, w))]
    if unscored := sorted(set(players) - {r["name"] for r in rows}):
        print(f"[{save}] left out, not enough attributes to score with {cfg['weights']}.toml: {', '.join(unscored)}")
    check_listed(cfg, "last_names", players.keys(), "has players never observed")
    check_listed(cfg, "last_batch", set(obs_files), "names files with no players")
    last_batch, last_names = set(cfg.get("last_batch", [])), set(cfg.get("last_names", []))
    # a screenshot of just names (e.g. a shortlist) picks players already observed; otherwise the batch's files
    last_rows = [r for r in rows if r["name"] in last_names] if last_names else \
        [r for r in rows if players[r["name"]]["files"] & last_batch]

    # Anyone at the managed club but missing from its latest squad screen has left (sold, released).
    # A youth or reserve squad screen (`my-club-u18s`) shows only part of the club, so it doesn't count.
    squad_date = max((f["date"] for f in obs_files.values() if not f["source"].startswith("search-")
                      and not PARTIAL_SQUAD.search(f["source"]) and managed in f["clubs"]), default="")
    # ...as has anyone in save.toml's `released` (sold or released since the latest squad screen)
    check_listed(cfg, "released", players.keys(), "has players never observed")
    for r in rows:
        if r["club"] == managed and (r["history"][-1]["date"] < squad_date or r["name"] in cfg.get("released", [])):
            r["club"] = f"left {managed}"
    # ...and anyone in `signed` (signed since the latest squad screen) has joined, until a newer squad screen leaves them out
    check_listed(cfg, "signed", players.keys(), "has players never observed")
    for r in rows:
        if r["name"] in cfg.get("signed", []) and r["history"][-1]["date"] >= squad_date:
            r["club"] = managed
    # ...and anyone in `recalled` is back from loan (clears the "out" flag until the next squad screen)
    check_listed(cfg, "recalled", players.keys(), "has players never observed")
    for r in rows:
        if r["name"] in cfg.get("recalled", []) and r["loan"] == "out":
            r["loan"] = None
    squad = [r for r in rows if r["club"] == managed]
    # The latest youth intake: players in the newest obs file(s) whose source mentions "intake", until a squad screen
    # shows them (signed: they join the youth; let go: they've left)
    intake_date = max((f["date"] for f in obs_files.values() if "intake" in f["source"]), default="")
    intake = {n for n, p in players.items()
              if any(obs_files[f]["date"] == intake_date and "intake" in obs_files[f]["source"] for f in p["files"])
              and not any(obs_files[f]["date"] >= intake_date and not obs_files[f]["source"].startswith("search-") for f in p["files"])}

    roles = w.get("staff", {})
    staff = load_staff(save_dir / "staff", w.get("staff_words", {}))
    staff_rows = [staff_row(n, p, roles) for n, p in staff.items()]
    check_listed(cfg, "last_staff_batch", {f for p in staff.values() for f in p["files"]}, "names files with no staff")
    check_listed(cfg, "last_staff_names", staff.keys(), "has staff never observed")
    last_staff, last_staff_names = set(cfg.get("last_staff_batch", [])), set(cfg.get("last_staff_names", []))

    lineup = grid(squad, w, cfg, intake)
    return cfg, {
        "squad": (player_cols(w, False), squad, {"lineup": lineup}),
        "development": (player_cols(w, False), development(rows, players, obs_files, managed, squad)),
        "analysis": (player_cols(w, False), analysis(squad, [r for r in rows if r["club"] != managed], lineup, w)),
        "elsewhere": (player_cols(w, True), [r for r in rows if r["club"] != managed]),
        "last": (player_cols(w, True), last_rows),
        "filters": ([], filters(squad, w)),
        "charts": ([], charts(rows, squad, w, cfg, {r["name"] for r in last_rows})),
        "staff": (staff_cols(roles), staff_rows),
        # the batch's files, plus anyone named in last_staff_names
        "last_staff": (staff_cols(roles), [r for r in staff_rows
                                           if staff[r["name"]]["files"] & last_staff or r["name"] in last_staff_names]),
        "staff_filters": ([], staff_filters(staff_rows, roles, w.get("staff_words", {}), w.get("staff_names", {}))),
        "attributes": ([], attributes_page(cfg, w, rows, save_dir)),
        "guide": ([], GUIDE + [settings_tile(config, cfg, save)]),
    }


# The app's name and icon, at the left of every page's menu
BRAND = ('<span class="brand">' + re.sub(r"<!--.*?-->\s*", "", (APP / "logo.svg").read_text(encoding="utf-8"), flags=re.S)
         + '<span><b>FM</b>Analyst</span></span>'
         # and a faint copy in the bottom-right corner of the page (theme.css's .watermark)
         + '<i class="watermark" style="background-image:url(data:image/svg+xml;base64,'
         + base64.b64encode((APP / "logo.svg").read_bytes()).decode() + ')"></i>')


def write_pages(save, config, cfg, pages):
    out = ROOT / "out" / save
    out.mkdir(parents=True, exist_ok=True)
    home = html.escape(cfg["short_name"])
    labels = {p: PAGE_LABELS.get(p, p.replace("_", " ")) for p in PAGES}
    keys = {p: PAGE_KEYS.get(p) or str(i % 10) for i, p in enumerate(PAGES, 1)}  # the tenth page is key 0
    dates = [f.name[:10] for d in ("obs", "staff") for f in (ROOT / "saves" / save / d).glob("*.csv")]
    site = json.dumps({"pages": {keys[p]: p for p in PAGES}, "currency": config.get("currency", "£"),
                       "gameDate": max(dates, default=None)})  # the latest game date: the history overlay's "years ago"
    theme = (APP / "theme.css").read_text(encoding="utf-8")
    w = load_weights(cfg, ROOT / "saves" / save)
    weights = json.dumps({"GK": w["gk"], **w["outfield"], **w.get("staff", {})}, ensure_ascii=False)  # for the history overlay
    for page, (columns, data, *extra) in pages.items():
        extra = extra[0] if extra else {}
        nav = BRAND + "".join(('<span class="sep"></span>' if p in NAV_GROUP_STARTS else "") +
                      f'<a href="{p}.html"{" class=\"on\"" if p == page else ""}><b>{keys[p]}</b> {labels[p]}</a>' for p in PAGES)
        data = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")  # a "</script>" in a name can't end the script
        template = (APP / ("charts.html" if page == "charts" else "template.html")).read_text(encoding="utf-8")
        page_html = (template.replace("__THEME__", theme).replace("__NAV__", nav)
                     .replace("__TITLE__", f"{home} · {labels[page]}").replace("__SAVE__", save)
                     .replace("__HOME__", home).replace("__PAGE__", page).replace("__SITE__", site)
                     .replace("__COLS__", json.dumps(columns)).replace("__LINEUP__", json.dumps(extra.get("lineup"), ensure_ascii=False).replace("</", "<\\/")).replace("__WEIGHTS__", weights).replace("__DATA__", data))
        (out / f"{page}.html").write_text(page_html, encoding="utf-8")
    for old in out.glob("*.html"):  # pages an older version wrote (settings.html)
        if old.stem not in pages:
            old.unlink()
    print(f"[{save}] out/{save}/: " + ", ".join(f"{p} ({len(spec[1])})" for p, spec in pages.items()
                                                 if p not in ("analysis", "development", "filters", "charts", "staff_filters", "attributes", "guide")))
    return out


# The guide page (key g): how to use the app and each page. Simple HTML; shown as tiles by template.html's renderGuide.
# A tile with a key links to that page. The last tile, "Your settings", is added by settings_tile().
GUIDE = [
    {"key": "", "title": "Getting started",
     "text": "<ol><li>In FM, open your <b>squad</b> screen with the <b>attribs_outfield</b> view (see the README for the views).</li>"
             "<li>Point the mouse at the list and press <b>F8</b>: FM27 Exporter saves the list as a file.</li>"
             "<li>Here, click <b>Import file…</b> (bottom right, or <b>File → Import a file…</b>) and pick that file.</li></ol>"
             "The app works out what it is (your squad, a search, staff), scores everyone and opens the right page. "
             "Import your squad first; after that, import searches and shortlists whenever you're looking for players."},
    {"key": "", "title": "Importing: what goes where",
     "text": "<ul><li><b>Your squad</b> (the file name starts with First Team or Squad): your players, on <b>Squad</b>, "
             "<b>Analysis</b> and <b>Development</b>. Every squad import is kept as a dated snapshot, so export it again every "
             "half-season to track progress. Players missing from a newer squad export count as gone.</li>"
             "<li><b>A player search or shortlist</b>: a list of its own, on <b>Last player import</b>, and added to "
             "<b>All players</b>. Re-importing the same file updates that list.</li>"
             "<li><b>A staff search</b> (the <b>staff_everything</b> view) or your club's staff screen: on <b>Last staff "
             "import</b> and <b>All staff</b>.</li>"
             "<li><b>A whole division</b> with Division, Minutes and Average Rating columns: adds the rating charts to "
             "<b>Market</b> (rename the file after the league first, e.g. league-one.csv).</li></ul>"
             "After each import a message in the corner says how many were imported, and who was skipped (not enough "
             "attributes known). <b>Ctrl + F8</b> in FM adds every attribute column to a view."},
    {"key": "", "title": "What the numbers mean",
     "text": "<ul><li><b>Position scores</b> (GK, DC, DL/DR, WBL/WBR, DM, MC, ML/MR, AMC, AML/AMR, ST): how well a "
             "player's attributes suit that position, on FM's 1–20 scale. Each attribute counts by its weight there (see "
             "<b>Attributes</b>); pace, acceleration and stamina count most, because they win the most matches.</li>"
             "<li><b>Best</b>: the position he scores highest at; <b>Score</b>: that score. With FM's <b>Position</b> column "
             "in your exports, only positions FM says he plays count, so a striker stays a striker.</li>"
             "<li><b>vs Age</b>: his score against what's normal for his age. 100% = on track to be a good Championship or "
             "low Premier League player at his peak; more is ahead of that, less behind.</li>"
             "<li><b>Foot</b>: Left if his left foot is Fairly Strong or better, else Right.</li>"
             "<li><b>Colours</b>: attributes like FM (grey 1–5, white 6–9, yellow 10–14, green 15+); scores and vs Age from "
             "red (weak) through white to green (very good).</li>"
             "<li><b>loan in</b> / <b>loan out</b> / <b>trial</b> next to a name; <b>↑DC</b> = moved up from Third to fill "
             "an empty slot (his best position was DC).</li>"
             "<li>A player whose scouted attributes are mostly unknown (FM shows -) isn't scored until you know more.</li></ul>"},
    {"key": "", "title": "A player's profile",
     "text": "<b>Click a name</b> on any page (or double-click a row, or press <b>Enter</b>) to open it; <b>Esc</b> closes it.<br>"
             "<b>Top table:</b> one row per snapshot, oldest first: club, age, every position score, Best, Score and vs Age, "
             "each with its change since the row before (<span class='t4'>+1.2</span>, <span class='t0'>−0.3</span>).<br>"
             "<b>Attributes:</b> every attribute at each snapshot, oldest to newest, with its change (+1, −1), the weight it "
             "has at his best position, and <b>growth</b>: the total change since the first snapshot. The most important "
             "attributes for his position come first."},
    {"key": "1", "title": "Squad",
     "text": "<b>Lineup</b> (top left): your squad by position. <b>Starter</b> and <b>Backup</b>; <b>Third</b> = surplus, "
             "players to sell or loan out; <b>Youth</b> = 18 and under, best for their age first. Each player goes to his best "
             "position; an empty slot (often DM, MC or AMC) is filled from Third by whoever plays it best (marked <b>↑</b>).<br>"
             "<b>Formations</b> (top right): 20 common systems ranked by how well your squad fits them. <b>XI</b> = the average "
             "score of your best eleven for that shape, <b>Depth</b> = a second eleven's, <b>Weakest spot</b> = where that "
             "eleven is thinnest. Click a formation to see its eleven. <b>Hide</b> folds both boxes away.<br>"
             "<b>Table</b> (below): every player at your club with all his scores. Sort by clicking a heading, filter with the "
             "Search box (see <b>Searching and filtering</b>).<br>"
             "<b>Use it to:</b> spot thin positions (no backup), surplus (a long Third), who's next in line, and the shape that suits you."},
    {"key": "2", "title": "Analysis",
     "text": "<b>Shows:</b> your team position by position, as the Lineup places them, ranked by what you pick in "
             "<b>Rank by</b>: <b>Speed</b> (Pace and Acceleration together), the <b>position score</b>, or any single attribute.<br>"
             "<b>Positions ranked:</b> your positions from strongest to weakest: your average, your best player there, the "
             "average of the imported players whose best position it is (<b>Imported</b>) and the difference (<b>±</b>; "
             "green = you're ahead).<br>"
             "<b>Below:</b> each position's players, best first, with their role (Starter, Backup, Third, Youth).<br>"
             "<b>Use it to:</b> see where your team is quick or slow, strong or weak, and where to strengthen."},
    {"key": "3", "title": "Development",
     "text": "<b>Shows:</b> how your players changed between two squad snapshots: average score change, how many improved or "
             "declined, who joined or left, which attributes rose or fell most across the squad, the biggest risers and "
             "fallers, and every player's change with the attributes that went up and down.<br>"
             "<b>How:</b> pick the snapshots in <b>from</b> and <b>to</b> (the latest two by default). It needs at least two "
             "squad imports: export your squad every half-season or so.<br>"
             "<b>Use it to:</b> see whether training and game time are working, and who's stalling."},
    {"key": "4", "title": "Market",
     "text": "<b>Shows:</b> your squad (orange) against players you've imported (purple), position by position.<br>"
             "<b>Controls:</b> <b>compare with</b> = whom to compare with: your <b>Last player import</b>, <b>All imported "
             "players</b>, or a division you've imported with ratings. <b>position</b> picks the position for the lower "
             "charts; <b>attribute</b> lists that position's most important attributes. Hover a dot to see who it is.<br>"
             "<b>Charts:</b> <b>Role score by position</b> and <b>Attribute by position</b> (the white line is the middle of "
             "the purple players: your dots right of it are better than most); <b>Score vs wage</b> (dots above the dashed "
             "line are better than their wage suggests: good value); with a division import, <b>Score vs average rating</b> "
             "(does a higher score mean better matches in your league?)."},
    {"key": "5", "title": "All players",
     "text": "<b>Shows:</b> everyone you've imported who isn't at your club, scored the same way as your squad, with club, "
             "wage and value (the top of FM's range).<br>"
             "<b>Use it to:</b> compare transfer targets with your own players, and follow players over the seasons.<br>"
             "<b>Clear old lists</b> (bottom right, click twice) moves old searches to an archive folder so the list stays "
             "short. Nothing is deleted: your squad, the latest list and anyone named in save.toml stay."},
    {"key": "6", "title": "Last player import",
     "text": "<b>Shows:</b> only the players from the file you imported last (a search or shortlist).<br>"
             "<b>Use it to:</b> judge one search at a time. Sort by <b>Score</b> or <b>vs Age</b>, filter, e.g. "
             "<b>best=ST age&lt;=23</b>, and compare with your starters on <b>Squad</b>: anyone clearly higher at a weak "
             "position is worth scouting."},
    {"key": "7", "title": "Player search",
     "text": "<b>Shows:</b> for each position, your best players there (3, or 1 for GK) and the attributes that matter most, "
             "most important first, with a suggested minimum (<b>Search ≥</b>) = the average of your best players.<br>"
             "<b>Use it to:</b> set up FM's player search to find players better than yours.<br>"
             "<b>How:</b> in FM's search add <b>Position Is Accomplished At</b> that position, then each highlighted attribute "
             "<b>Is At Least</b> its Search ≥ value. Dimmed attributes don't need a minimum. Too few results? Lower the "
             "smaller minimums first. The minimums rise as your squad improves."},
    {"key": "8", "title": "All staff",
     "text": "<b>Shows:</b> every staff member you've imported, scored for each job (FM's words like Good = 13, Very Good = 16): "
             "<b>Mgr</b> manager, <b>AM</b> assistant manager, <b>HoYD</b> head of youth development, coaches "
             "(<b>AttTct</b>/<b>AttTec</b> attacking tactical/technical, <b>DefTct</b>/<b>DefTec</b> defending, "
             "<b>PosTct</b>/<b>PosTec</b> possession, <b>GKC</b> goalkeeping, <b>FitC</b> fitness, <b>SetC</b> set pieces), "
             "<b>DoF</b> director of football, <b>Scout</b>, <b>PA</b> performance analyst, <b>Physio</b>, <b>SpSci</b> sports scientist.<br>"
             "<b>How:</b> read across a row: the highest number is the job he suits best. Sort by a job to find the best for it. "
             "<b>Clear old lists</b> works here too."},
    {"key": "9", "title": "Last staff import",
     "text": "Only the staff from your latest staff import: judge one staff search at a time, the way Last player import works."},
    {"key": "0", "title": "Staff search",
     "text": "<b>Shows:</b> per job, the best staff you've imported (from any club) and the attributes that matter most for "
             "that job, with a suggested minimum as FM's word (e.g. Good).<br>"
             "<b>Use it to:</b> set up FM's staff search when hiring: set each highlighted attribute to at least its "
             "<b>Search ≥</b> word. The more staff searches you import, the better the minimums get."},
    {"key": "a", "title": "Attributes",
     "text": "<b>Shows:</b> the weight of every attribute at each position: how much it counts in that position's score "
             "(Share = its part of the total).<br>"
             "<b>Change them:</b> pick a position, type new numbers (blank or 0 = not used), then <b>Save</b>: they become this "
             "save's own weights and every page is rescored. <b>Undo changes</b> drops what you haven't saved; "
             "<b>Reset to default</b> puts the default weights back in the page (then Save); <b>Back to default</b> scores "
             "with the defaults straight away (your own stay saved, <b>Use my weights</b> brings them back).<br>"
             "<b>Share them:</b> <b>Export…</b> writes a table (.txt for Notepad, .csv for Excel); <b>Import…</b> reads one "
             "back into the page, then Save. Search exports only have 18 attributes: weight others and searched players may "
             "not get a score."},
    {"key": "", "title": "Searching and filtering",
     "text": "Type in the <b>Search</b> box at the top of a table (<b>/</b> or <b>f</b> jumps there, <b>Esc</b> clears it):"
             "<ul><li>a name or club: <b>kalu</b>, <b>levski</b></li>"
             "<li>a condition on any column: <b>age&lt;=21</b>, <b>best=DC</b>, <b>best!=GK</b>, <b>vs&gt;=90</b>, "
             "<b>score&gt;=12</b>, <b>foot=left</b>, <b>club:levski</b> (contains), <b>wage&lt;=10k</b>, <b>value&lt;=500k</b></li>"
             "<li>loans: <b>loan-in</b>, <b>loan-out</b>, <b>trial</b>; staff: <b>job:coach</b>, <b>PosTct&gt;=12</b></li>"
             "<li>several at once, with spaces: <b>age&lt;=23 best=ST vs&gt;=90</b></li></ul>"
             "The count next to the box shows how many match. Click a column heading to sort (again to reverse)."},
    {"key": "", "title": "Saves, folders and mistakes",
     "text": "<ul><li><b>Saves</b> (bottom right): switch between FM saves, or start one for a new game or club.</li>"
             "<li>Your data is in <b>Documents\\FMAnalyst</b>: settings (config.toml), each save's data and pages. Updating the "
             "app keeps it all.</li>"
             "<li><b>Imported the wrong file?</b> Close the app, delete its files from <b>saves\\&lt;your save&gt;\\obs</b> (their "
             "names start with the date), and start the app again.</li>"
             "<li><b>Something went wrong?</b> <b>app.log</b> in the data folder says what happened.</li>"
             "<li>Your own formation, pinned players and other details live in your save's <b>save.toml</b> (see the README).</li></ul>"},
    {"key": "", "title": "Tips for the best results",
     "text": "<ul><li><b>Add FM's Position column</b> to your squad and search views: Best, the Lineup and Formations then use "
             "the positions FM says each player plays, instead of guessing from attributes.</li>"
             "<li><b>Export your squad every half-season</b>: that's what fills <b>Development</b> and each player's profile.</li>"
             "<li><b>Scout before you judge</b>: scouted ranges (e.g. 10–14) count as their middle, so a fully scouted player's "
             "score is more reliable.</li>"
             "<li>Use the <b>player_search_outfield</b> view for searches, so everyone gets a score at every position.</li></ul>"},
    {"key": "", "title": "Keyboard shortcuts",
     "text": "<ul><li><b>1</b>–<b>9</b>, <b>0</b>, <b>a</b>, <b>g</b>: go to a page (the numbers in the menu).</li>"
             "<li><b>Arrow keys</b>: move around a table; <b>Enter</b>: the player's profile; <b>s</b>: sort by the current column.</li>"
             "<li><b>/</b> or <b>f</b>: the Search box; <b>Esc</b>: clear it, or close a profile.</li>"
             "<li><b>c</b>: copy the player's name, to paste into FM's search.</li>"
             "<li><b>?</b>: the list of every shortcut.</li></ul>"},
]


# --- commands ---

def load_config():
    if not CONFIG.exists():
        raise SystemExit("No config.toml yet: run `python3 score.py init` to set one up")
    return load_toml(CONFIG)


def default_screenshots_dir():
    """Where the OS's own screenshot shortcut saves, by default."""
    if platform.system() == "Darwin":
        return "~/Desktop"  # macOS, unless changed in the Screenshot app's Options
    return "~/Pictures/Screenshots"  # Windows (Win+PrtScn) and GNOME


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)  # a JSON string is a valid TOML basic string


def new_save(save, club, division="", weight=None):
    """Create saves/<save>/ (save.toml, obs/, staff/). weight defaults to the newest weights file."""
    weight = weight or DEFAULT
    save_toml = ROOT / "saves" / save / "save.toml"
    (save_toml.parent / "obs").mkdir(parents=True, exist_ok=True)
    (save_toml.parent / "staff").mkdir(exist_ok=True)
    save_toml.write_text(f"# A save managing {club}. The Guide's \"Your settings\" tile shows them.\n"
                         f"weights = {toml_str(weight)}\nmanaged_club = {toml_str(club)}\n"
                         f"short_name = {toml_str(save)}\ndivision = {toml_str(division)}\n"
                         "last_batch = []\nlast_staff_batch = []\n", encoding="utf-8")
    return save_toml


def set_active_save(save):
    """Point config.toml's active_save at a save, keeping the rest of the file as it is."""
    text = CONFIG.read_text(encoding="utf-8")
    line = f"active_save = {toml_str(save)}"
    text = re.sub(r"^active_save\s*=.*$", line, text, flags=re.M) if re.search(r"^active_save\s*=", text, re.M) \
        else line + "\n" + text
    CONFIG.write_text(text, encoding="utf-8")


def write_config(save, currency, screenshots_dir=None, exports_dir=None):
    """Write config.toml: this computer's settings."""
    ROOT.mkdir(parents=True, exist_ok=True)
    lines = ["# Settings for this computer. The Guide's \"Your settings\" tile shows them.",
             f"active_save = {toml_str(save)}",
             f"screenshots_dir = {toml_str(screenshots_dir or default_screenshots_dir())}",
             f"currency = {toml_str(currency)}"]
    if exports_dir:
        lines.append(f"exports_dir = {toml_str(str(exports_dir))}  # where FM27 Exporter saves its files")
    CONFIG.write_text("\n".join(lines) + "\n", encoding="utf-8")


def init():
    """Ask for the settings a new user needs, and write config.toml and saves/<save>/save.toml."""
    old = load_toml(CONFIG) if CONFIG.exists() else {}

    def ask(prompt, default=""):
        answer = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
        return answer or default

    print("Set up config.toml and a save. Press enter to keep the [default].\n")
    shots = ask("Folder your screenshots are saved to", old.get("screenshots_dir", default_screenshots_dir()))
    currency = ask("Currency symbol FM shows", old.get("currency", "£"))
    club = ask("Club you manage, exactly as FM shows it")
    while not club:
        club = ask("Club you manage (needed)")
    save = ask("Name for this save (a folder name)", re.sub(r"[^a-z0-9]+", "-", club.lower()).strip("-"))
    division = ask("Your club's division, exactly as FM shows it (optional)")
    weight = DEFAULT  # changed later, if wanted, on the Attributes page

    if (ROOT / "saves" / save / "save.toml").exists():
        print(f"\nsaves/{save}/save.toml already exists: left as it is")
    else:
        new_save(save, club, division, weight)
        print(f"\nWrote saves/{save}/save.toml")
    write_config(save, currency, shots)
    print("Wrote config.toml. Next: screenshot your squad screen in FM, then ask your agent to add the screenshots.")


def screenshots(minutes):
    """The newest images in screenshots_dir, newest first, so an agent can find the ones just taken on any OS."""
    folder = Path(load_config().get("screenshots_dir", default_screenshots_dir())).expanduser()
    if not folder.is_dir():
        raise SystemExit(f"Screenshots folder {folder} doesn't exist: set screenshots_dir in config.toml")
    images = sorted((p for p in folder.iterdir() if p.suffix.lower() in IMAGE_TYPES),
                    key=lambda p: p.stat().st_mtime, reverse=True)
    now = time.time()
    recent = [p for p in images if now - p.stat().st_mtime <= minutes * 60]
    for p in recent:
        print(f"{int(now - p.stat().st_mtime):>6}s ago  {p}")
    if not recent:
        newest = f"; the newest is {images[0].name}, {int((now - images[0].stat().st_mtime) / 60)} min ago" if images else ""
        print(f"No images in {folder} from the last {minutes:g} min{newest}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--save", help="score this save instead of config.toml's active_save")
    ap.add_argument("--open", nargs="?", const="last", choices=PAGES, metavar="PAGE",
                    help=f"open a page afterwards (default: last): {', '.join(PAGES)}")
    sub = ap.add_subparsers(dest="command")
    sub.add_parser("init", help="first-time setup: config.toml and a save")
    sp = sub.add_parser("screenshots", help="list the newest screenshots in screenshots_dir")
    sp.add_argument("--minutes", type=float, default=10, help="how far back to look (default 10)")
    args = ap.parse_args()

    if args.command == "init":
        return init()
    if args.command == "screenshots":
        return screenshots(args.minutes)
    config = load_config()
    save = args.save or config["active_save"]
    cfg, pages = build(save, config)
    out = write_pages(save, config, cfg, pages)
    if args.open:
        webbrowser.open((out / f"{args.open}.html").resolve().as_uri())


if __name__ == "__main__":
    sys.exit(main())
