"""ElevenIndex's scoring: reads a save's imports (saves/<save>/obs and staff, written by import_export.py), scores every
player at each position and every staff member for each job, and writes the pages (out/<save>/*.html).

    python score.py [--save NAME] [--open PAGE]     rebuild a save's pages (the app does this itself)
"""
import argparse
import base64
import csv
import functools
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
# temporary folder, so the data lives in Documents\ElevenIndex. ELEVENINDEX_HOME (or the older FMANALYST_HOME)
# overrides where the data goes.
APP = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))


def data_root():
    """Documents\\ElevenIndex, moved there once from the FMAnalyst folder an earlier version used (renamed whole: saves,
    settings, weights); if the move can't happen (a file in use), the old folder is used as it is. Nothing is deleted."""
    new, old = documents() / "ElevenIndex", documents() / "FMAnalyst"
    if not new.exists() and old.is_dir():
        try:
            old.rename(new)
        except OSError:
            return old
    return new


_HOME = os.environ.get("ELEVENINDEX_HOME") or os.environ.get("FMANALYST_HOME")
ROOT = Path(_HOME) if _HOME else data_root() if getattr(sys, "frozen", False) else Path(__file__).parent
CONFIG = ROOT / "config.toml"
VERSION = "1.15.0"  # the app's version: the window title and the guide show it; the release tag is v<VERSION>
PROJECT_URL = "https://github.com/angelkirchev/ElevenIndex"
CREATOR, CREATOR_URL = "Angelski", "https://community.sports-interactive.com/profile/428404-angelski/"  # About: the maker, on the official FM forum
PAGES = ["squad", "formations", "analysis", "development", "charts", "elsewhere", "last", "filters", "compare", "staff",
         "last_staff", "staff_filters", "attributes", "guide"]
# Each page's full name (the window title, the Guide)
PAGE_LABELS = {"squad": "Lineup", "formations": "Formations", "development": "Development", "elsewhere": "All players", "last": "Last player import", "filters": "Player search",
               "charts": "Research", "compare": "Compare players", "staff": "All staff", "last_staff": "Last staff import", "staff_filters": "Staff search",
               "analysis": "Analysis", "attributes": "Attributes", "guide": "Guide"}
# The top menu: (key, page) or (key, group name, its pages, the page it opens on), None = a divider. A group's pages are
# sub-tabs under the bar; its key opens the group on its first page (Lineup, Last import…), and pressed again moves to the next.
MENU = [("1", "Squad", ["squad", "formations", "analysis", "development"], "squad"), ("2", "charts"),
        ("3", "Players", ["elsewhere", "last", "filters", "compare"], "last"),
        ("4", "Staff", ["staff", "last_staff", "staff_filters"], "last_staff"),
        None, ("a", "attributes"), ("g", "guide")]
SUB_LABELS = {"squad": "Lineup", "formations": "Formations", "analysis": "Analysis", "development": "Development",
              "elsewhere": "All players", "last": "Last import", "filters": "Search", "compare": "Compare",
              "staff": "All staff", "last_staff": "Last import", "staff_filters": "Search"}
META = {"Name", "Club", "Age", "Position", "Left Foot", "Preferred Foot", "Wage", "Transfer Value", "Loan", "Minutes", "Rating",
        "Division"}  # every other column is an attribute
STAFF_META = {"Name", "Club", "Age", "Job", "Personality"}  # a staff file's other columns are attributes
LEFT_FOOTED = {"Very Strong", "Strong", "Fairly Strong"}  # older views' Left Foot this good or better: Foot = Left


def foot_word(preferred):
    """FM's Preferred Foot ("Left-Footed", "Right-Footed Only", "Either") -> Left, Right or Either."""
    p = preferred.strip().lower()
    return "Either" if p.startswith("either") else "Left" if p.startswith("left") else "Right" if p.startswith("right") else None
MIN_KNOWN_WEIGHT = 0.85  # a player is scored at a position when attributes carrying 85% of its weight are known (FM shows "-" for unscouted ones)
PARTIAL_SQUAD = re.compile(r"-(u\d+s?|reserves)$")  # an export of only part of the club (U18s, U21s, reserves)
# Filters page (players): search minimums for the fewest top-weighted attributes making up this share of a position's
# weight, at least SEARCH_MIN_ATTRS and at most SEARCH_MAX_ATTRS: few where a handful of attributes dominate, more where
# the weight is spread out (keepers)
SEARCH_WEIGHT_SHARE, SEARCH_MIN_ATTRS, SEARCH_MAX_ATTRS = 0.85, 3, 8
CHART_ATTRS, CHART_GK_ATTRS = 8, 5  # Research: a position's top attributes by weight, in a player's tooltip
# FM's four attribute groups, for Research's Technical / Mental / Physical / Goalkeeping charts
ATTRIBUTE_GROUPS = {
    "Technical": ["Corners", "Crossing", "Dribbling", "Finishing", "First Touch", "Free Kick Taking", "Heading", "Long Shots",
                  "Long Throws", "Marking", "Passing", "Penalty Taking", "Tackling", "Technique"],
    "Mental": ["Aggression", "Anticipation", "Bravery", "Composure", "Concentration", "Decisions", "Determination", "Flair",
               "Leadership", "Off The Ball", "Positioning", "Teamwork", "Vision", "Work Rate"],
    "Physical": ["Acceleration", "Agility", "Balance", "Jumping Reach", "Natural Fitness", "Pace", "Stamina", "Strength"],
    "Goalkeeping": ["Aerial Reach", "Command of Area", "Communication", "Eccentricity", "Handling", "Kicking", "One on Ones",
                    "Reflexes", "Rushing Out (Tendency)", "Punching", "Throwing"],
}


def load_toml(path):
    return tomllib.loads(path.read_text(encoding="utf-8"))


def weights_file(name):
    """weights/<name>.toml: the user's own copy in the data folder if there is one, else the program's."""
    for folder in (ROOT / "weights", APP / "weights"):
        if (folder / f"{name}.toml").is_file():
            return folder / f"{name}.toml"
    return APP / "weights" / f"{name}.toml"


# The attribute weights: weights/default.toml's, or your own, saved on the Attributes page as saves/attributes.toml.
# One table for the whole app: every save scores with it.
DEFAULT = "default"
ATTRIBUTES_FILE = ROOT / "saves" / "attributes.toml"


def flat_values(values):
    """Attribute weights as one table {attribute: 1-20}; an earlier test version's outfield and gk tables are merged."""
    values = values or {}
    if isinstance(values.get("outfield"), dict) or isinstance(values.get("gk"), dict):
        return {**values.get("gk", {}), **values.get("outfield", {})}
    return dict(values)


def compose(w):
    """A weights file's position weights: an attribute's weight at a position = its CA cost there (COSTS_FILE) x its
    attribute weight (1-20, [attributes]: one table for keepers and outfield players), scaled so each position's
    biggest weight is 10. A file with per-position tables of its own and no [attributes] is used as it is."""
    if not w.get("attributes"):
        return w
    values = flat_values(w["attributes"])
    costs = load_costs(COSTS_FILE)

    def position(cost):
        raw = {a: cost[a] * float(v) for a, v in values.items() if float(v) > 0 and cost.get(a)}
        top = max(raw.values(), default=0)
        scaled = {a: round(x / top * 10, 1) for a, x in sorted(raw.items(), key=lambda kv: (-kv[1], kv[0]))} if top else {}
        return {a: int(v) if v.is_integer() else v for a, v in scaled.items() if v > 0}
    order = w.get("outfield_positions") or [p for p in costs if p != "GK"]
    return {**w, "attributes": values, "gk": position(costs["GK"]), "outfield": {p: position(costs.get(p, {})) for p in order}}


def default_weights():
    return compose(load_toml(weights_file(DEFAULT)))


def load_weights(cfg=None, save_dir=None):
    """The weights every save scores with: the default file, with your own attribute weights (saves/attributes.toml,
    just the [attributes] tables) in place of its own when you've saved some. cfg and save_dir: not used any more (one
    table for every save), kept for the callers."""
    w = load_toml(weights_file(DEFAULT))
    if ATTRIBUTES_FILE.is_file():
        own = load_toml(ATTRIBUTES_FILE).get("attributes")
        if own:
            w = {**w, "attributes": own}
    return compose(w)


def attribute_values(w):
    """The attribute weights (1-20) w scores with: {attribute: weight}."""
    return flat_values(w.get("attributes") or load_toml(weights_file(DEFAULT))["attributes"])


def check_values(values):
    """Attribute weights from the page or a file: numbers from 1 to 20, decimals allowed (kept to 2 places; blank or 0 =
    doesn't count), and every position left with at least one attribute that counts."""
    clean = {}
    for a, v in flat_values(values).items():
        v = round(float(v), 2)
        if v == 0:
            continue
        if not (1 <= v <= 20):
            raise ValueError(f"{a}'s weight should be from 1 to 20, not {v:g}.")
        clean[str(a)] = int(v) if v.is_integer() else v
    w = compose({**load_toml(weights_file(DEFAULT)), "attributes": clean})
    if empty := [p for p, t in {"GK": w["gk"], **w["outfield"]}.items() if not t]:
        raise ValueError(f"No attribute counts at {', '.join(empty)}: give one that has a CA cost there a weight.")
    return clean


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
            if isinstance(v, dict) and all(isinstance(x, dict) for x in v.values()):  # [outfield.CD], [staff.Mgr]…
                for k, sub in v.items():
                    table(f"{name}.{toml_key(k)}", sub)
            else:
                table(name, v)
    return "\n".join(lines).lstrip("\n") + "\n"


def with_positions(base, positions):
    """base's weights with the position tables replaced by positions ({"GK": {...}, "CD": {...}…}), zero weights dropped."""
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


# FM27's current-ability cost of each attribute at each position: the fixed half of every weight (compose).
COSTS_FILE = APP / "docs" / "ca_costs_fm27.csv"
SEARCH_ATTRS = ("Acceleration", "Anticipation", "Composure", "Decisions", "Dribbling", "Finishing", "First Touch", "Heading",
                "Jumping Reach", "Off The Ball", "Pace", "Passing", "Positioning", "Stamina", "Strength", "Tackling",
                "Teamwork", "Work Rate")  # what the Search - Outfield Players View exports


def load_costs(path):
    return read_weights_table(path.read_text(encoding="utf-8-sig"))


def attributes_text(values, attrs, header):
    """The attribute weights as one list to edit in Notepad: "Acceleration 16, Pace 20, ...", biggest first. The
    attributes that don't count are named in a note. Lines starting with # are notes."""
    lines = [f"# {line}" for line in header.splitlines()]
    counted = sorted((a for a, v in values.items() if v), key=lambda a: (-values[a], a.lower()))
    lines.append(", ".join(f"{a} {values[a]:g}" for a in counted))
    if rest := sorted(set(attrs) - set(counted), key=str.lower):
        lines.append(f"# Not counted (add one with a weight to count it): {', '.join(rest)}")
    return "\n".join(lines) + "\n"


ATTRIBUTE_PAIR = re.compile(r"([A-Za-z][A-Za-z' ]*?)\s*[:=]?\s*(-?\d+(?:[.,]\d+)?)")


def read_attributes_text(text, known=()):
    """attributes_text's list back to {attribute: weight} (checked by check_values). Pairs are separated by commas
    (or new lines or semicolons); a decimal can use a dot (12.25) or a comma (12,25: no space after it). An earlier
    version's table (Attribute, Weight columns) is read too. known: the attribute names, matched whatever the case;
    a name that isn't one of them is refused (a typo would otherwise drop the attribute)."""
    body = "\n".join(l for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#"))
    if not body.strip():
        raise ValueError("the file is empty")
    if body.lstrip().lower().startswith("attribute"):  # the table an earlier version exported
        cols = {name.strip().lower(): t for name, t in read_weights_table(body).items()}
        values = cols.get("weight") or {**cols.get("gk", {}), **cols.get("outfield", {})} or next(iter(cols.values()), {})
    else:
        values = {}
        for chunk in re.split(r"[,;\n](?!\d)", body):  # a comma straight before a digit is a decimal comma
            if not chunk.strip():
                continue
            m = ATTRIBUTE_PAIR.fullmatch(chunk.strip())
            if not m:
                raise ValueError(f"couldn't read {chunk.strip()!r}: write an attribute and its weight, like Pace 20")
            values[m.group(1).strip()] = float(m.group(2).replace(",", "."))
    if known:
        names = {k.lower(): k for k in known}
        if unknown := [a for a in values if a.lower() not in names]:
            raise ValueError(f"unknown attribute{'s' if len(unknown) > 1 else ''}: {', '.join(unknown)} (check the spelling)")
        values = {names[a.lower()]: v for a, v in values.items()}
    return values


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
        raise ValueError("its first row should be: Attribute, then the positions (GK, CD, DL/DR…)")
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


def parse_wage(text):
    """A wage, per week as the pages show it: FM's views can show it a year ('€15.43M p/a', the Salary column) or a
    month ('p/m'); a scouted range ('€5.65M - €9.46M p/a') counts its lower end."""
    v = parse_money(text)
    if v is None:
        return None
    t = text.lower()
    return v / 52 if "p/a" in t or "p/y" in t else v * 12 / 52 if "p/m" in t else v


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
    if row.get("Preferred Foot"):  # FM's "Left-Footed", "Right-Footed Only", "Either"...
        facts["leftFoot"] = foot_word(row["Preferred Foot"])
    elif row.get("Left Foot"):  # older views: how good the left foot is
        facts["leftFoot"] = "Left" if row["Left Foot"] in LEFT_FOOTED else "Right"
    if row.get("Wage"):
        facts["wage"] = parse_wage(row["Wage"])
    if row.get("Transfer Value"):  # a scouted range, "£16K - £150K", or one figure
        low, _, high = row["Transfer Value"].partition(" - ")
        facts["value"] = (parse_money(low), parse_money(high or low))
    if row.get("Position"):  # FM's own positions, when the view shows them
        facts["position"] = row["Position"]
    if "Loan" in row:  # squad screens only: in, out, trial, or blank for none
        facts["loan"] = row["Loan"] or None
    if row.get("Rating"):  # a season's average rating (and minutes, from views that show them)
        facts.update(rating=float(row["Rating"]),
                     minutes=int(row["Minutes"]) if (row.get("Minutes") or "").isdigit() else None)
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


def latest_search(people, sources):
    """The names in the newest search or shortlist import (its outfield and goalkeeping files): Research compares with
    it when the latest import was your own squad."""
    def when(f):
        m = re.search(r"-(\d\d-\d\d-\d\d)$", sources[f]["source"])
        return sources[f]["date"][:10], m.group(1) if m else ""
    searches = [f for f in sources if sources[f]["source"].startswith("search-")]
    if not searches:
        return set()
    newest = max(searches, key=when)
    batch = {f for f in searches if sources[f]["source"] == sources[newest]["source"] and sources[f]["date"] == sources[newest]["date"]}
    return {name for name, person in people.items() if person["files"] & batch}


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
                        "minutes": seen.get("minutes"), "rating": seen.get("rating"),
                        "attrs": attrs})
    if not history:
        return None
    foot = facts["leftFoot"]  # Left, Right, Either or None
    strong_left = foot in ("Left", "Either")
    row = {key: val for key, val in history[-1].items() if key not in ("date", "leftFoot", "attrs")}
    row.update(name=name, position=facts["position"],
               plays=[g for g in fm_positions(facts["position"]) if g in groups] or None,
               left=None if foot is None else ("yes" if strong_left else "no"),
               foot=foot,
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
            *((col("rating", "Rating", num=True, fmt="f2"),) if with_club else ())]


def filters(squad, w):
    """The player search page: per position, the squad's best there (three, keepers too) and a suggested FM search
    minimum for the attributes that carry most of the position's weight (search_attrs): their average, rounded."""
    pages = []
    for pos, weights in {"GK": w["gk"], **w["outfield"]}.items():
        top = sorted((r for r in squad if r.get(pos) is not None), key=lambda r: r[pos], reverse=True)[:3]  # your best three, keepers too
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
    """The Research page: every player (yours flagged squad), each with his position scores and the attributes its menus
    can show (each position's top ones by weight), to compare with your latest import or everyone."""
    weights = {"GK": w["gk"], **w["outfield"]}
    tops = {pos: [a for a, _ in sorted(table.items(), key=lambda item: item[1], reverse=True)][:CHART_GK_ATTRS if pos == "GK" else CHART_ATTRS]
            for pos, table in weights.items()}
    shown = {a for names in tops.values() for a in names} | {a for names in ATTRIBUTE_GROUPS.values() for a in names}
    mine = {id(r) for r in squad}
    players = []
    for r in [*(r for r in rows if id(r) not in mine), *squad]:
        attrs = r["history"][-1]["attrs"]
        players.append({"name": r["name"], "club": r["club"], "age": r["age"],
                        "squad": id(r) in mine, "last": r["name"] in last, "wage": r["wage"], "rating": r["rating"],
                        "minutes": r["minutes"], "best": r["best"],
                        # where he plays, as the Lineup sees it: FM's positions when exported, else his best and its neighbours
                        **({"plays": r.get("plays") or NEAR.get(r["best"], [r["best"]])} if id(r) in mine else {}),
                        "scores": {g: r[g] for g in weights if r.get(g) is not None},
                        "attrs": {a: attrs[a] for a in shown if a in attrs}})
    return {"positions": list(weights), "players": players, "tops": tops, "managed": cfg["managed_club"],
            "division": cfg.get("division"), "weights": weights, "groups": ATTRIBUTE_GROUPS}


def compare_page(rows, squad, w, cfg):
    """Players › Compare: everyone (yours flagged, first), with what the page sets side by side: position scores, the
    newest attributes, where he plays and his score over time. The page keeps the two picked (by name)."""
    positions = ["GK", *w["outfield"]]
    mine = {id(r) for r in squad}
    players = [{"name": r["name"], "club": r["club"], "age": r["age"], "wage": r["wage"], "value": r["value"],
                "best": r["best"], "score": r["score"], "vsAge": r["vsAge"], "squad": id(r) in mine, "position": r["position"],
                "plays": r.get("plays") or NEAR.get(r["best"], [r["best"]]),
                "scores": {g: r[g] for g in positions if r.get(g) is not None},
                "attrs": {a: v for a, v in r["history"][-1]["attrs"].items() if v is not None},
                "history": [{"date": h["date"], "score": h["score"], "best": h["best"]} for h in r["history"]]} for r in rows]
    players.sort(key=lambda p: (not p["squad"], p["name"]))
    return {"players": players, "positions": positions, "groups": ATTRIBUTE_GROUPS, "managed": cfg["managed_club"]}


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
                     "D": ["CD"] * ("C" in sides) + ["DL/DR"] * wide,
                     "M": ["MC"] * ("C" in sides) + ["ML/MR"] * wide,
                     "AM": ["AMC"] * ("C" in sides) + ["AML/AMR"] * wide}.get(role, [])
            groups += [g for g in found if g not in groups]
    return groups


WIDE_ROLES = {"D": "DL/DR", "WB": "WBL/WBR", "M": "ML/MR", "AM": "AML/AMR"}
WIDE_GROUPS = set(WIDE_ROLES.values())


def fm_sides(text):
    """FM's position text -> the sides he plays at the wide positions: "D/WB (R), AM (RL)" -> {"DL/DR": "R",
    "WBL/WBR": "R", "AML/AMR": "LR"}. Empty without the Position column (the foot decides then)."""
    out = {}
    for part in (text or "").split(","):
        m = re.match(r"\s*([A-Z/]+)\s*(?:\(([RLC]+)\))?", part)
        sides = "".join(c for c in (m.group(2) or "") if c in "LR") if m else ""
        for role in (m.group(1).split("/") if m and sides else []):
            if role in WIDE_ROLES:
                g = WIDE_ROLES[role]
                out[g] = "".join(sorted(set(out.get(g, "") + sides)))
    return out


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
    ("4-2-3-1", "Double pivot, a ten, wingers pressing high", {"DL/DR": 2, "CD": 2, "MC": 2, "AML/AMR": 2, "AMC": 1, "ST": 1}),
    ("4-3-3 DM Wide", "A holder, two box-to-box eights, wingers", {"DL/DR": 2, "CD": 2, "DM": 1, "MC": 2, "AML/AMR": 2, "ST": 1}),
    ("4-3-3", "Flat midfield three, a front three of strikers", {"DL/DR": 2, "CD": 2, "MC": 3, "ST": 3}),
    ("4-2-3-1 DM Wide", "Two holders, a ten, two wide forwards", {"DL/DR": 2, "CD": 2, "DM": 2, "AMC": 1, "AML/AMR": 2, "ST": 1}),
    ("3-4-3", "Back three, wide midfielders, three strikers", {"CD": 3, "ML/MR": 2, "MC": 2, "ST": 3}),
    ("3-4-2-1", "Two holders, two runners, wing backs for width", {"CD": 3, "WBL/WBR": 2, "DM": 2, "MC": 2, "ST": 1}),
    ("4-4-2", "Two banks of four, strike partnership", {"DL/DR": 2, "CD": 2, "ML/MR": 2, "MC": 2, "ST": 2}),
    ("4-2-2-2", "Narrow: two tens, two strikers", {"DL/DR": 2, "CD": 2, "DM": 2, "AMC": 2, "ST": 2}),
    ("5-2-3 Wide", "Back five, front three on the break", {"CD": 3, "WBL/WBR": 2, "MC": 2, "AML/AMR": 2, "ST": 1}),
    ("4-3-1-2", "Diamond midfield, ten behind two strikers", {"DL/DR": 2, "CD": 2, "DM": 1, "MC": 2, "AMC": 1, "ST": 2}),
    ("5-4-1", "Deep low block, counter-attacks", {"CD": 3, "WBL/WBR": 2, "ML/MR": 2, "MC": 2, "ST": 1}),
    ("5-2-2-1", "Back five, two tens behind a lone striker", {"CD": 3, "WBL/WBR": 2, "MC": 2, "AMC": 2, "ST": 1}),
    ("4-2-4", "All-out attack, two wingers and two strikers", {"DL/DR": 2, "CD": 2, "MC": 2, "AML/AMR": 2, "ST": 2}),
    ("3-5-2", "Wide midfielders, midfield three, two strikers", {"CD": 3, "ML/MR": 2, "DM": 1, "MC": 2, "ST": 2}),
    ("5-3-2", "Back five, midfield three, direct to two strikers", {"CD": 3, "WBL/WBR": 2, "MC": 3, "ST": 2}),
    ("4-1-4-1", "A holder behind a midfield four, compact", {"DL/DR": 2, "CD": 2, "DM": 1, "ML/MR": 2, "MC": 2, "ST": 1}),
    ("4-4-1-1", "Compact 4-4-2 with a deep-lying forward", {"DL/DR": 2, "CD": 2, "ML/MR": 2, "MC": 2, "AMC": 1, "ST": 1}),
    ("3-4-1-2", "Back three, a ten feeding two strikers", {"CD": 3, "WBL/WBR": 2, "MC": 2, "AMC": 1, "ST": 2}),
    ("4-3-2-1", "Christmas tree: narrow, two tens", {"DL/DR": 2, "CD": 2, "MC": 3, "AMC": 2, "ST": 1}),
    ("3-4-3-0", "Strikerless: false nine and inside forwards", {"CD": 3, "WBL/WBR": 2, "MC": 2, "AML/AMR": 2, "AMC": 1}),
]
FORMATION_ORDER = ["GK", "CD", "DL/DR", "WBL/WBR", "DM", "MC", "ML/MR", "AMC", "AML/AMR", "ST"]
LINES = {"Defence": {"CD", "DL/DR", "WBL/WBR"}, "Midfield": {"DM", "MC", "ML/MR"}, "Attack": {"AMC", "AML/AMR", "ST"}}  # Formations' lines
# Without FM's positions, a player's best position (from attributes) and the ones next to it: so a midfielder isn't put
# at full-back just because his attributes would score there
NEAR = {"GK": ["GK"], "CD": ["CD", "DL/DR", "DM"], "DL/DR": ["DL/DR", "WBL/WBR", "CD"], "WBL/WBR": ["WBL/WBR", "DL/DR", "ML/MR"],
        "DM": ["DM", "MC", "CD"], "MC": ["MC", "DM", "AMC"], "ML/MR": ["ML/MR", "AML/AMR", "WBL/WBR", "MC"],
        "AMC": ["AMC", "MC", "AML/AMR", "ST"], "AML/AMR": ["AML/AMR", "ML/MR", "AMC", "ST", "MC"], "ST": ["ST", "AMC", "AML/AMR"]}


OFF_SIDE = 1.5  # a wide slot: someone who doesn't play that side counts this much lower when picking (his score stays)


def side_ok(r, group, side):
    """Whether r plays that side (L / R) of a wide position: FM's Position column, else the foot."""
    sides = fm_sides(r.get("position")).get(group)
    if sides:
        return side in sides
    if r.get("foot") == "Either":
        return True
    return (r.get("left") == "yes") == (side == "L")


def best_xi(players, need, plays):
    """The strongest side for need ({slot: count}; a wide pair is two slots, "DL/DR|L" and "DL/DR|R"): the best
    player-slot pair first, each player once, only where they play; on the wrong side of a wide slot he counts OFF_SIDE
    lower. Returns ([(slot, player, score)], unfilled count)."""
    left = dict(need)
    def pick(r, slot):
        pos, _, side = slot.partition("|")
        return r[pos] - (OFF_SIDE if side and not side_ok(r, pos, side) else 0)
    pairs = sorted(((pick(r, slot), i, slot) for i, r in enumerate(players) for slot in need
                    if r.get(slot.partition("|")[0]) is not None and plays(r, slot.partition("|")[0])), reverse=True)
    used, xi = set(), []
    for _, i, slot in pairs:
        if left[slot] and i not in used:
            used.add(i); left[slot] -= 1
            xi.append((slot, players[i], players[i][slot.partition("|")[0]]))
    return xi, sum(left.values())


def wide_slots(need):
    """need with each wide pair (DL/DR, WBL/WBR, ML/MR, AML/AMR × 2) split into a left and a right slot."""
    out = {}
    for pos, n in need.items():
        if pos in WIDE_GROUPS and n == 2:
            out[f"{pos}|L"] = out[f"{pos}|R"] = 1
        else:
            out[pos] = n
    return out


def formations(players, plays):
    """How the squad fits each of FORMATIONS: its best XI's average score, a second XI's (depth), and each position as a
    group: its starters and their backups averaged. The weakest spot is a position missing a backup, else the lowest
    group average; the strongest, the highest with every backup there."""
    out = []
    for name, note, shape in FORMATIONS:
        need = {"GK": 1, **shape}
        slots = wide_slots(need)
        def split(side_list):  # (slot, player, score) -> (position, player, score, side)
            return [(slot.partition("|")[0], r, v, slot.partition("|")[2] or None) for slot, r, v in side_list]
        xi, short = best_xi(players, slots, plays)
        taken = {r["name"] for _, r, _ in xi}
        second, short2 = best_xi([r for r in players if r["name"] not in taken], slots, plays)
        xi, second = split(xi), split(second)
        avg = lambda side, miss: sum(t[2] for t in side) / len(side) if side else 0  # over the slots filled
        groups = []
        for pos, n in need.items():
            vs = [v for p, _, v, _ in xi if p == pos] + [v for p, _, v, _ in second if p == pos]
            starters = sum(p == pos for p, *_ in xi)
            groups.append({"pos": pos, "v": sum(vs) / len(vs) if vs else 0, "starters": starters, "backups": len(vs) - starters,
                           "missing": n * 2 - len(vs)})  # n starters and n backups wanted
        weak = min(groups, key=lambda g: (g["missing"] == 0, g["v"]))
        full = [g for g in groups if not g["missing"]]
        strong = max(full, key=lambda g: g["v"]) if full else None
        side = lambda xs: [{"pos": pos, "name": r["name"], "v": v, "age": r["age"], "left": r.get("left") == "yes",
                            "sides": fm_sides(r.get("position")), "side": sd, "off": bool(sd) and not side_ok(r, pos, sd)}
                           for pos, r, v, sd in sorted(xs, key=lambda t: (FORMATION_ORDER.index(t[0]), t[3] or "", -t[2]))]
        lines = {k: [v for pos, _, v, _ in xi if pos in group] for k, group in LINES.items()}
        out.append({"name": name, "note": note, "xi": avg(xi, short), "depth": avg(second, short2), "short": short,
                    "weak": weak, "strong": strong, "groups": groups,
                    "shape": need, "players": side(xi), "second": side(second),
                    "lines": {k: sum(vs) / len(vs) if vs else None for k, vs in lines.items()}})
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
            "formations": formations([r for r in available if r["loan"] != "out"], plays),
            "plays": {r["name"]: [g for g in FORMATION_ORDER if r.get(g) is not None and plays(r, g)] for r in squad},
            "sides": {r["name"]: fm_sides(r.get("position")) for r in squad}}


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


def staff_cols(roles, names=None):
    """The staff tables' columns: who, then a score per job (its full name as the title, [staff_names])."""
    def col(key, label, **more):
        return {"key": key, "label": label, **more}
    return [col("name", "Name"), col("club", "Club"), col("age", "Age", num=True), col("job", "Job"),
            col("personality", "Personality"), *(col(role, role, num=True, fmt="f1", title=(names or {}).get(role, role)) for role in roles)]


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


# Analysis' age advice: a rough guess at next season for a player 30+: FM takes the physical side first, so these
# drop a point (two from 33), and he's rescored with the same weights. An estimate, shown with "~".
AGE_DECLINE = ("Pace", "Acceleration", "Stamina", "Jumping Reach")


def next_season(attrs, age, weights):
    if age is None or age < 30:
        return None
    drop = 2 if age >= 33 else 1
    older = {a: max(1, v - drop) if a in AGE_DECLINE else v for a, v in attrs.items()}
    return weighted(older, {"x": weights}, MIN_KNOWN_WEIGHT).get("x")


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
        pos_w = w["gk"] if pos == "GK" else w["outfield"][pos]
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
                                    "score": r.get(pos), "attrs": attrs_of(r),
                                    "next": next_season(attrs_of(r), r["age"], pos_w)})
        pool = [r for r in others if r["best"] == pos]
        sums = {}
        for r in pool:
            for a, v in attrs_of(r).items():
                n, t = sums.get(a, (0, 0))
                sums[a] = (n + 1, t + v)
        # who on your lists could take over from an ageing starter: under 28, playing the position, best first
        market = sorted((r for r in others if r.get(pos) is not None and (r["age"] or 99) < 28
                         and (pos in (r.get("plays") or []) or r["best"] == pos)), key=lambda r: -r[pos])[:25]
        out.append({"pos": pos, "players": players, "imported": len(pool),
                    "market": [{"name": r["name"], "age": r["age"], "club": r["club"], "score": r[pos], "wage": r["wage"]}
                               for r in market],
                    "importedAvg": {a: t / n for a, (n, t) in sums.items()},
                    "importedScore": sum(r[pos] for r in pool) / len(pool) if pool else None})
    # The attributes to rank by: the most weighted (summed over the positions) first
    weight = {}
    for pw in [w["gk"], *w["outfield"].values()]:
        for a, v in pw.items():
            weight[a] = weight.get(a, 0) + v
    known = {a for r in squad for a in attrs_of(r)}
    attrs = sorted(known, key=lambda a: (-weight.get(a, 0), a))
    best_shape = (lineup.get("formations") or [None])[0]  # the Squad plan's formation line: the best fit's weak spot
    shape = {"name": best_shape["name"], "xi": best_shape["xi"], "weak": best_shape.get("weak")} if best_shape else None
    return {"positions": out, "attrs": attrs, "players": rows, "shape": shape}  # players: for the history overlay


def attributes_page(cfg, w, rows, save_dir):
    """The attributes page: the attribute weights (1-20, one for every position) every save scores with, to change in
    the page (saved by the app), and FM's CA costs (what turns them into each position's weights)."""
    costs = load_costs(COSTS_FILE)
    order = ["GK", *w["outfield"]]
    own = ATTRIBUTES_FILE
    return {"custom": own.is_file(),
            "saved": time.strftime("%Y-%m-%d", time.localtime(own.stat().st_mtime)) if own.is_file() else None,
            "values": attribute_values(w), "defaults": flat_values(load_toml(weights_file(DEFAULT))["attributes"]),
            "costs": {p: costs.get(p, {}) for p in order}, "search": list(SEARCH_ATTRS),
            "attrs": sorted({a for p in order for a in costs.get(p, {})}, key=str.lower)}


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
    """Menu → About: the version, this save's main settings, where to change them, and the project page."""
    rows = [("App version", f"ElevenIndex {VERSION}"), ("Save", save), ("Club", cfg.get("managed_club", "–")), ("Attribute weights", "your own (Attributes page; every save)" if ATTRIBUTES_FILE.is_file() else "default"),
            ("Currency", config.get("currency", "–")), ("Exports folder", config.get("exports_dir") or "found under Documents"),
            ("Data folder", str(ROOT))]
    table = "".join(f"<tr><td class=\"text\">{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
    table += (f'<tr><td class="text">Project page</td><td><a href="{PROJECT_URL}" target="_blank" rel="noopener">'
              f'{PROJECT_URL.removeprefix("https://")}</a> (updates, README, report a problem)</td></tr>')
    table += (f'<tr><td class="text">Created by</td><td><b>{CREATOR}</b> · <a href="{CREATOR_URL}" target="_blank" '
              f'rel="noopener">profile on the official FM forum</a> (Sports Interactive Community)</td></tr>')  # the last line
    return {"key": "", "title": "About and your settings",
            "text": f"<table>{table}</table>Switch, start or delete saves with <b>Menu → Saves</b> (under the logo). Everything else is in "
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
    # Lineup: per position, the best on your imported lists whose best position it is (to beat your starter)
    lineup["targets"] = {pos: [{"name": r["name"], "club": r["club"], "age": r["age"], "wage": r["wage"], "v": r[pos]}
                               for r in sorted((r for r in others if r["best"] == pos and r.get(pos) is not None),
                                               key=lambda r: r[pos], reverse=True)[:8]]
                         for pos in FORMATION_ORDER}
    players_table, others_table = player_cols(w, False), player_cols(w, True)
    def import_label(files):  # "Player Search, exported 2026-11-14 13:08" from an import's observation file names
        for f in sorted(files):
            m = re.match(r"(\d{4}-\d\d-\d\d)(?:T\d{4})?_search-(.+)-(\d\d)-(\d\d)-\d\d_", f)
            if m:
                return f"{m.group(2).replace('-', ' ').title()}, exported {m.group(1)} {m.group(3)}:{m.group(4)}"
            if re.match(r"\d{4}-\d\d-\d\d", f):
                return f"exported {f[:10]}"
        return ""
    many = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"
    info = {
        "squad": f"{many(len(squad), 'player')} at {managed} · click a position on the pitch for everyone who plays it",
        "formations": "20 shapes, ranked by how well your squad fits them · click one for its eleven and the numbers",
        "analysis": "your team position by position, against the players you've imported",
        "development": "how your players changed between two squad exports",
        "elsewhere": f"{many(len(others), 'player')} at other clubs, from everything you've imported",
        "last": " · ".join(filter(None, [many(len(last_rows), 'player'), import_label(batch)])) + " (your latest import)",
        "filters": "your best at each position, and the minimums to set in FM's player search (Filter)",
        "compare": "two players side by side: pick them with the icon by a name in any list",
        "staff": f"{len(staff_rows)} staff, from every staff import",
        "last_staff": " · ".join(filter(None, [f"{len(last_staff)} staff", import_label(staff_batch)])) + " (your latest staff import)",
        "staff_filters": "the best staff for each job, and FM's words to search for",
    }
    return cfg, with_info({
        "squad": (players_table, squad, {"lineup": lineup}),
        "formations": ([], {"formations": lineup["formations"]}),
        "development": (players_table, development(rows, people, sources, managed, squad)),
        "analysis": (players_table, analysis(squad, others, lineup, w)),
        "elsewhere": (others_table, others),
        "last": (others_table, last_rows),
        "filters": ([], filters(squad, w)),
        "charts": ([], charts(rows, squad, w, cfg, {r["name"] for r in last_rows if r["club"] != managed}
                              or latest_search(people, sources))),
        "compare": ([], compare_page(rows, squad, w, cfg)),
        "staff": (staff_cols(roles, w.get("staff_names", {})), staff_rows),
        "last_staff": (staff_cols(roles, w.get("staff_names", {})), last_staff),
        "staff_filters": ([], staff_filters(staff_rows, roles, words, w.get("staff_names", {}))),
        "attributes": ([], attributes_page(cfg, w, rows, save_dir)),
        "guide": ([], GUIDE),
    }, info)


def with_info(pages, info):
    """Each page's (columns, data, extra) with its info line (the strip under the bar) in extra."""
    out = {}
    for page, (columns, data, *extra) in pages.items():
        out[page] = (columns, data, {**(extra[0] if extra else {}), "info": info.get(page)})
    return out


# The menu's left end: the icon and the name; and the icon again, large and faint, in the page's corner
_LOGO = (APP / "logo.svg").read_text(encoding="utf-8")


@functools.lru_cache(maxsize=None)
def font_css():
    """fonts/fonts.css with each font file built in (a data: URL): the pages' fonts (Inter, Barlow Condensed; Latin and
    Latin Extended), with no internet needed. A page opened from disk can't load a font file beside it, so they're inline."""
    folder = APP / "fonts"
    if not (folder / "fonts.css").exists():
        return ""
    def inline(m):
        data = base64.b64encode((folder / m.group(1)).read_bytes()).decode("ascii")
        return f"url(data:font/woff2;base64,{data})"
    return re.sub(r"url\(([\w.-]+\.woff2)\)", inline, (folder / "fonts.css").read_text(encoding="utf-8"))
BRAND = ('<span class="brand">' + re.sub(r"<!--.*?-->\s*", "", _LOGO, flags=re.S) + '<span class="bname"><span class="bword">INDEX</span><small>Rate smarter. Build better.</small></span></span>'
         '<i class="watermark" style="background-image:url(data:image/svg+xml;base64,'
         + base64.b64encode(_LOGO.encode("utf-8")).decode() + ')"></i>')


def nav_keys():
    """key -> its page, or a group's {pages, home} (the page script picks one): for the pages' keyboard shortcuts."""
    return {item[0]: {"pages": item[2], "home": item[3]} if len(item) > 2 else item[1] for item in MENU if item}


def nav_where():
    """page -> where it is in the menu, e.g. "5 · Players › Search" (the Guide's tiles)."""
    where = {}
    for item in filter(None, MENU):
        if len(item) > 2:
            where.update({p: f"{item[0]} · {item[1]} › {SUB_LABELS[p]}" for p in item[2]})
        else:
            where[item[1]] = f"{item[0]} · {PAGE_LABELS[item[1]]}"
    return where


NAV_SCRIPT = """<script>(() => {  // the menu's groups: sub-tabs under the bar, and the keys
  const PAGE = %s, NAV = %s;
  window.fmNavTarget = key => {  // a key's page: a group's next sub-tab when you're in it, else its Last import
    const t = NAV[key];
    if (!t || typeof t === "string") return t || null;
    const i = t.pages.indexOf(PAGE);
    return i >= 0 ? t.pages[(i + 1) %% t.pages.length] : t.home;
  };
  document.addEventListener("DOMContentLoaded", () => {
    const bar = document.querySelector(".bar"), info = document.getElementById("fm-info");
    let strip = document.getElementById("fm-subnav-src");
    if (strip) { strip.id = "fm-subnav"; strip.hidden = false; bar.after(strip); }
    if (info && info.dataset.on) {  // the page's line about its list, at the strip's right (a strip of its own without sub-tabs)
      if (!strip) { strip = document.createElement("nav"); strip.className = "subnav"; strip.id = "fm-subnav"; bar.after(strip); }
      info.hidden = false; strip.append(info);
    }
  });
})();</script>"""


def menu(current):
    """The top menu, with the sub-tabs of the group the page is in (moved under the bar by NAV_SCRIPT)."""
    links, tabs = [], ""
    for item in MENU:
        if item is None:
            links.append('<span class="sep"></span>')
            continue
        key, name, pages = item[0], item[1], (item[2] if len(item) > 2 else [item[1]])
        label = name if len(item) > 2 else PAGE_LABELS[name]
        on = ' class="on"' if current in pages else ""
        home = item[3] if len(item) > 2 else name
        links.append(f'<a href="{home}.html"{on}><b>{key}</b> {label}</a>')
        if len(item) > 2 and current in pages:
            tabs = "".join(f'<a href="{p}.html"{" class=on" if p == current else ""}>{SUB_LABELS[p]}</a>' for p in pages)
    sub = f'<nav class="subnav" id="fm-subnav-src" hidden>{tabs}</nav>' if tabs else ""
    return BRAND + "".join(links) + sub + NAV_SCRIPT % (json.dumps(current), json.dumps(nav_keys()))


def as_script(value):
    """JSON to put inside a <script>: a '</' (a name with '</script>' in it) can't end the script early."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


# The small banner at the right of the top bar (blank url = no banner): Knap × ElevenIndex, the author's
# partnership with Knap (2026-11-17); the link goes to his FM27 tactics.
PROMO = {"url": "https://www.fmscout.com/f-knap-tactics-for-fm27.html", "kicker": "Knap × ElevenIndex",
         "text": "Knap's FM27 tactics", "title": "Knap × ElevenIndex: Knap's FM27 tactics, on FM Scout (opens in your browser)"}


def promo_html():
    if not PROMO.get("url"):
        return ""
    # an app-style tile in the app's green: a plain shirt in front, two see-through shirts behind it left and right (no words)
    shirt = ("M8.3 4.2 5.2 5.6 3.2 9.2l2.6 1.5 1.3-1.3V19.8h9.8V9.4l1.3 1.3 2.6-1.5-2-3.6-3.1-1.4"
             "c-.7 1.3-2 2.1-3.7 2.1s-3-.8-3.7-2.1Z")
    icon = ('<svg viewBox="0 0 24 24" aria-hidden="true"><defs><linearGradient id="promo-g" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#2a9a57"/><stop offset="1" stop-color="#124a28"/></linearGradient></defs>'
            '<rect class="tile" x=".75" y=".75" width="22.5" height="22.5" rx="5.5" fill="url(#promo-g)"/>'
            '<rect class="frame" x="2.6" y="2.6" width="18.8" height="18.8" rx="3.8"/>'
            '<path class="shine" d="M4.6 5.4c2.8-1.7 12-1.7 14.8 0"/>'
            f'<path class="shirt side" transform="translate(1.6 5.6) scale(.48 .56)" d="{shirt}"/>'
            f'<path class="shirt side" transform="translate(10.9 5.6) scale(.48 .56)" d="{shirt}"/>'
            f'<path class="shirt main" transform="translate(4.5 3.4) scale(.625)" d="{shirt}"/></svg>')
    return (f'<a class="promo" href="{html.escape(PROMO["url"])}" target="_blank" rel="noopener" '
            f'title="{html.escape(PROMO["title"])}"><i class="promo-tab"><b>FM</b>SCOUT</i>{icon}'
            f'<span><small>{html.escape(PROMO["kicker"])}</small>'
            f'<em>{html.escape(PROMO["text"])} <b>→</b></em></span></a>')


# Menu > Theme: the colour themes, (name, label, top bar, accent); theme.css has their colours, the first is :root's.
THEMES = [("green", "Pitch green", "#1b6b3a", "#c5f04f"), ("blue", "Night blue", "#1c4a80", "#6cc4ff"),
          ("claret", "Claret & gold", "#7a1f35", "#f5c542"), ("graphite", "Graphite & orange", "#343b40", "#ff9a3c"),
          ("purple", "Royal purple", "#4b2a7a", "#c4a5ff")]


def write_pages(save, config, cfg, pages):
    """Write out/<save>/<page>.html for every page, and remove pages an older version wrote."""
    out = ROOT / "out" / save
    out.mkdir(parents=True, exist_ok=True)
    stamps = [f.name[:10] for folder in ("obs", "staff") for f in (ROOT / "saves" / save / folder).glob("*.csv")]
    w = load_weights(cfg, ROOT / "saves" / save)
    # for the profiles of other clubs' players: your best at each position (where he'd start for you), and the score
    # expected at each age (a youngster's estimate at 24)
    squad_rows = pages["squad"][1] if "squad" in pages and isinstance(pages["squad"][1], list) else []
    squad_best = {}
    for pos in ("GK", *w["outfield"]):
        cands = [r for r in squad_rows if r.get(pos) is not None and (not r.get("plays") or pos in r["plays"] or r.get("best") == pos)]
        if cands:
            top = max(cands, key=lambda r: r[pos])
            squad_best[pos] = [top["name"], round(top[pos], 2)]
    site = json.dumps({"where": nav_where(), "currency": config.get("currency", "£"),
                       "gameDate": max(stamps, default=None),  # the newest date: the profile's "years ago"
                       "club": cfg.get("managed_club", ""), "squadBest": squad_best,
                       "ageBench": {str(k): v for k, v in w.get("age_benchmark", {}).items()}}, ensure_ascii=False)
    weights = json.dumps({"GK": w["gk"], **w["outfield"], **w.get("staff", {})}, ensure_ascii=False)  # the profile's ordering
    fills = {"__FONTS__": font_css(), "__THEME__": (APP / "theme.css").read_text(encoding="utf-8"), "__SITE__": site, "__WEIGHTS__": weights,
             "__SAVE__": save, "__HOME__": html.escape(cfg["short_name"])}
    templates = {name: (APP / name).read_text(encoding="utf-8") for name in ("template.html", "charts.html")}
    for page, (columns, data, *extra) in pages.items():
        extra = extra[0] if extra else {}
        text = templates["charts.html" if page == "charts" else "template.html"]
        for marker, value in {**fills, "__NAV__": menu(page), "__PROMO__": promo_html(), "__PAGE__": page,
                              "__TITLE__": f"{fills['__HOME__']} · {PAGE_LABELS[page]}", "__COLS__": json.dumps(columns),
                              "__LINEUP__": as_script(extra.get("lineup")), "__INFO__": as_script(extra.get("info")),
                              "__DATA__": as_script(data)}.items():
            text = text.replace(marker, value)
        text = text.replace('<html lang="en">', f'<html lang="en" data-theme="{theme_of(config)}">', 1)
        (out / f"{page}.html").write_text(text, encoding="utf-8")
    for stale in out.glob("*.html"):
        if stale.stem not in pages:
            stale.unlink()
    tables = [p for p, spec in pages.items() if isinstance(spec[1], list) and p not in ("filters", "staff_filters", "guide")]
    print(f"[{save}] out/{save}/: " + ", ".join(f"{p} ({len(pages[p][1])})" for p in tables))
    return out


# The guide page (key g): how to use the app and each page. Simple HTML; shown as tiles by template.html's renderGuide.
# A tile with a key links to that page. The version and settings are in Menu → About (settings_tile()).
GUIDE = [
    {"key": "", "title": "Getting started",
     "text": "<ol><li>In FM, open your <b>squad</b> screen with the <b>Squad - All Players View</b> (see the README for the views).</li>"
             "<li>Point the mouse at the list and press <b>F8</b>: FM27 Exporter saves the list as a file.</li>"
             "<li>Here, open <b>Menu</b> (under the logo, or key <b>M</b>) → click the export under <b>Recent exports</b>, or <b>Import file…</b> (key <b>I</b>), or drop the file on the window. While the app is open, a new export (F8 in FM) also pops up a notice with an <b>Import</b> button.</li></ol>"
             "The app works out what it is (your squad, a search, staff), scores everyone and opens the right page. "
             "Import your squad first; after that, import searches and shortlists whenever you're looking for players."},
    {"key": "", "title": "Importing: what goes where",
     "text": "<ul><li><b>Your squad</b> (the file name starts with First Team or Squad): your players, on <b>Squad</b>, "
             "<b>Analysis</b> and <b>Development</b>. Every squad import is kept as a dated snapshot, so export it again every "
             "half-season to track progress. Players missing from a newer squad export count as gone.</li>"
             "<li><b>A player search or shortlist</b>: a list of its own, on <b>Players › Last import</b>, and added to "
             "<b>Players › All players</b>. Re-importing the same file updates that list.</li>"
             "<li><b>A staff search</b> (the <b>Search - Staff View</b>) or your club's staff screen: on <b>Staff › "
             "Last import</b> and <b>Staff › All staff</b>.</li>"
             "<li>Players with a <b>Rating</b> (FM shows one for those who've played this season) add the score vs "
             "rating chart to <b>Research</b>.</li></ul>"
             "After each import a message in the corner says how many were imported, and who was skipped (not enough "
             "attributes known). <b>Ctrl + F8</b> in FM adds every attribute column to a view."},
    {"key": "", "title": "What the numbers mean",
     "text": "<ul><li><b>Position scores</b> (GK, CD, DL/DR, WBL/WBR, DM, MC, ML/MR, AMC, AML/AMR, ST): how well a "
             "player's attributes suit that position, on FM's 1–20 scale. Each attribute counts by its weight there (see "
             "<b>Attributes</b>); pace, acceleration and stamina count most, because they win the most matches.</li>"
             "<li><b>Best</b>: the position he scores highest at; <b>Score</b>: that score. With FM's <b>Position</b> column "
             "in your exports, only positions FM says he plays count, so a striker stays a striker.</li>"
             "<li><b>vs Age</b>: his score against what's normal for his age. 100% = on track to be a good Championship or "
             "low Premier League player at his peak; more is ahead of that, less behind.</li>"
             "<li><b>Foot</b>: FM's preferred foot: Left, Right or Either (Either can play both sides).</li>"
             "<li>What the colours mean: see <b>Colours</b>.</li>"
             "<li><b>loan in</b> / <b>loan out</b> / <b>trial</b> next to a name; <b>↑CD</b> = moved up from Third to fill "
             "an empty slot (his best position was CD).</li>"
             "<li>A player whose scouted attributes are mostly unknown (FM shows -) isn't scored until you know more.</li></ul>"},
    {"key": "", "title": "Advice and extras",
     "text": "<ul><li><b>Advice lines</b>: under the Lineup pitch (how good your eleven is and what to do about its weak "
             "spot), by the top 3 Formations (why they fit), on Research (<b>Next step</b>), Development (who's growing, who's "
             "slowing) and Compare (who's better now, younger, cheaper).</li>"
             "<li><b>A scouted player's profile</b>: <b>For you</b> = where he'd start in your team and over whom; <b>At 24 "
             "(estimate)</b> for every player under 24 (his vs Age kept to 24: an estimate, not FM's potential); a radar of what counts most at his position.</li>"
             "<li><b>Analysis › Squad plan</b>: every position's recommendations as one list, most urgent first (Act now, "
             "Strengthen, Plan ahead, Bring on), with a line on where to start. <b>Research</b> graphs <b>Your squad: age vs "
             "score</b> and <b>wage vs score</b>.</li></ul>"},
    {"key": "", "title": "Colours",
     "text": "<b>Scores</b> (every position score, Best, Score, the pitch, profiles, Formations) get a colour per point, "
             "so good players don't all look alike:<br>"
             + " ".join(f'<span style="color:{c};font-weight:700">■ {t}</span>' for c, t in (
                 ("#f87171", "under 9"), ("#fb923c", "9"), ("#fbd13d", "10"), ("#d7e35a", "11"), ("#a6e163", "12"),
                 ("#4ade80", "13"), ("#2dd4bf", "14"), ("#38bdf8", "15"), ("#c084fc", "16+"))) +
             "<ul><li><b>Attributes</b>: like FM: grey 1–5, white 6–9, yellow 10–14, green 15+.</li>"
             "<li><b>vs Age</b>: red (well behind for his age), orange, white, yellow, green (well ahead).</li>"
             "<li><b>Lineup › Depth</b>: green = two or more backups there, yellow = one, red = none.</li>"
             "<li><b>Ages on the pitch</b> (Lineup, Formations): sky blue = 21 or under, white = 22–29, amber = 30+ "
             "(see <b>Analysis</b> for who could replace an ageing starter).</li>"
             "<li><b>Weakest spot</b> (Lineup, Formations): in a quiet tone by how far it is below the eleven's average, "
             "shown after it (e.g. −1.1): grey = about level, sand = a little below, amber = 1 or more below; "
             "<b>no backup</b> in red. On the Formations pitch's Positions the weakest is outlined red, the strongest green.</li>"
             "<li><b>Development</b> and <b>Compare</b>: green = better, red = worse; in Compare the first player is sky "
             "blue, the second the accent colour.</li>"
             "<li><b>Research</b>: each position is a band; the players you compare with are the accent-coloured range on top, "
             "your players the orange diamonds below it (your best big, with his name and score).</li>"
             "<li><b>Theme</b> (Menu → Theme…) changes the top bar, panels and accent only: these colours stay the "
             "same in every theme.</li></ul>"},
    {"key": "", "title": "A player's profile",
     "text": "<b>Click a name</b> on any page (or double-click a row, or press <b>Enter</b>) to open it; <b>Esc</b> closes it.<br>"
             "<b>Overview:</b> a scout report (what kind of player he is, his level, how he's doing for his age), his key facts, "
             "<b>Good for</b> (the positions FM lists for him, then others his attributes suit), his Technical / Mental / "
             "Physical values at his best position, <b>Strengths</b> and <b>Weaknesses</b> among what counts there, "
             "<b>Train first</b> (the attributes that would lift his score most: +1 → what it adds) and what isn't scouted yet.<br>"
             "<b>Positions:</b> every position score, best first, with his level and whether FM lists it.<br>"
             "<b>Attributes:</b> every attribute by group; the bar shows how much it counts at the position you pick.<br>"
             "<b>History:</b> his scores and attributes at every snapshot, each with its change, and the total change.<br>"
             "Staff profiles: every job ranked, strengths and weaknesses, attributes and history."},
    {"key": "squad", "title": "Lineup",
     "text": "<b>Pitch</b> (left): your best eleven for the formation you pick (the best fit first), each box the starter "
             "and his score there; on the wide positions each goes to the side he plays (FM's position, else his foot). "
             "<b>Score</b> colours a box by that score, <b>Depth</b> by how many backups he has there: "
             "everyone else of yours who plays it (that side, at a wide position), not counting those who start elsewhere "
             "(green = 2 or more, yellow = 1, red = none).<br>"
             "<b>Click a position</b>: on the right, everyone of yours who plays it (FM's positions, else his best and the ones "
             "next to it), best first: his role in this formation (Starter, Backup, or where he starts instead), FM position, "
             "age, score there, vs Age, wage. Under it, the best on your imported lists who'd beat your starter, with the "
             "compare icon.<br>"
             "<b>Table</b> (below): every player at your club with all his scores. Sort by clicking a heading, filter with the "
             "Search box (see <b>Searching and filtering</b>).<br>"
             "<b>Use it to:</b> spot thin positions, who's next in line, and who to buy."},
    {"key": "formations", "title": "Formations",
     "text": "<b>Shows:</b> 20 common systems ranked by how well your squad fits them: <b>XI</b> = the average score of your "
             "best eleven for that shape, <b>2nd XI</b> = a second eleven's, and the <b>weakest spot</b>: the position whose "
             "starters and backups together average lowest (one without a backup counts first).<br>"
             "<b>Click one</b>: its eleven on a pitch (with the backup under each), and the numbers: both elevens' averages, "
             "<b>Defence</b>, <b>Midfield</b> and <b>Attack</b>, every position's group average (starters + backups), the "
             "strongest and weakest spot, how many spots are good, average or weak, where there's no real backup, and the "
             "eleven's average age."},
    {"key": "analysis", "title": "Analysis",
     "text": "<b>Shows:</b> your team position by position, as the Lineup places them, ranked by what you pick in "
             "<b>Rank by</b>: the <b>position score</b> (the default), <b>Speed</b> (Pace and Acceleration together), or any "
             "single attribute.<br>"
             "<b>Positions ranked:</b> your positions from strongest to weakest: your average, your best player there, the "
             "average of the imported players whose best position it is (<b>Imported</b>) and the difference (<b>±</b>; "
             "green = you're ahead), and <b>Recommendations</b>: what to do there, most urgent first (advice only, "
             "no score changes): <b>Replace soon</b> (amber) = a starter 30+ whose ~next-season score (an estimate: "
             "Pace, Acceleration, Stamina and Jumping Reach a point lower, two from 33) no one of yours under 28 can match, "
             "with the best and the cheapest on your lists who beat it; <b>Get a backup</b> / <b>Better backup needed</b> "
             "(none, or 1.5+ below the starter); <b>Plan ahead</b> (grey) = someone of yours can take over from an "
             "ageing starter; <b>Give … minutes</b> / <b>Develop …</b> (sky blue) = a youngster (21 or under) level with "
             "the starter, or within 2.0 with what to train first. Each position's tile repeats it; on the pitches "
             "every starter's age shows by his position (amber 30+, sky blue 21 or under).<br>"
             "<b>Below:</b> each position's players, best first, with their role (Starter, Backup, Third, Youth).<br>"
             "<b>Use it to:</b> see where your team is quick or slow, strong or weak, and where to strengthen."},
    {"key": "development", "title": "Development",
     "text": "<b>Shows:</b> how your players changed between two squad snapshots: average score change, how many improved or "
             "declined, who joined or left, which attributes rose or fell most across the squad, the biggest risers and "
             "fallers, and every player's change with the attributes that went up and down.<br>"
             "<b>How:</b> pick the snapshots in <b>from</b> and <b>to</b> (the latest two by default). It needs at least two "
             "squad imports: export your squad every half-season or so.<br>"
             "<b>Use it to:</b> see whether training and game time are working, and who's stalling."},
    {"key": "charts", "title": "Research",
     "text": "<b>Shows:</b> your squad (orange) against players you've imported (the accent colour), position by position: first a table, "
             "<b>Listed players by position</b> (how many there are, their middle and best score, your best there, how many "
             "beat him and the cheapest of those; click a row to pick the position), then one graph at a time, picked in "
             "<b>Graph</b>, with how to read it underneath.<br>"
             "<b>Controls:</b> <b>compare with</b> = whom to compare with: your <b>Last player import</b>, <b>All imported "
             "players</b>. <b>position</b> picks the position for the "
             "score vs wage and rating charts. Hover a dot to see who it is.<br>"
             "<b>Charts:</b> <b>Role score</b>, <b>Technical</b>, <b>Mental</b>, <b>Physical</b> and <b>Goalkeeping by position</b> "
             "(a group's value = its attributes averaged with that position's weights, so it counts what matters there): for each position the accent-coloured "
             "range is the players you compare with (the line from lowest to highest, the shaded box their middle half, the "
             "white bar and number their middle, a tick for each player); your players are the orange diamonds, and the "
             "number at the right is your best minus their middle (green = you're ahead). <b>Score vs wage</b>: four "
             "quadrants split at their middle; top left = <b>better and cheaper</b> (good value), bottom right = <b>worse "
             "and dearer</b>. When imported players have a Rating, <b>Score vs average rating</b>: does a higher score mean better matches?"},
    {"key": "elsewhere", "title": "All players",
     "text": "<b>Shows:</b> everyone you've imported who isn't at your club, scored the same way as your squad, with club, "
             "wage and value (the top of FM's range).<br>"
             "<b>Use it to:</b> compare transfer targets with your own players, and follow players over the seasons.<br>"
             "<b>Clear old lists</b> (bottom right, click twice) moves old searches to an archive folder so the list stays "
             "short. Nothing is deleted: your squad, the latest list and anyone named in save.toml stay."},
    {"key": "last", "title": "Last player import",
     "text": "<b>Shows:</b> only the players from the file you imported last (a search or shortlist).<br>"
             "<b>Use it to:</b> judge one search at a time. Sort by <b>Score</b> or <b>vs Age</b>, filter, e.g. "
             "<b>best=ST age&lt;=23</b>, and compare with your starters on <b>Squad</b>: anyone clearly higher at a weak "
             "position is worth scouting."},
    {"key": "filters", "title": "Player search",
     "text": "<b>Shows:</b> for each position, your best players there (3, or 1 for GK) and the attributes that matter most, "
             "most important first, with a suggested minimum (<b>Filter</b>) = the average of your best players.<br>"
             "<b>Use it to:</b> set up FM's player search to find players better than yours.<br>"
             "<b>How:</b> in FM's search add <b>Position Is Accomplished At</b> that position, then each highlighted attribute "
             "<b>Is At Least</b> its Filter value. Dimmed attributes don't need a minimum. Too few results? Lower the "
             "smaller minimums first. The minimums rise as your squad improves."},
    {"key": "compare", "title": "Compare players",
     "text": "<b>Shows:</b> two players side by side: club, age, wage, best position and score, vs Age; a radar of the "
             "attributes that count most at the position you pick in <b>compare at</b>; the Technical, Mental, Physical "
             "(and Goalkeeping) values there; every position score; every attribute by group, the higher side marked; and "
             "each one's score over time.<br>"
             "<b>Pick them:</b> click the compare icon by a player's name in any list (Squad, All players, Last import…), "
             "press <b>x</b> on his row, or click <b>+ Compare</b> in his profile; the picks show at the bottom left on every "
             "page. Or type a name in an empty slot here. Two at most: remove one to pick another.<br>"
             "<b>Compare at:</b> <b>Auto</b> follows their best position (the first one's when they differ); pick any "
             "position to see them both there, e.g. two strikers as AML/AMR.<br>"
             "<b>Change them:</b> click a name to type another, <b>×</b> empties a slot, <b>⇄</b> swaps the sides. "
             "<b>vs my best</b> on an imported player puts your best at that position on the other side."},
    {"key": "staff", "title": "All staff",
     "text": "<b>Shows:</b> every staff member you've imported, scored for each job (FM's words like Good = 13, Very Good = 16): "
             "<b>Mgr</b> manager, <b>AM</b> assistant manager, <b>HoYD</b> head of youth development, coaches "
             "(<b>AttTct</b>/<b>AttTec</b> attacking tactical/technical, <b>DefTct</b>/<b>DefTec</b> defending, "
             "<b>PosTct</b>/<b>PosTec</b> possession, <b>GKC</b> goalkeeping, <b>FitC</b> fitness, <b>SetC</b> set pieces), "
             "<b>DoF</b> director of football, <b>Scout</b>, <b>PA</b> performance analyst, <b>Physio</b>, <b>SpSci</b> sports scientist.<br>"
             "<b>How:</b> read across a row: the highest number is the job he suits best. Sort by a job to find the best for it. "
             "<b>Clear old lists</b> works here too."},
    {"key": "last_staff", "title": "Last staff import",
     "text": "Only the staff from your latest staff import: judge one staff search at a time, the way Last player import works."},
    {"key": "staff_filters", "title": "Staff search",
     "text": "<b>Shows:</b> per job, the best staff you've imported (from any club) and the attributes that matter most for "
             "that job, with a suggested minimum as FM's word (e.g. Good).<br>"
             "<b>Use it to:</b> set up FM's staff search when hiring: set each highlighted attribute to at least its "
             "<b>Filter</b> word. The more staff searches you import, the better the minimums get."},
    {"key": "attributes", "title": "Attributes",
     "text": "<b>Shows:</b> one weight per attribute, 1–20 like FM's attributes, for every position at once (keepers too), and "
             "what it comes to at each position: weight × FM's CA cost there, as the attribute's share of that position's "
             "score.<br>"
             "<b>Change them:</b> type a new weight (blank = doesn't count), then <b>Save</b>. It's one table for the whole app: "
             "every save scores with it (the open save is rescored at once, the others when you open them). <b>Undo changes</b> "
             "drops what you haven't saved; <b>Reset to default</b> puts the default weights back in the page (then Save).<br>"
             "<b>Share them:</b> <b>Export attributes…</b> writes the attribute weights as one list for "
             "Notepad: Pace 20, Acceleration 16, … (1–20; decimals with a dot, 12.25). Change it and <b>Import attributes…</b> "
             "reads it back, then Save."},
    {"key": "", "title": "Searching and filtering",
     "text": "Type in the <b>Search</b> box at the top of a table (<b>/</b> or <b>f</b> jumps there, <b>Esc</b> clears it):"
             "<ul><li>a name or club: <b>kalu</b>, <b>levski</b></li>"
             "<li>a condition on any column: <b>age&lt;=21</b>, <b>best=CD</b>, <b>best!=GK</b>, <b>vs&gt;=90</b>, "
             "<b>score&gt;=12</b>, <b>foot=left</b>, <b>club:levski</b> (contains), <b>wage&lt;=10k</b>, <b>value&lt;=500k</b></li>"
             "<li>loans: <b>loan-in</b>, <b>loan-out</b>, <b>trial</b>; staff: <b>job:coach</b>, <b>PosTct&gt;=12</b></li>"
             "<li>several at once, with spaces: <b>age&lt;=23 best=ST vs&gt;=90</b></li></ul>"
             "The count next to the box shows how many match. Click a column heading to sort (again to reverse)."},
    {"key": "", "title": "Saves, folders and mistakes",
     "text": "<ul><li><b>Menu</b> (under the logo, key <b>M</b>): this save; <b>Recent exports</b> (the newest in FM27 "
             "Exporter's folder, new or imported, one click to import; ▾ shows the last five, ▴ folds them up); "
             "<b>Import file…</b> (key <b>I</b>; or drop a file on the window); <b>Open the exports folder</b>; "
             "<b>Saves…</b> (everything about saves: switch to one, start one for a new game or club, delete one, "
             "<b>Back up this save…</b> as one zip, <b>Restore a backup…</b>; a deleted save can be restored there for "
             "14 days, or removed for good at once with Delete now, clicked twice); <b>Theme…</b> (five colour themes); "
             "<b>Check for updates</b> (asks GitHub for the latest version number, nothing of yours is sent, and says in "
             "the corner whether you're up to date, with a link); <b>About</b>.</li>"
             "<li>Your data is in <b>Documents\\ElevenIndex</b>: settings (config.toml), each save's data and pages. Updating the "
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
     "text": "<ul><li><b>1</b>–<b>4</b>, <b>a</b>, <b>g</b>: go to a page (the numbers in the menu): <b>1</b> Squad opens on "
             "Lineup, <b>3</b> Players and <b>4</b> Staff on Last import; press it again for the next sub-tab.</li>"
             "<li><b>Arrow keys</b>: move around a table; <b>Enter</b>: the player's profile; <b>s</b>: sort by the current column.</li>"
             "<li><b>/</b> or <b>f</b>: the Search box; <b>Esc</b>: clear it, or close a profile.</li>"
             "<li><b>c</b>: copy the player's name, to paste into FM's search; <b>x</b>: add him to <b>Players › Compare</b>.</li>"
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
    settings = {"managed_club": club, "short_name": save, "division": division}
    lines = [f"# ElevenIndex save for {club}. The Guide's last tile shows these settings."]
    lines += [f"{key} = {toml_str(value)}" for key, value in settings.items()]
    lines += ["last_batch = []", "last_staff_batch = []"]
    (folder / "save.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return folder / "save.toml"


def main():
    """python score.py [--save NAME] [--open PAGE]: rebuild a save's pages from its imports (the app does this itself
    on every start and import)."""
    parser = argparse.ArgumentParser(description="Rebuild ElevenIndex's pages for a save.")
    parser.add_argument("--save", help="the save to rebuild (default: config.toml's active_save)")
    parser.add_argument("--open", nargs="?", const="squad", choices=PAGES, metavar="PAGE", help="then open a page in the browser")
    args = parser.parse_args()
    config = load_config()
    save = args.save or config["active_save"]
    out = write_pages(save, config, *build(save, config))
    if args.open:
        webbrowser.open((out / f"{args.open}.html").resolve().as_uri())


def set_setting(key, value):
    """Set one of config.toml's settings, keeping the rest of the file as it is."""
    text = CONFIG.read_text(encoding="utf-8")
    line = f"{key} = {toml_str(value)}"
    pattern = rf"^{re.escape(key)}\s*=.*$"
    text = re.sub(pattern, lambda m: line, text, flags=re.M) if re.search(pattern, text, re.M) \
        else text.rstrip("\n") + "\n" + line + "\n"
    CONFIG.write_text(text, encoding="utf-8")


def set_active_save(save):
    """Point config.toml's active_save at a save."""
    set_setting("active_save", save)


def theme_of(config):
    """The colour theme config.toml names (Menu > Theme), else the first."""
    name = config.get("theme", "")
    return name if name in {t[0] for t in THEMES} else THEMES[0][0]


def stamp_theme(save, theme):
    """Change the theme of a save's written pages in place (no rebuild): their <html data-theme>."""
    for page in (ROOT / "out" / save).glob("*.html"):
        text = page.read_text(encoding="utf-8")
        new = re.sub(r'<html lang="en"( data-theme="[a-z]*")?>', f'<html lang="en" data-theme="{theme}">', text, count=1)
        if new != text:
            page.write_text(new, encoding="utf-8")


def write_config(save, currency, exports_dir=None):
    """config.toml: this computer's settings (the save in use, the currency FM shows, where the exports are)."""
    ROOT.mkdir(parents=True, exist_ok=True)
    settings = {"active_save": save, "currency": currency, **({"exports_dir": str(exports_dir)} if exports_dir else {})}
    text = "# ElevenIndex's settings for this computer.\n" + "".join(f"{k} = {toml_str(v)}\n" for k, v in settings.items())
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
