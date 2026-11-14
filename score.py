"""FMAnalyst's scoring: reads a save's imports (saves/<save>/obs and staff, written by import_export.py), scores every
player at each position and every staff member for each job, and writes the pages (out/<save>/*.html).

    python score.py [--save NAME] [--open PAGE]     rebuild a save's pages (the app does this itself)
"""
import argparse
import base64
import csv
import html
import json
import os
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
VERSION = "1.4.0"  # the app's version: the window title and the guide show it; the release tag is v<VERSION>
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
STAFF_META = {"Name", "Club", "Age", "Job", "Personality"}  # a staff file's other columns are attributes
LEFT_FOOTED = {"Very Strong", "Strong", "Fairly Strong"}  # left foot this good or better: Foot = Left
MIN_KNOWN_WEIGHT = 0.85  # a player is scored at a position when attributes carrying 85% of its weight are known (FM shows "-" for unscouted ones)
PARTIAL_SQUAD = re.compile(r"-(u\d+s?|reserves)$")  # an export of only part of the club (U18s, U21s, reserves)
# Filters page (players): search minimums for the fewest top-weighted attributes making up this share of a position's
# weight, at least SEARCH_MIN_ATTRS and at most SEARCH_MAX_ATTRS: few where a handful of attributes dominate, more where
# the weight is spread out (keepers)
SEARCH_WEIGHT_SHARE, SEARCH_MIN_ATTRS, SEARCH_MAX_ATTRS = 0.85, 3, 8
CHART_ATTRS, CHART_GK_ATTRS = 8, 5  # charts page: the attribute menu lists a position's top attributes by weight


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


def parse_money(text):
    """A wage or value as FM writes it ('£1.5K p/w', '€2.1M', '$48.5K p/a') -> 1500.0, 2100000.0…; None when it
    holds no number (N/A, blank)."""
    found = re.search(r"(\d+(?:\.\d+)?)\s*([KMBkmb]?)", text)
    if not found:
        return None
    return float(found.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[found.group(2).upper()]


def carry_forward(dates):
    """The observation dates in order, each with every attribute's newest value so far: one FM couldn't show on a
    later screen keeps the value seen before."""
    newest = {}
    for stamp in sorted(dates):
        newest = {**newest, **dates[stamp]["attrs"]}
        yield stamp, dates[stamp], newest


def csv_rows(folder):
    """(file, row) for each row of each CSV file in the folder, the files in name (= date) order."""
    if not folder.is_dir():
        return
    for path in sorted(folder.glob("*.csv")):
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                yield path, row


def weighted(attrs, groups, min_known=1.0):
    """Each group's Σ(attribute × weight) / Σ(weights) over its known attributes, for the groups where those carry at
    least min_known of the total weight (1.0: every attribute must be known)."""
    scores = {}
    for key, w in groups.items():
        known = {a: x for a, x in w.items() if a in attrs}
        if known and sum(known.values()) >= min_known * sum(w.values()):
            scores[key] = sum(attrs[a] * x for a, x in known.items()) / sum(known.values())
    return scores


# --- players ---

# An observation file's name: <date>[T<hhmm>]_<source>_<view>.csv. A squad export carries its time too
# (2026-11-14T0032_my-club_outfield.csv), so two squad exports on one day are two snapshots, not one replacing the other.
OBS_NAME = re.compile(r"(\d{4}-\d{2}-\d{2}(?:T\d{4})?)_(.+)_([a-z]+)")


def obs_parts(stem):
    """'2026-11-14T0032_my-club_outfield' -> ('2026-11-14T0032', 'my-club', 'outfield')."""
    m = OBS_NAME.fullmatch(stem)
    return m.groups() if m else (stem[:10], stem[11:].rsplit("_", 1)[0], stem.rsplit("_", 1)[-1])


def row_facts(row):
    """What an observation row says about the player besides his attributes, keyed as the snapshot keeps it."""
    facts = {}
    if row.get("Left Foot"):
        facts["leftFoot"] = row["Left Foot"]
    if row.get("Wage"):
        facts["wage"] = parse_money(row["Wage"])
    if row.get("Transfer Value"):  # a scouted range, "£16K - £150K", or one figure
        low, _, high = row["Transfer Value"].partition(" - ")
        facts["value"] = (parse_money(low), parse_money(high or low))
    if row.get("Position"):  # FM's own positions, when the view shows them
        facts["position"] = row["Position"]
    if "Loan" in row:  # squad screens only: in, out, trial, or blank for none
        facts["loan"] = row["Loan"] or None
    if row.get("Rating"):  # a season's minutes and average rating, from screens that show them
        facts.update(minutes=int(row["Minutes"]), rating=float(row["Rating"]), division=row.get("Division"))
    return facts


def read_observations(obs_dir, aliases=None):
    """Every observation, by player and date: ({name: {keeper, files, dates: {date: snapshot}}}, {file: {date,
    source, clubs}}). A name FM cut short is read as the full one when save.toml's aliases give it."""
    aliases = aliases or {}
    people, sources = {}, {}
    for path, row in csv_rows(obs_dir):
        name = aliases.get(row["Name"], row["Name"])
        stamp, source, view = obs_parts(path.stem)
        sources.setdefault(path.name, {"date": stamp, "source": source, "clubs": set()})["clubs"].add(row["Club"])
        person = people.setdefault(name, {"keeper": False, "files": set(), "dates": {}})
        person["keeper"] = person["keeper"] or view == "gk"  # a goalkeeping view only lists keepers
        person["files"].add(path.name)
        seen = person["dates"].setdefault(stamp, {"attrs": {}})
        seen["club"], seen["age"] = row["Club"], int(row["Age"])
        seen.update(row_facts(row))
        seen["attrs"].update({col: int(val) for col, val in row.items() if col not in META and val})  # blank: not readable
    return people, sources


def age_benchmark(w):
    """age -> the score expected at that age (ages outside the table count as its youngest or oldest)."""
    table = {int(age): value for age, value in w["age_benchmark"].items()}
    youngest, oldest = min(table), max(table)
    return lambda age: table[min(max(age, youngest), oldest)]


def player_row(name, person, w):
    """One player: his newest scores and details, and his history (one entry per date with enough known to score
    him). None when no date has enough."""
    groups = {"GK": w["gk"]} if person["keeper"] else w["outfield"]
    expected = age_benchmark(w)
    facts = {"leftFoot": None, "wage": None, "value": (None, None), "loan": None, "position": None}
    history = []
    for stamp, seen, attrs in carry_forward(person["dates"]):
        facts.update((key, seen[key]) for key in facts if key in seen)
        scores = weighted(attrs, groups, MIN_KNOWN_WEIGHT)
        if not scores:
            continue
        # his best position: among those FM says he plays when an export showed them, else any
        allowed = [g for g in fm_positions(facts["position"]) if g in scores] or list(scores)
        best = max(allowed, key=scores.get)
        history.append({"date": stamp, "club": seen["club"], "age": seen["age"], "leftFoot": facts["leftFoot"], **scores,
                        "best": best, "score": scores[best], "vsAge": scores[best] / expected(seen["age"]),
                        "minutes": seen.get("minutes"), "rating": seen.get("rating"), "division": seen.get("division"),
                        "attrs": attrs})
    if not history:
        return None
    foot = facts["leftFoot"]
    strong_left = foot in LEFT_FOOTED
    row = {key: val for key, val in history[-1].items() if key not in ("date", "leftFoot", "attrs")}
    row.update(name=name, position=facts["position"],
               plays=[g for g in fm_positions(facts["position"]) if g in groups] or None,
               left=None if foot is None else ("yes" if strong_left else "no"),
               foot=None if foot is None else ("Left" if strong_left else "Right"),
               loan=facts["loan"], wage=facts["wage"], valueLo=facts["value"][0], value=facts["value"][1],
               history=history)
    return row


def player_cols(w, with_club):
    """The players table's columns: with_club for players at other clubs (club, wage, value, rating)."""
    def col(key, label, **more):
        return {"key": key, "label": label, **more}
    wage = col("wage", "Wage", num=True, fmt="money")
    lead = ([col("club", "Club"), wage, col("value", "Value", num=True, fmt="range"), col("age", "Age", num=True)]
            if with_club else [col("age", "Age", num=True), wage])
    return [col("name", "Player"), *lead, col("foot", "Foot"),
            *(col(g, g, num=True, fmt="f1") for g in ("GK", *w["outfield"])),
            col("position", "FM position"), col("best", "Best"), col("score", "Score", num=True, fmt="f1"),
            col("vsAge", "vs Age", num=True, fmt="pct"),
            *((col("rating", "Rating", num=True, fmt="f2"), col("minutes", "Mins", num=True)) if with_club else ())]


def filters(squad, w):
    """The player search page: per position, the squad's best there (three, one for GK) and a suggested FM search
    minimum for the attributes that carry most of the position's weight (search_attrs): their average, rounded."""
    pages = []
    for pos, weights in {"GK": w["gk"], **w["outfield"]}.items():
        top = sorted((r for r in squad if r.get(pos) is not None), key=lambda r: r[pos], reverse=True)[:1 if pos == "GK" else 3]
        if not top:
            continue
        searched = search_attrs(weights)
        rows = []
        for attr, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
            values = [r["history"][-1]["attrs"].get(attr) for r in top]
            known = [v for v in values if v is not None]
            minimum = int(sum(known) / len(known) + 0.5) if known and attr in searched else None
            rows.append({"attr": attr, "weight": weight, "values": values, "min": minimum})
        pages.append({"pos": pos, "rows": rows, "players": [{"name": r["name"], "age": r["age"], "score": r[pos]} for r in top]})
    return pages


def charts(rows, squad, w, cfg, last=frozenset()):
    """The Market page: every player (yours flagged squad), each with his position scores and the attributes its menus
    can show (each position's top ones by weight), to compare with your latest import, everyone, or a division."""
    weights = {"GK": w["gk"], **w["outfield"]}
    tops = {pos: [a for a, _ in sorted(table.items(), key=lambda item: item[1], reverse=True)][:CHART_GK_ATTRS if pos == "GK" else CHART_ATTRS]
            for pos, table in weights.items()}
    shown = {a for names in tops.values() for a in names}
    mine = {id(r) for r in squad}
    players = []
    for r in [*(r for r in rows if id(r) not in mine), *squad]:
        attrs = r["history"][-1]["attrs"]
        players.append({"name": r["name"], "club": r["club"], "division": r["division"], "age": r["age"],
                        "squad": id(r) in mine, "last": r["name"] in last, "wage": r["wage"], "rating": r["rating"],
                        "minutes": r["minutes"], "best": r["best"],
                        "scores": {g: r[g] for g in weights if r.get(g) is not None},
                        "attrs": {a: attrs[a] for a in shown if a in attrs}})
    return {"positions": list(weights), "players": players, "tops": tops, "managed": cfg["managed_club"],
            "division": cfg.get("division")}


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


def search_attrs(weights):
    """The attributes worth a minimum in FM's search: the top-weighted ones making up SEARCH_WEIGHT_SHARE of the weight."""
    ranked, total, picked, share = sorted(weights.items(), key=lambda kv: -kv[1]), sum(weights.values()), [], 0
    for attr, weight in ranked:
        if len(picked) >= SEARCH_MAX_ATTRS or (share >= SEARCH_WEIGHT_SHARE * total and len(picked) >= SEARCH_MIN_ATTRS):
            break
        picked.append(attr)
        share += weight
    return set(picked)


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


DEPTHS = ["starter", "backup", "third"]  # the Lineup's senior columns; Third takes any number


def read_pins(cfg, slots, names):
    """save.toml's grid_pins: a player = a slot label, or {slot, col, row} (each optional; row = the nth slot with that
    label, counted down the grid). Returns (slot indexes per player pinned to a slot, column per player pinned to one,
    every pinned name)."""
    pins = {name: spec if isinstance(spec, dict) else {"slot": spec} for name, spec in cfg.get("grid_pins", {}).items()}
    column = {name: DEPTHS.index(spec["col"]) for name, spec in pins.items() if spec.get("col") in DEPTHS}
    labels = {name: spec["slot"] for name, spec in pins.items() if spec.get("slot")}
    require_known({"grid_pins": list(labels)}, "grid_pins", names, "names players not in the squad")
    if unknown := set(labels.values()) - {s["label"] for s in slots}:
        raise SystemExit(f"save.toml grid_pins names slots not in grid: {sorted(unknown)}")
    slot_of = {}
    for name, label in labels.items():
        matching = [i for i, s in enumerate(slots) if s["label"] == label]
        row = pins[name].get("row")
        if row:
            if not 1 <= row <= len(matching):
                raise SystemExit(f"save.toml grid_pins: {name}'s row {row}, but grid has {len(matching)} {label!r} slot(s)")
            matching = [matching[row - 1]]
        slot_of[name] = matching
    return slot_of, column, set(pins)


def grid(squad, w, cfg, intake):
    """The Lineup: the squad by formation slot (save.toml's grid, else one per position). Seniors go best first to
    their best position in the formation: its first free Starter, then Backup, else Third (any number, shared over the
    position's slots: players to sell or loan out); a left-footer prefers a left-footed slot. Empty Starter and Backup
    slots are then filled from Third by whoever plays them best. Youngsters (youth_age and under) and the latest intake
    get one list per slot, best for their age first. Pins (read_pins) put players where the user says. Out on loan:
    only placed when pinned."""
    slots = cfg.get("grid") or [{"label": g, "pos": g} for g in ["GK", *w["outfield"]]]
    names = {r["name"] for r in squad}
    slot_of, column_of, pinned = read_pins(cfg, slots, names)
    youth_age = cfg.get("youth_age", 18)
    for key in ("grid_senior", "grid_youth", "grid_exclude"):
        require_known(cfg, key, names, "names players not in the squad")
    promoted, kept, dropped = (set(cfg.get(k, [])) for k in ("grid_senior", "grid_youth", "grid_exclude"))
    intake = intake - kept
    squad = [r for r in squad if r["name"] not in dropped]
    available = [r for r in squad if r["loan"] != "out" or r["name"] in pinned]

    def plays(r, pos):  # FM's own positions when an export showed them, else his best position and its neighbours
        return pos in (r.get("plays") or NEAR.get(r["best"], [r["best"]]))

    def fits(r):  # the slots a player can go to
        if r["name"] in slot_of:
            return slot_of[r["name"]]
        return [i for i, s in enumerate(slots) if r.get(s["pos"]) is not None and plays(r, s["pos"])
                and (not s.get("left") or r["left"] == "yes")]

    def best_pos(r, options, value):  # his best position among the slots open to him (the first on a tie)
        positions = list(dict.fromkeys(slots[i]["pos"] for i in options))
        return max(positions, key=lambda pos: value(r, pos))

    def card(r, pos, value):
        return {"name": r["name"], "age": r["age"], "loan": r["loan"], "v": value(r, pos), "score": r[pos], "left": r["left"] == "yes"}

    def place_seniors(players, value):
        table = [[[] for _ in slots] for _ in DEPTHS]
        spare, by_name = [], {r["name"]: r for r in players}
        unfilled = [sum(i in slot_of.get(r["name"], ()) for r in players) for i in range(len(slots))]  # pinned players still to come
        for r in sorted(players, key=lambda r: (r["name"] not in column_of, -value(r, r["best"]))):
            options = fits(r)
            if not options:
                spare.append(r)
                continue
            pos = best_pos(r, options, value)
            here = [i for i in options if slots[i]["pos"] == pos]
            is_pinned = r["name"] in slot_of
            if is_pinned:
                for i in options:
                    unfilled[i] -= 1
            # a left-footed slot first (only left-footers get there), then one no pinned player is still coming to
            rank = lambda i: (not slots[i].get("left"), 0 if is_pinned else unfilled[i], i)
            tiers = (column_of[r["name"]],) if r["name"] in column_of else (0, 1)
            open_ = sorted((d, *rank(i)) for d in tiers if d < 2 for i in here if not table[d][i])
            if open_:
                depth, slot = open_[0][0], open_[0][-1]
            else:
                depth, slot = 2, min(here, key=lambda i: (len(table[2][i]), *rank(i)))
            table[depth][slot].append(card(r, pos, value))
        fill_gaps(table, by_name, value)
        return table, spare

    def fill_gaps(table, by_name, value):
        """Fill each empty Starter, then Backup, slot from Third: the best player-and-slot pair first. Pinned players
        stay where they are; a moved one notes 'from' (the position he'd otherwise be in)."""
        for depth in (0, 1):
            while True:
                candidates = []
                for i, slot in enumerate(slots):
                    if table[depth][i]:
                        continue
                    for j in range(len(slots)):
                        for k, c in enumerate(table[2][j]):
                            r = by_name[c["name"]]
                            if c["name"] in slot_of or c["name"] in column_of or r.get(slot["pos"]) is None:
                                continue
                            if (slot.get("left") and not c["left"]) or not plays(r, slot["pos"]):
                                continue
                            candidates.append((value(r, slot["pos"]), -i, -j, -k, i, j, k))  # ties: the first slot, then the first found
                if not candidates:
                    break
                *_, i, j, k = max(candidates)
                moved = table[2][j].pop(k)
                table[depth][i].append(card(by_name[moved["name"]], slots[i]["pos"], value) | {"from": slots[j]["pos"]})

    def for_age(r, pos):  # the position's score against what's normal at his age
        return r[pos] * r["vsAge"] / r["score"]

    def place_young(players):
        """One list per slot: each youngster at his best position for his age, shared over its slots (a left-footer
        preferring a left-footed one), best for age first."""
        lists, spare = [[] for _ in slots], []
        for r in sorted(players, key=lambda r: r["vsAge"], reverse=True):
            options = fits(r)
            if not options:
                spare.append(r)
                continue
            pos = best_pos(r, options, for_age)
            slot = min((i for i in options if slots[i]["pos"] == pos), key=lambda i: (not slots[i].get("left"), len(lists[i]), i))
            lists[slot].append(card(r, pos, for_age))
        return [sorted(l, key=lambda c: c["v"], reverse=True) for l in lists], spare

    def senior(r):
        return r["age"] > youth_age or r["name"] in promoted

    seniors, spare = place_seniors([r for r in available if senior(r)], lambda r, pos: r[pos])
    youth, spare_youth = place_young([r for r in available if not senior(r) and r["name"] not in intake])
    newcomers, spare_new = place_young([r for r in available if not senior(r) and r["name"] in intake])
    extra = {"youth": youth, "intake": newcomers}
    extra_cols = [{"key": "youth", "label": "Youth", "title": f"players {youth_age} and under, best for their age first"}]
    if any(newcomers):
        extra_cols.append({"key": "intake", "label": "Intake", "title": "the latest youth intake, best for their age first"})

    def short(r, v):
        return {"name": r["name"], "age": r["age"], "pos": r["best"], "v": v}
    return {"youthAge": youth_age, "extraCols": extra_cols,
            "slots": [{"label": s["label"], "pos": s["pos"], "left": bool(s.get("left")),
                       "senior": [depth[i] for depth in seniors], "extra": {k: extra[k][i] for k in extra}}
                      for i, s in enumerate(slots)],
            "spare": [short(r, r["score"]) for r in sorted(spare, key=lambda r: r["score"], reverse=True)],
            "spareYouth": [short(r, r["score"]) for r in sorted(spare_youth + spare_new, key=lambda r: r["vsAge"], reverse=True)],
            "loanOut": [short(r, r["score"]) for r in squad if r["loan"] == "out" and r["name"] not in pinned],
            "formations": formations([r for r in available if r["loan"] != "out"], plays)}


# --- staff ---

def read_staff(staff_dir, words):
    """Every staff observation, by person and date, FM's words turned into numbers ([staff_words])."""
    people = {}
    for path, row in csv_rows(staff_dir):
        person = people.setdefault(row["Name"], {"files": set(), "dates": {}})
        person["files"].add(path.name)
        seen = person["dates"].setdefault(path.stem[:10], {"attrs": {}})
        seen.update(club=row["Club"], age=int(row["Age"]) if row.get("Age") else None,
                    job=row.get("Job") or None, personality=row.get("Personality") or None)
        for attr, word in row.items():
            if attr in STAFF_META or not word:  # blank: not readable
                continue
            if word not in words:
                raise SystemExit(f"{path.name}: {row['Name']}: {attr} = {word!r} isn't in the weights file's [staff_words]")
            seen["attrs"][attr] = words[word]
    return people


def staff_row(name, person, roles):
    """One staff member: his newest details and score for every job, and his history by date."""
    job = personality = None
    history = []
    for stamp, seen, attrs in carry_forward(person["dates"]):
        job, personality = seen["job"] or job, seen["personality"] or personality
        scores = weighted(attrs, roles)
        best = max(scores, key=scores.get, default=None)
        history.append({"date": stamp, "club": seen["club"], "age": seen["age"], **scores,
                        "best": best, "score": scores.get(best), "attrs": attrs})
    row = {key: val for key, val in history[-1].items() if key not in ("date", "attrs")}
    row.update(name=name, job=job, personality=personality, history=history)
    return row


def staff_cols(roles):
    """The staff tables' columns: who, then a score per job."""
    def col(key, label, **more):
        return {"key": key, "label": label, **more}
    return [col("name", "Name"), col("club", "Club"), col("age", "Age", num=True), col("job", "Job"),
            col("personality", "Personality"), *(col(role, role, num=True, fmt="f1") for role in roles)]


def staff_filters(all_staff, roles, words, names):
    """The staff search page: per job, the best three imported (any club) and a suggested FM search minimum, as FM's
    word nearest the average, for the attributes that carry most of the job's weight (search_attrs)."""
    def nearest_word(value):
        return min(words, key=lambda word: abs(words[word] - value))
    pages = []
    for role, weights in roles.items():
        top = sorted((r for r in all_staff if r.get(role) is not None), key=lambda r: r[role], reverse=True)[:3]
        if not top:
            continue
        searched = search_attrs({a: x for a, x in weights.items() if x > 0})
        rows = []
        for attr, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
            values = [r["history"][-1]["attrs"].get(attr) for r in top]
            known = [v for v in values if v is not None]
            rows.append({"attr": attr, "weight": weight, "values": values,
                         "labels": ["–" if v is None else nearest_word(v) for v in values],
                         "min": nearest_word(sum(known) / len(known)) if known and attr in searched else None})
        full = names.get(role)
        pages.append({"pos": role, "title": full if full != role else None, "rows": rows,
                      "players": [{"name": r["name"], "age": r["age"], "score": r[role], "club": r["club"]} for r in top]})
    return pages


def require_known(cfg, key, known, what):
    """Stop when save.toml's list `key` names something that isn't there (a typo, or a name FM cut short)."""
    unknown = sorted(set(cfg.get(key, [])) - set(known))
    if unknown:
        raise SystemExit(f"save.toml {key} {what}: {unknown}")


# --- pages ---


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


def squad_moves(rows, cfg, managed, sources):
    """Who's at the club now. A player of yours missing from your newest full squad export has left (a youth or
    reserve squad export, PARTIAL_SQUAD, shows only part of the club); save.toml's released / signed / recalled say
    what happened since that export. Changes the rows' club and loan in place."""
    newest = max((info["date"] for info in sources.values() if not info["source"].startswith("search-")
                  and not PARTIAL_SQUAD.search(info["source"]) and managed in info["clubs"]), default="")
    released, signed, recalled = (set(cfg.get(k, [])) for k in ("released", "signed", "recalled"))
    for r in rows:
        last_seen = r["history"][-1]["date"]
        if r["club"] == managed and (last_seen < newest or r["name"] in released):
            r["club"] = f"left {managed}"
        if r["name"] in signed and last_seen >= newest:
            r["club"] = managed
        if r["name"] in recalled and r["loan"] == "out":
            r["loan"] = None


def latest_intake(people, sources):
    """The newest youth intake's players (a file whose source says intake), until a squad export shows them."""
    stamp = max((info["date"] for info in sources.values() if "intake" in info["source"]), default="")
    in_intake = lambda f: sources[f]["date"] == stamp and "intake" in sources[f]["source"]
    in_squad_since = lambda f: sources[f]["date"] >= stamp and not sources[f]["source"].startswith("search-")
    return {name for name, person in people.items()
            if any(map(in_intake, person["files"])) and not any(map(in_squad_since, person["files"]))}


def build(save, config):
    """Every page: {page: (table columns, data[, extra])}."""
    save_dir = ROOT / "saves" / save
    if not (save_dir / "save.toml").is_file():
        raise SystemExit(f"There's no save called {save} (no saves/{save}/save.toml).")
    cfg = load_toml(save_dir / "save.toml")
    w = load_weights(cfg, save_dir)
    managed = cfg["managed_club"]

    people, sources = read_observations(save_dir / "obs", cfg.get("aliases"))
    rows = [row for name, person in people.items() if (row := player_row(name, person, w))]
    scored = {r["name"] for r in rows}
    if left_out := sorted(set(people) - scored):
        print(f"[{save}] left out, not enough attributes known to score: {', '.join(left_out)}")
    for key in ("last_names", "released", "signed", "recalled"):
        require_known(cfg, key, people, "has players never imported")
    require_known(cfg, "last_batch", sources, "names files with no players")
    # the last import: the players named in last_names (a list of names), else those in last_batch's files
    named, batch = set(cfg.get("last_names", [])), set(cfg.get("last_batch", []))
    last_rows = [r for r in rows if (r["name"] in named if named else people[r["name"]]["files"] & batch)]
    squad_moves(rows, cfg, managed, sources)
    squad = [r for r in rows if r["club"] == managed]
    others = [r for r in rows if r["club"] != managed]

    roles, words = w.get("staff", {}), w.get("staff_words", {})
    staff = read_staff(save_dir / "staff", words)
    staff_rows = [staff_row(name, person, roles) for name, person in staff.items()]
    require_known(cfg, "last_staff_batch", {f for person in staff.values() for f in person["files"]}, "names files with no staff")
    require_known(cfg, "last_staff_names", staff, "has staff never imported")
    staff_batch, staff_named = set(cfg.get("last_staff_batch", [])), set(cfg.get("last_staff_names", []))
    last_staff = [r for r in staff_rows if staff[r["name"]]["files"] & staff_batch or r["name"] in staff_named]

    lineup = grid(squad, w, cfg, latest_intake(people, sources))
    players_table, others_table = player_cols(w, False), player_cols(w, True)
    return cfg, {
        "squad": (players_table, squad, {"lineup": lineup}),
        "development": (players_table, development(rows, people, sources, managed, squad)),
        "analysis": (players_table, analysis(squad, others, lineup, w)),
        "elsewhere": (others_table, others),
        "last": (others_table, last_rows),
        "filters": ([], filters(squad, w)),
        "charts": ([], charts(rows, squad, w, cfg, {r["name"] for r in last_rows})),
        "staff": (staff_cols(roles), staff_rows),
        "last_staff": (staff_cols(roles), last_staff),
        "staff_filters": ([], staff_filters(staff_rows, roles, words, w.get("staff_names", {}))),
        "attributes": ([], attributes_page(cfg, w, rows, save_dir)),
        "guide": ([], GUIDE + [settings_tile(config, cfg, save)]),
    }


# The menu's left end: the icon and the name; and the icon again, large and faint, in the page's corner
_LOGO = (APP / "logo.svg").read_text(encoding="utf-8")
BRAND = ('<span class="brand">' + re.sub(r"<!--.*?-->\s*", "", _LOGO, flags=re.S) + '<span><b>FM</b>Analyst</span></span>'
         '<i class="watermark" style="background-image:url(data:image/svg+xml;base64,'
         + base64.b64encode(_LOGO.encode("utf-8")).decode() + ')"></i>')


def page_keys():
    """page -> its key: 1-9 and 0 for the first ten, then PAGE_KEYS' letters."""
    return {page: PAGE_KEYS.get(page) or str(n % 10) for n, page in enumerate(PAGES, 1)}


def menu(current, keys):
    links = []
    for page in PAGES:
        if page in NAV_GROUP_STARTS:
            links.append('<span class="sep"></span>')
        on = ' class="on"' if page == current else ""
        links.append(f'<a href="{page}.html"{on}><b>{keys[page]}</b> {PAGE_LABELS[page]}</a>')
    return BRAND + "".join(links)


def as_script(value):
    """JSON to put inside a <script>: a '</' (a name with '</script>' in it) can't end the script early."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


def write_pages(save, config, cfg, pages):
    """Write out/<save>/<page>.html for every page, and remove pages an older version wrote."""
    out = ROOT / "out" / save
    out.mkdir(parents=True, exist_ok=True)
    keys = page_keys()
    stamps = [f.name[:10] for folder in ("obs", "staff") for f in (ROOT / "saves" / save / folder).glob("*.csv")]
    site = json.dumps({"pages": {keys[p]: p for p in PAGES}, "currency": config.get("currency", "£"),
                       "gameDate": max(stamps, default=None)})  # the newest date: the profile's "years ago"
    w = load_weights(cfg, ROOT / "saves" / save)
    weights = json.dumps({"GK": w["gk"], **w["outfield"], **w.get("staff", {})}, ensure_ascii=False)  # the profile's ordering
    fills = {"__THEME__": (APP / "theme.css").read_text(encoding="utf-8"), "__SITE__": site, "__WEIGHTS__": weights,
             "__SAVE__": save, "__HOME__": html.escape(cfg["short_name"])}
    templates = {name: (APP / name).read_text(encoding="utf-8") for name in ("template.html", "charts.html")}
    for page, (columns, data, *extra) in pages.items():
        extra = extra[0] if extra else {}
        text = templates["charts.html" if page == "charts" else "template.html"]
        for marker, value in {**fills, "__NAV__": menu(page, keys), "__PAGE__": page,
                              "__TITLE__": f"{fills['__HOME__']} · {PAGE_LABELS[page]}", "__COLS__": json.dumps(columns),
                              "__LINEUP__": as_script(extra.get("lineup")), "__DATA__": as_script(data)}.items():
            text = text.replace(marker, value)
        (out / f"{page}.html").write_text(text, encoding="utf-8")
    for stale in out.glob("*.html"):
        if stale.stem not in pages:
            stale.unlink()
    tables = [p for p, spec in pages.items() if isinstance(spec[1], list) and p not in ("filters", "staff_filters", "guide")]
    print(f"[{save}] out/{save}/: " + ", ".join(f"{p} ({len(pages[p][1])})" for p in tables))
    return out


# The guide page (key g): how to use the app and each page. Simple HTML; shown as tiles by template.html's renderGuide.
# A tile with a key links to that page. The last tile, "Your settings", is added by settings_tile().
GUIDE = [
    {"key": "", "title": "Getting started",
     "text": "<ol><li>In FM, open your <b>squad</b> screen with the <b>Squad - Outfield Players View</b> (see the README for the views).</li>"
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
             "<li><b>A staff search</b> (the <b>Search - Staff View</b>) or your club's staff screen: on <b>Last staff "
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
             "<b>Scout report:</b> two or three lines like FM's scout: what kind of player he is at his position (FM's own, "
             "or his best by attributes), his level, his strengths and weaknesses for that position, and how he's doing for his age.<br>"
             "<b>Scores:</b> one row per snapshot, oldest first: club, age, every position score, Best, Score and vs Age, each "
             "with its change since the row before (<span class='t4'>+1.2</span>, <span class='t0'>−0.3</span>); the "
             "<b>Change</b> row at the bottom is the total change since the first snapshot.<br>"
             "<b>Attributes:</b> every attribute at each snapshot, oldest to newest, each with its change, and <b>Change</b>: the "
             "total since the first snapshot. The attributes that matter most at his position come first."},
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
     "text": "<b>Shows:</b> your squad (orange) against players you've imported (pink), position by position.<br>"
             "<b>Controls:</b> <b>compare with</b> = whom to compare with: your <b>Last player import</b>, <b>All imported "
             "players</b>, or a division you've imported with ratings. <b>position</b> picks the position for the lower "
             "charts; <b>attribute</b> lists that position's most important attributes. Hover a dot to see who it is.<br>"
             "<b>Charts:</b> <b>Role score by position</b> and <b>Attribute by position</b>: for each position the pink "
             "range is the players you compare with (the line from lowest to highest, the shaded box their middle half, the "
             "white bar and number their middle, a tick for each player); your players are the orange diamonds, and the "
             "number at the right is your best minus their middle (green = you're ahead). <b>Score vs wage</b>: four "
             "quadrants split at their middle; top left = <b>better and cheaper</b> (good value), bottom right = <b>worse "
             "and dearer</b>. With a division import, <b>Score vs average rating</b>: does a higher score mean better matches?"},
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
             "<li>Use the <b>Search - Outfield Players View</b> for searches, so everyone gets a score at every position.</li></ul>"},
    {"key": "", "title": "Keyboard shortcuts",
     "text": "<ul><li><b>1</b>–<b>9</b>, <b>0</b>, <b>a</b>, <b>g</b>: go to a page (the numbers in the menu).</li>"
             "<li><b>Arrow keys</b>: move around a table; <b>Enter</b>: the player's profile; <b>s</b>: sort by the current column.</li>"
             "<li><b>/</b> or <b>f</b>: the Search box; <b>Esc</b>: clear it, or close a profile.</li>"
             "<li><b>c</b>: copy the player's name, to paste into FM's search.</li>"
             "<li><b>?</b>: the list of every shortcut.</li></ul>"},
]


# --- settings and the command line ---

def load_config():
    """config.toml: this computer's settings (the app writes it on first start)."""
    if not CONFIG.is_file():
        raise SystemExit(f"No settings yet ({CONFIG}): start the app once to set it up.")
    return load_toml(CONFIG)


def toml_str(s):
    return json.dumps(s, ensure_ascii=False)  # TOML's quoted strings are written like JSON's


def new_save(save, club, division="", weight=None):
    """Make saves/<save>/: its save.toml, and empty obs/ and staff/ folders for the imports."""
    folder = ROOT / "saves" / save
    for sub in ("obs", "staff"):
        (folder / sub).mkdir(parents=True, exist_ok=True)
    settings = {"weights": weight or DEFAULT, "managed_club": club, "short_name": save, "division": division}
    lines = [f"# FMAnalyst save for {club}. The Guide's last tile shows these settings."]
    lines += [f"{key} = {toml_str(value)}" for key, value in settings.items()]
    lines += ["last_batch = []", "last_staff_batch = []"]
    (folder / "save.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder / "save.toml"


def main():
    """python score.py [--save NAME] [--open PAGE]: rebuild a save's pages from its imports (the app does this itself
    on every start and import)."""
    parser = argparse.ArgumentParser(description="Rebuild FMAnalyst's pages for a save.")
    parser.add_argument("--save", help="the save to rebuild (default: config.toml's active_save)")
    parser.add_argument("--open", nargs="?", const="squad", choices=PAGES, metavar="PAGE", help="then open a page in the browser")
    args = parser.parse_args()
    config = load_config()
    save = args.save or config["active_save"]
    out = write_pages(save, config, *build(save, config))
    if args.open:
        webbrowser.open((out / f"{args.open}.html").resolve().as_uri())


def set_active_save(save):
    """Point config.toml's active_save at a save, keeping the rest of the file as it is."""
    text = CONFIG.read_text(encoding="utf-8")
    line = f"active_save = {toml_str(save)}"
    text = re.sub(r"^active_save\s*=.*$", line, text, flags=re.M) if re.search(r"^active_save\s*=", text, re.M) \
        else line + "\n" + text
    CONFIG.write_text(text, encoding="utf-8")


def write_config(save, currency, exports_dir=None):
    """config.toml: this computer's settings (the save in use, the currency FM shows, where the exports are)."""
    ROOT.mkdir(parents=True, exist_ok=True)
    settings = {"active_save": save, "currency": currency, **({"exports_dir": str(exports_dir)} if exports_dir else {})}
    text = "# FMAnalyst's settings for this computer.\n" + "".join(f"{k} = {toml_str(v)}\n" for k, v in settings.items())
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
