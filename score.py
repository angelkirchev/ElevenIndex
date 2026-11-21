"""ElevenIndex's scoring: reads a save's imports (saves/<save>/obs and staff, written by import_export.py), scores every
player at each position and every staff member for each job, and writes the pages (out/<save>/*.html).

    python score.py [--save NAME] [--open PAGE]     rebuild a save's pages (the app does this itself)
"""
import argparse
import bisect
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
VERSION = "1.21.0"  # the app's version: the window title and the guide show it; the release tag is v<VERSION>
PROJECT_URL = "https://elevenindex.pages.dev"  # the website: download, guide, changelog; version.json for the update check
FMSCOUT_URL = "https://www.fmscout.com/a-xi-index.html"  # About: its page on FM Scout
CREATOR, CREATOR_URL = "Angelski", "https://community.sports-interactive.com/profile/428404-angelski/"  # About: the maker, on the official FM forum
PAGES = ["club", "contracts", "medical", "transfers", "squad", "formations", "depth", "analysis", "development", "training", "season", "youth", "charts", "elsewhere",
         "toplists", "compare", "staff", "team_staff", "staff_compare", "staff_research", "attributes", "guide"]
# Each page's full name (the window title, the Guide)
PAGE_LABELS = {"club": "Club", "squad": "Lineup", "formations": "Formations", "depth": "Squad depth", "youth": "Youth", "contracts": "Contracts", "medical": "Medical", "transfers": "Transfers", "development": "Development", "training": "Training plan", "season": "Match stats", "elsewhere": "Players database", "toplists": "Top lists",
               "charts": "Research", "compare": "Compare players", "staff": "Staff database", "team_staff": "Your staff", "staff_compare": "Compare staff", "staff_research": "Staff research",
               "analysis": "Analysis", "attributes": "Attributes", "guide": "Guide"}
# The top menu: (key, page) or (key, group name, its pages, the page it opens on), None = a divider. A group's pages are
# sub-tabs under the bar; its key opens the group on its first page (Lineup, Latest list…), and pressed again moves to the next.
MENU = [("1", "Club", ["club", "season", "contracts", "medical", "transfers"], "club"),
        ("2", "Squad", ["squad", "formations", "depth", "analysis", "development", "training", "youth"], "squad"),
        ("3", "Players", ["elsewhere", "toplists", "charts", "compare"], "elsewhere"),
        ("4", "Staff", ["team_staff", "staff", "staff_research", "staff_compare"], "team_staff"),
        None, ("a", "attributes"), ("g", "guide")]
SUB_LABELS = {"club": "Overview", "squad": "Lineup", "formations": "Formations", "depth": "Depth", "youth": "Youth", "contracts": "Contracts", "medical": "Medical", "transfers": "Transfers", "analysis": "Analysis", "development": "Development", "training": "Training", "season": "Match stats",
              "elsewhere": "Database", "toplists": "Top lists", "compare": "Compare",
              "staff": "Database", "team_staff": "Your staff", "staff_compare": "Compare", "charts": "Research", "staff_research": "Research"}
META = {"Name", "Club", "Age", "Position", "Left Foot", "Preferred Foot", "Wage", "Transfer Value", "Loan", "Team", "Minutes", "Rating",
        "Division", "Height", "Contract", "Deal", "Current Ability", "Potential Ability", "Reputation", "Familiarity",
        "Match Exercise", "Fitness", "Jadedness", "Injured until", "Traits", "Moves", "Born", "Left Foot Rating",
        "Right Foot Rating", "Home Reputation", "World Reputation"}
# read from a save game: FM's hidden attributes and personality (never scored, shown on a player's details)
HIDDEN_COLS = ["Consistency", "Dirtiness", "Important Matches", "Injury Proneness", "Versatility", "Adaptability",
               "Ambition", "Loyalty", "Pressure", "Professionalism", "Sportsmanship", "Temperament", "Controversy"]
META |= set(HIDDEN_COLS)  # every other column is an attribute
STAFF_META = {"Name", "Club", "Age", "Job", "Personality", "Current Ability", "Potential Ability", "Tactical style",
              "Born", "Wage", "Contract", "Deal", "Reputation"}  # a staff file's other columns are attributes
LEFT_FOOTED = {"Very Strong", "Strong", "Fairly Strong"}  # older views' Left Foot this good or better: Foot = Left


def foot_word(preferred):
    """FM's Preferred Foot ("Left-Footed", "Right-Footed Only", "Either") -> Left, Right or Either."""
    p = preferred.strip().lower()
    return "Either" if p.startswith("either") else "Left" if p.startswith("left") else "Right" if p.startswith("right") else None
MIN_KNOWN_WEIGHT = 0.85  # a player is scored at a position when attributes carrying 85% of its weight are known (FM shows "-" for unscouted ones)
PARTIAL_SQUAD = re.compile(r"-(u\d+s?|reserves)$")  # an export of only part of the club (U18s, U21s, reserves)
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
        values, problems = {}, []
        for chunk in re.split(r"[,;\n](?!\d)", body):  # a comma straight before a digit is a decimal comma
            if not chunk.strip():
                continue
            m = ATTRIBUTE_PAIR.fullmatch(chunk.strip())
            if not m:
                problems.append(f"can't read “{chunk.strip()[:40]}”: write an attribute and its weight, like Pace 20")
                continue
            name, v = m.group(1).strip(), float(m.group(2).replace(",", "."))
            if name.lower() in {k.lower() for k in values}:
                problems.append(f"{name} is listed twice")
            if v != 0 and not 1 <= v <= 20:
                problems.append(f"{name}: {v:g} is out of range (1 to 20, or 0 for doesn't count)")
            values[name] = v
        if known:
            import difflib
            lower = {k.lower(): k for k in known}
            for name in values:
                if name.lower() not in lower:
                    close = difflib.get_close_matches(name.lower(), list(lower), n=1, cutoff=0.6)
                    problems.append(f"unknown attribute “{name}”" + (f": did you mean {lower[close[0]]}?" if close else ": check the spelling"))
        if problems:  # every problem at once, so the file can be fixed in one go
            raise ValueError("the file has " + (f"{len(problems)} problems" if len(problems) > 1 else "a problem") + ":\n• "
                             + "\n• ".join(problems[:12]) + (f"\n…and {len(problems) - 12} more" if len(problems) > 12 else "")
                             + "\nThe format: an attribute and its weight each, separated by commas, e.g. Pace 20, Acceleration 19, Stamina 11")
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


FM_ORDER = {}  # snapshot stamp -> FM's date in that save game (build() fills it from fm_dates.json)


def by_fm(stamp):
    """A snapshot's place in time: FM's date in the save game, not the file's time (a save game read later can be an
    older game); a snapshot without FM's date goes by its file's date."""
    return FM_ORDER.get(stamp) or stamp[:10], stamp


def carry_forward(dates):
    """The observation dates in FM's order, each with every attribute's newest value so far: one FM couldn't show on a
    later screen keeps the value seen before."""
    newest = {}
    for stamp in sorted(dates, key=by_fm):
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
    scores, get = {}, attrs.get
    for key, w in groups.items():
        total = known = s = 0.0  # one pass: fast with tens of thousands of players
        for a, x in w.items():
            total += x
            v = get(a)
            if v is not None:
                known += x
                s += v * x
        if known and known >= min_known * total:
            scores[key] = s / known
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
    if re.fullmatch(r"(\d+,){14}\d+", row.get("Familiarity") or ""):  # from a save game: FM's 15 position ratings
        facts["fam"] = dict(zip(FM_POSITIONS, map(int, row["Familiarity"].split(","))))
    if (row.get("Height") or "").isdigit():  # from a save game: cm
        facts["height"] = int(row["Height"])
    if row.get("Contract"):  # from a save game: the contract's end date, YYYY-MM-DD
        facts["contract"] = row["Contract"]
    if "Deal" in row:  # a save game: FM's contract status (Full-time, Part-time, Non-contract, Amateur); then a blank
        facts["deal"] = row["Deal"] or None  # Contract is news too: no end date (any more)
        facts["contract"] = row.get("Contract") or None
    if re.fullmatch(r"-?\d+", row.get("Current Ability") or ""):  # from a save game: FM's hidden ability
        facts["ca"] = int(row["Current Ability"])
        facts["pa"] = int(row["Potential Ability"]) if re.fullmatch(r"-?\d+", row.get("Potential Ability") or "") else None
    if re.fullmatch(r"\d+(\.\d+)?", row.get("Fitness") or ""):  # from a save game: today's fitness and match exercise, %
        facts["state"] = {"fitness": float(row["Fitness"]), "exercise": float(row.get("Match Exercise") or 0),
                          "jadedness": int(row["Jadedness"]) if re.fullmatch(r"-?\d+", row.get("Jadedness") or "") else None}
    personal = {k: row[c] for k, c in (("born", "Born"), ("leftFoot", "Left Foot Rating"), ("rightFoot", "Right Foot Rating"),
                                         ("repHome", "Home Reputation"), ("rep", "Reputation"), ("repWorld", "World Reputation"))
                if row.get(c)}
    if personal:  # a save game: birth date, foot ratings (1-20), reputations (0-10000)
        facts["personal"] = {k: v if k == "born" else int(v) for k, v in personal.items() if k == "born" or v.isdigit()}
    if "Injured until" in row:  # a save game: every row has it, blank when he's fit (so an old injury doesn't stay)
        facts["injured"] = row["Injured until"] or None
    if row.get("Traits"):  # a save game: his preferred moves (the ones named so far)
        facts["traits"] = row["Traits"].split("; ")
    hidden = {k: int(row[k]) for k in HIDDEN_COLS if (row.get(k) or "").isdigit()}
    if hidden:
        facts["hidden"] = hidden
    if "Loan" in row:  # squad screens only: in, out, trial, or blank for none
        facts["loan"] = row["Loan"] or None
    if "Team" in row:  # a save game: the club's team he's in (First team, Under-21s, Under-18s, B team…), blank on loan
        facts["team"] = row["Team"] or None
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


def latest_staff_search(staff):
    """The names in the newest staff search import (a search-… staff file): Research › Staff compares with it when the
    latest staff import was your own club's staff screen."""
    def when(f):
        m = re.search(r"-(\d\d-\d\d-\d\d)$", Path(f).stem)
        return f[:10], m.group(1) if m else ""
    searches = {f for person in staff.values() for f in person["files"] if "_search-" in f}
    if not searches:
        return set()
    newest = max(searches, key=when)
    return {name for name, person in staff.items() if newest in person["files"]}


def age_benchmark(w):
    """age -> the score expected at that age (ages outside the table count as its youngest or oldest)."""
    table = {int(age): value for age, value in w["age_benchmark"].items()}
    youngest, oldest = min(table), max(table)
    return lambda age: table[min(max(age, youngest), oldest)]


MENS_MEDIAN = 11.89  # the median best score of players aged 24-30 in a big men's save (the whole game, these weights)


def womens_vs_age(rows, w):
    """Women's football: the vs Age table is set for the men's game, where scores run a little higher. Scale it by the
    save's own median (aged 24-30) against the men's, and each player's vs Age with it."""
    v = sorted(r["score"] for r in rows if r.get("score") and 24 <= (r.get("age") or 0) <= 30)
    if len(v) < 200:
        return
    ratio = min(max(v[len(v) // 2] / MENS_MEDIAN, 0.8), 1.1)
    w["age_benchmark"] = {k: round(x * ratio, 2) for k, x in w["age_benchmark"].items()}
    for r in rows:
        for h in [r, *r["history"]]:
            if h.get("vsAge") is not None:
                h["vsAge"] /= ratio


def player_row(name, person, w):
    """One player: his newest scores and details, and his history (one entry per date with enough known to score
    him). None when no date has enough."""
    groups = {"GK": w["gk"]} if person["keeper"] else w["outfield"]
    expected = age_benchmark(w)
    facts = {"leftFoot": None, "wage": None, "value": (None, None), "loan": None, "team": None, "deal": None, "position": None, "height": None,
             "contract": None, "ca": None, "pa": None, "hidden": None, "fam": None, "state": None,
             "injured": None, "traits": None, "personal": None}
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
                        "ca": seen.get("ca"),
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
               loan=facts["loan"], team=facts["team"], deal=facts["deal"], wage=facts["wage"], valueLo=facts["value"][0], value=facts["value"][1],
               height=facts["height"], contract=facts["contract"], ca=facts["ca"], pa=facts["pa"],
               hidden=facts["hidden"], fam=facts["fam"], state=facts["state"], injured=facts["injured"],
               traits=facts["traits"], personal=facts["personal"], history=history)
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
            col("position", "Position"), col("height", "Height", num=True), col("contract", "Contract"),
            col("ca", "CA", num=True, title="Current Ability (hidden in FM)"),
            col("pa", "PA", num=True, title="Potential Ability (hidden in FM; negative: FM's code for a range)"),
            col("best", "Best"), col("score", "Score", num=True, fmt="f1"),
            col("vsAge", "vs Age", num=True, fmt="pct"),
            *((col("top", "Top %", num=True, title="where his best score ranks among everyone imported at that position: top 5% = better than 95% of them"),
               col("perPoint", "Value per point", num=True, fmt="money",
                   title="his value (the top of FM's range) per point of his best score: lower = cheaper for what he brings"),
               col("rating", "Rating", num=True, fmt="f2")) if with_club else ())]


def market_ranks(rows):
    """Each row's top % at his best position among everyone given (1 = the best of them) and value per score point."""
    by = {}
    for r in rows:
        if r.get("score") is not None:
            by.setdefault(r["best"], []).append(r["score"])
    for scores in by.values():
        scores.sort()  # ascending, for bisect: how many score more = all − those at or below
    for r in rows:
        scores = by.get(r["best"])
        if scores and r.get("score") is not None:
            better = len(scores) - bisect.bisect_right(scores, r["score"])
            r["top"] = max(1, round(100 * (better + 1) / len(scores)))
        r["perPoint"] = r["value"] / r["score"] if r.get("value") and r.get("score") else None


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
                        "attrs": {a: v for a, v in attrs.items() if a in shown}})
    return {"positions": list(weights), "players": players, "tops": tops, "managed": cfg["managed_club"],
            "division": cfg.get("division"), "weights": weights, "groups": ATTRIBUTE_GROUPS}


def staff_research(staff_rows, club, roles, names, last=frozenset()):
    """Research › Staff: the Research page's data with jobs for positions (charts.html's staff mode): every staff member,
    yours flagged, with his job scores; each job's cover (one person per job, staff_cover) as "your best" there."""
    mine = [r for r in staff_rows if r["club"] == club]
    cover = staff_cover(mine, roles)
    people = [{"name": r["name"], "club": r["club"], "age": r.get("age"), "job": r.get("job"), "personality": r.get("personality"),
               "squad": r["club"] == club, "last": r["name"] in last, "best": r.get("best"),
               "scores": {k: round(r[k], 2) for k in roles if r.get(k) is not None}, "attrs": {}} for r in staff_rows]
    jobs = [k for k in roles if k != YOU_ROLE]  # the manager's job is yours: not researched
    return {"kind": "staff", "positions": jobs, "names": {k: names.get(k, k) for k in jobs}, "players": people,
            "cover": {k: r["name"] for k, r in cover.items()}, "you": YOU_ROLE, "managed": club,
            "tops": {}, "weights": {}, "groups": {}}


def compare_page(rows, squad, w, cfg):
    """Players › Compare: everyone (yours flagged, first), with what the page sets side by side: position scores, the
    newest attributes, where he plays and his score over time. The page keeps the two picked (by name)."""
    positions = ["GK", *w["outfield"]]
    mine = {id(r) for r in squad}
    players = [{"name": r["name"], "club": r["club"], "age": r["age"], "wage": r["wage"], "value": r["value"],
                "best": r["best"], "score": r["score"], "vsAge": r["vsAge"], "squad": id(r) in mine, "position": r["position"],
                "ca": r.get("ca"), "pa": r.get("pa"), "contract": r.get("contract"),
                "hidden": r.get("hidden"),
                "plays": r.get("plays") or NEAR.get(r["best"], [r["best"]]),
                "scores": {g: r[g] for g in positions if r.get(g) is not None},
                "attrs": {a: v for a, v in r["history"][-1]["attrs"].items() if v is not None},
                "history": [{"date": h.get("season") or h["date"], "score": h["score"], "best": h["best"]} for h in r["history"]]} for r in rows]
    players.sort(key=lambda p: (not p["squad"], p["name"]))
    return {"players": players, "positions": positions, "groups": ATTRIBUTE_GROUPS, "managed": cfg["managed_club"]}


def fm_positions(text):
    """FM's position text ("D/WB (R), M (RC)", "AM (RL), ST (C)", "GK") -> the weights' position groups, in order."""
    return list(_fm_positions(text or ""))


@functools.lru_cache(maxsize=None)
def _fm_positions(text):  # a few hundred distinct texts among tens of thousands of players: each worked out once
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
    return tuple(groups)


WIDE_ROLES = {"D": "DL/DR", "WB": "WBL/WBR", "M": "ML/MR", "AM": "AML/AMR"}
WIDE_GROUPS = set(WIDE_ROLES.values())


def fm_sides(text):
    """FM's position text -> the sides he plays at the wide positions: "D/WB (R), AM (RL)" -> {"DL/DR": "R",
    "WBL/WBR": "R", "AML/AMR": "LR"}. Empty without the Position column (the foot decides then)."""
    return dict(_fm_sides(text or ""))


@functools.lru_cache(maxsize=None)
def _fm_sides(text):
    out = {}
    for part in (text or "").split(","):
        m = re.match(r"\s*([A-Z/]+)\s*(?:\(([RLC]+)\))?", part)
        sides = "".join(c for c in (m.group(2) or "") if c in "LR") if m else ""
        for role in (m.group(1).split("/") if m and sides else []):
            if role in WIDE_ROLES:
                g = WIDE_ROLES[role]
                out[g] = "".join(sorted(set(out.get(g, "") + sides)))
    return tuple(out.items())





# Formations, as the weights' position groups (a wide pair counts twice: one left, one right).
# The formations, by back line: back four, back three, back five. "Forwards" are strikers, "wingers" AM (L/R); D (L/R)
# full-backs, WB wing-backs, M (L/R) wide midfield, M (C) centre-mids, AM (C) tens, DM holders; 3-4-3-0 has no striker.
# 3-5-2 and 5-3-2 have the same positions (wing-backs, a midfield three), so they rank the same.
FORMATIONS = [
    # back four
    ("4-4-2", "A flat midfield four, two strikers", {"DL/DR": 2, "CD": 2, "ML/MR": 2, "MC": 2, "ST": 2}),
    ("4-4-2 Diamond Narrow", "A holder, two centre-mids, a ten, two strikers", {"DL/DR": 2, "CD": 2, "DM": 1, "MC": 2, "AMC": 1, "ST": 2}),
    ("4-4-2 Diamond Wide", "A holder, two wide midfielders, a ten, two strikers", {"DL/DR": 2, "CD": 2, "DM": 1, "ML/MR": 2, "AMC": 1, "ST": 2}),
    ("4-4-1-1", "A flat midfield four, a ten behind a lone striker", {"DL/DR": 2, "CD": 2, "ML/MR": 2, "MC": 2, "AMC": 1, "ST": 1}),
    ("4-3-3", "Three centre-mids behind three strikers", {"DL/DR": 2, "CD": 2, "MC": 3, "ST": 3}),
    ("4-3-2-1", "Christmas tree: three centre-mids, two tens, a striker", {"DL/DR": 2, "CD": 2, "MC": 3, "AMC": 2, "ST": 1}),
    ("4-3-1-2", "Three centre-mids, a ten behind two strikers", {"DL/DR": 2, "CD": 2, "MC": 3, "AMC": 1, "ST": 2}),
    ("4-2-3-1", "Two centre-mids, wingers and a ten, a striker", {"DL/DR": 2, "CD": 2, "MC": 2, "AML/AMR": 2, "AMC": 1, "ST": 1}),
    ("4-2-2-2", "Two deeper midfielders, two tens, two strikers", {"DL/DR": 2, "CD": 2, "MC": 2, "AMC": 2, "ST": 2}),
    ("4-2-4", "Two centre-mids, two wingers, two strikers", {"DL/DR": 2, "CD": 2, "MC": 2, "AML/AMR": 2, "ST": 2}),
    ("4-1-4-1", "A holder behind a flat midfield four, a striker", {"DL/DR": 2, "CD": 2, "DM": 1, "ML/MR": 2, "MC": 2, "ST": 1}),
    ("4-1-2-3 DM Wide", "A holder, two centre-mids, two wingers, a striker", {"DL/DR": 2, "CD": 2, "DM": 1, "MC": 2, "AML/AMR": 2, "ST": 1}),
    # back three
    ("3-5-2", "Wing-backs, three centre-mids, two strikers", {"CD": 3, "WBL/WBR": 2, "MC": 3, "ST": 2}),
    ("3-4-3 Wide", "A midfield four, three strikers", {"CD": 3, "ML/MR": 2, "MC": 2, "ST": 3}),
    ("3-4-3 Diamond", "A holder, wide midfielders, a ten, three strikers", {"CD": 3, "DM": 1, "ML/MR": 2, "AMC": 1, "ST": 3}),
    ("3-4-2-1", "Two centre-mids, two wide, two tens, a striker", {"CD": 3, "ML/MR": 2, "MC": 2, "AMC": 2, "ST": 1}),
    ("3-4-1-2", "A midfield four, a ten behind two strikers", {"CD": 3, "ML/MR": 2, "MC": 2, "AMC": 1, "ST": 2}),
    ("3-2-4-1", "A double pivot, wingers and two tens, a striker", {"CD": 3, "DM": 2, "AML/AMR": 2, "AMC": 2, "ST": 1}),
    ("3-2-3-2", "A double pivot, three attacking mids, two strikers", {"CD": 3, "DM": 2, "AML/AMR": 2, "AMC": 1, "ST": 2}),
    ("3-1-4-2", "A holder, a midfield four, two strikers", {"CD": 3, "DM": 1, "ML/MR": 2, "MC": 2, "ST": 2}),
    ("3-4-3-0", "Strikerless: wing-backs, two centre-mids, wingers and a ten", {"CD": 3, "WBL/WBR": 2, "MC": 2, "AML/AMR": 2, "AMC": 1}),
    # back five
    ("5-4-1", "Wing-backs, a midfield four, a striker", {"CD": 3, "WBL/WBR": 2, "ML/MR": 2, "MC": 2, "ST": 1}),
    ("5-3-2", "Wing-backs, three centre-mids, two strikers", {"CD": 3, "WBL/WBR": 2, "MC": 3, "ST": 2}),
    ("5-2-3", "Wing-backs, two centre-mids, three strikers", {"CD": 3, "WBL/WBR": 2, "MC": 2, "ST": 3}),
    ("5-2-2-1", "Wing-backs, two centre-mids, two tens, a striker", {"CD": 3, "WBL/WBR": 2, "MC": 2, "AMC": 2, "ST": 1}),
    ("5-2-1-2", "Wing-backs, two centre-mids, a ten, two strikers", {"CD": 3, "WBL/WBR": 2, "MC": 2, "AMC": 1, "ST": 2}),
]
FORMATION_ORDER = ["GK", "CD", "DL/DR", "WBL/WBR", "DM", "MC", "ML/MR", "AMC", "AML/AMR", "ST"]
LINES = {"Defence": {"CD", "DL/DR", "WBL/WBR"}, "Midfield": {"DM", "MC", "ML/MR"}, "Attack": {"AMC", "AML/AMR", "ST"}}  # Formations' lines
# Without FM's positions, a player's best position (from attributes) and the ones next to it: so a midfielder isn't put
# at full-back just because his attributes would score there
NEAR = {"GK": ["GK"], "CD": ["CD", "DL/DR", "DM"], "DL/DR": ["DL/DR", "WBL/WBR", "CD"], "WBL/WBR": ["WBL/WBR", "DL/DR", "ML/MR"],
        "DM": ["DM", "MC", "CD"], "MC": ["MC", "DM", "AMC"], "ML/MR": ["ML/MR", "AML/AMR", "WBL/WBR", "MC"],
        "AMC": ["AMC", "MC", "AML/AMR", "ST"], "AML/AMR": ["AML/AMR", "ML/MR", "AMC", "ST", "MC"], "ST": ["ST", "AMC", "AML/AMR"]}


OFF_SIDE = 1.5  # a wide slot: someone who doesn't play that side counts this much lower when picking (his score stays)
# From a save game, FM's 15 position ratings (1-20): 15+ he plays it (no change), 13-14 accomplished-ish and 10-12
# competent count these many points lower when picking an XI, under 10 he isn't picked there.
FAMILIARITY_PENALTY = [(15, 0.0), (13, 0.4), (10, 1.0)]
FM_POSITIONS = ["GK", "SW", "DL", "DC", "DR", "DM", "ML", "MC", "MR", "AML", "AMC", "AMR", "ST", "WBL", "WBR"]
GROUP_POSITIONS = {"GK": ["GK"], "CD": ["DC"], "DL/DR": ["DL", "DR"], "WBL/WBR": ["WBL", "WBR"], "DM": ["DM"], "MC": ["MC"],
                   "ML/MR": ["ML", "MR"], "AMC": ["AMC"], "AML/AMR": ["AML", "AMR"], "ST": ["ST"]}


def familiarity(r, group, side=None):
    """FM's rating (1-20) of r at a position group (a wide one: that side, else his better side); None without a save."""
    fam = r.get("fam")
    if not fam:
        return None
    names = GROUP_POSITIONS.get(group, [])
    if side and len(names) == 2:
        names = [names[0] if side == "L" else names[1]]
    return max((fam.get(n, 0) for n in names), default=0)


def fam_penalty(r, group, side=None):
    """Points off his score when picked at a group he's less familiar with (0 at 15+); None: not there at all."""
    v = familiarity(r, group, side)
    if v is None:
        return 0.0
    return next((off for floor, off in FAMILIARITY_PENALTY if v >= floor), None)


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
    def pick(r, slot):  # his score there as picked: less on the wrong side or at a position he's less familiar with
        pos, _, side = slot.partition("|")
        if r.get("fam"):
            off = fam_penalty(r, pos, side or None)
            return None if off is None else r[pos] - off
        return r[pos] - (OFF_SIDE if side and not side_ok(r, pos, side) else 0)
    pairs = sorted(((v, i, slot) for i, r in enumerate(players) for slot in need
                    if r.get(slot.partition("|")[0]) is not None and plays(r, slot.partition("|")[0])
                    and (v := pick(r, slot)) is not None), reverse=True)
    used, xi = set(), []
    for v, i, slot in pairs:
        if left[slot] and i not in used:
            used.add(i); left[slot] -= 1
            xi.append((slot, players[i], v))
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
                            "sides": fm_sides(r.get("position")), "side": sd,
                            "off": bool(sd) and (not side_ok(r, pos, sd) if not r.get("fam") else (familiarity(r, pos, sd) or 0) < 15),
                            "fam": familiarity(r, pos, sd), "warn": warnings(r)}
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
    available = [r for r in squad if (r["loan"] != "out" or r["name"] in pinned) and not r.get("injured")]  # fit, here

    def plays(r, pos):  # a save game: FM's rating there 10+; FM's own positions when an export showed them; else his
        if r.get("fam"):  # best position and its neighbours
            return (familiarity(r, pos) or 0) >= 10
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
                    job=row.get("Job") or None, personality=row.get("Personality") or None,
                    style=dict((k, int(v)) for k, _, v in (p.partition(":") for p in row["Tactical style"].split(";")) if v.isdigit())
                          if row.get("Tactical style") else None,
                    ca=int(row["Current Ability"]) if (row.get("Current Ability") or "").isdigit() else None,
                    pa=int(row["Potential Ability"]) if re.fullmatch(r"-?\d+", row.get("Potential Ability") or "") else None,
                    born=row.get("Born") or None, wage=parse_wage(row["Wage"]) if row.get("Wage") else None,
                    contract=row.get("Contract") or None, deal=row.get("Deal") or None,
                    reputation=int(row["Reputation"]) if (row.get("Reputation") or "").isdigit() else None)
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
    newest = person["dates"][max(person["dates"], key=by_fm)]
    row.update(name=name, job=job, personality=personality, ca=newest.get("ca"), pa=newest.get("pa"),
               style=newest.get("style"), born=newest.get("born"), wage=newest.get("wage"),
               contract=newest.get("contract"), deal=newest.get("deal"), reputation=newest.get("reputation"), history=history)
    return row


# FM's club staff screen: its tabs with attributes (Contract has none), each exported with F8 on its own
STAFF_TABS = {"Coaching": ["Attacking", "Defending", "Fitness", "Goalkeeping", "Possession", "Set Pieces", "Tactical", "Technical",
                           "Working With Youngsters"],
              "Medical": ["Physiotherapy", "Sports Science"],
              "Mental": ["Adaptability", "Authority", "Determination", "Motivating", "People Management"],
              "Scouting": ["Analysing Data", "Judging Player Ability", "Judging Player Potential", "Judging Staff Ability", "Negotiating", "Tactical Knowledge"]}


def staff_tabs(staff_dir, club):
    """The newest export day of your club's staff screen: {date, tabs: {tab: True when that day's file has its attributes}}."""
    slug = re.sub(r"[^a-z0-9]+", "-", club.lower()).strip("-") + "-staff"
    files = sorted(staff_dir.glob(f"*_{slug}.csv")) if staff_dir.is_dir() else []
    if not files:
        return {"date": None, "tabs": {t: False for t in STAFF_TABS}, "people": 0}
    with open(files[-1], newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    filled = {k for r in rows for k, v in r.items() if v}
    return {"date": files[-1].name[:10], "people": len(rows), "tabs": {t: any(a in filled for a in attrs) for t, attrs in STAFF_TABS.items()}}


STAFF_SHOW_MAX = 3000  # staff from other clubs shown on the staff pages (the best by score); yours always
SHOW_MAX = 8000  # imported players shown on the big pages; above it the lowest scored are left off them


def keep_best(rows, cap=SHOW_MAX):
    """The best `cap` rows by score, and a note for the page's info line ("" when nothing is left off)."""
    if len(rows) <= cap:
        return rows, ""
    best = sorted(rows, key=lambda r: r.get("score") or 0, reverse=True)[:cap]
    floor = best[-1].get("score") or 0
    return best, f" · showing the best {cap:,} (scores from {floor * 5:.1f}); {len(rows) - cap:,} below are left off to keep it quick"


YOU_ROLE = "Mgr"  # the manager's job is yours: nobody of your staff is put there in the one-each cover


def staff_cover(mine, roles):
    """Your staff, one person per job (like a formation's XI): the best person-job pair first, each person and job once,
    the manager's job left to you. {role: person row} for the jobs someone covers."""
    pairs = sorted(((r[role], i, role) for i, r in enumerate(mine) for role in roles if role != YOU_ROLE and r.get(role) is not None),
                   reverse=True)
    cover, used = {}, set()
    for score, i, role in pairs:
        if role not in cover and i not in used:
            cover[role] = mine[i]
            used.add(i)
    return cover


def staff_jobs(staff_rows, club, roles, names):
    """Each job: {role, name, you (the manager's job), cover: [name, score, job] (one person per job, staff_cover),
    yours: [[name, score, job], 2 best, whatever else they cover], lists: [name, club, score, age] best elsewhere}."""
    mine = [r for r in staff_rows if r["club"] == club]
    rest = [r for r in staff_rows if r["club"] != club]
    cover = staff_cover(mine, roles)
    def top(rows, role, n):
        return sorted((r for r in rows if r.get(role) is not None), key=lambda r: r[role], reverse=True)[:n]
    def short(r, role):
        return [r["name"], r["club"], round(r[role], 2), r.get("age")]
    out = []
    for role in roles:
        if role == YOU_ROLE:  # the manager's job is yours
            continue
        c = cover.get(role)
        out.append({"role": role, "name": names.get(role, role), "you": role == YOU_ROLE,
                    "cover": [c["name"], round(c[role], 2), c.get("job")] if c else None,
                    "yours": [[r["name"], round(r[role], 2), r.get("job")] for r in top(mine, role, 2)],
                    "lists": next((short(r, role) for r in top(rest, role, 1)), None)})
    return out


def staff_cols(roles, names=None):
    """The staff tables' columns: who, then a score per job (its full name as the title, [staff_names])."""
    def col(key, label, **more):
        return {"key": key, "label": label, **more}
    return [col("name", "Name"), col("club", "Club"), col("age", "Age", num=True), col("job", "Job"),
            col("personality", "Personality"), col("ca", "CA", num=True, title="Current Ability (hidden in FM)"),
            col("pa", "PA", num=True, title="Potential Ability (hidden in FM)"), *(col(role, role, num=True, fmt="f1", title=(names or {}).get(role, role)) for role in roles)]


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
                                    "next": next_season(attrs_of(r), r["age"], pos_w),
                                    "weak": weak_link(attrs_of(r), pos_w)})
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


def weak_link(attrs, pos_w, top=4):
    """His weakest of the position's most weighted attributes: [attribute, value], or None. One weak key attribute
    hurts more on the pitch than an average shows."""
    keys = [a for a, _ in sorted(pos_w.items(), key=lambda kv: -kv[1]) if a in attrs][:top]
    if not keys:
        return None
    a = min(keys, key=lambda k: attrs[k])
    return [a, attrs[a]]


def read_finances(save_dir):
    """finances.json (the last save game read): budgets, the file's date and FM's date; {} when there's none."""
    try:
        return json.loads((save_dir / "finances.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def squad_roles(lineup):
    """name -> Starter / Backup / Third / Youth, as the Lineup places your players."""
    roles = {}
    for s in lineup["slots"]:
        for i, col in enumerate(s["senior"]):
            for p in col:
                roles.setdefault(p["name"], ["Starter", "Backup", "Third"][min(i, 2)])
        for col in s["extra"].values():
            for p in col:
                roles.setdefault(p["name"], "Youth")
    return roles


def development_factor(r):
    """How likely a youngster grows towards his potential, 1-20, from what drives it in FM: Professionalism most,
    then Determination and Ambition (2:1:1). None when the save game hasn't given his personality."""
    h = r.get("hidden") or {}
    det = r["history"][-1]["attrs"].get("Determination")
    if "Professionalism" not in h or "Ambition" not in h or det is None:
        return None
    return (2 * h["Professionalism"] + h["Ambition"] + det) / 4


def warnings(r):
    """Short warnings from the hidden attributes: ["inconsistent", "big games", "injury-prone"] (save games only)."""
    h = r.get("hidden") or {}
    out = []
    if r.get("injured"):
        out.append(f"injured until {r['injured']}")
    if h.get("Consistency", 20) <= 7:
        out.append("inconsistent")
    if h.get("Important Matches", 20) <= 7:
        out.append("big games")
    if h.get("Injury Proneness", 0) >= 14:
        out.append("injury-prone")
    state = r.get("state") or {}
    if state.get("fitness") is not None and state["fitness"] < 85:
        out.append(f"fitness {state['fitness']:.0f}%")
    return out


def youth_intakes(rows, managed, dates):
    """Squad › Youth's intakes: every youngster who first turned up at the club aged 16 or under after the first save
    read (FM's youth intake, or a young signing), by FM's season, and what became of him: still here, moved on, or no
    longer in the game. Players already there at the first read can't be told apart, so they aren't listed."""
    when = lambda stamp: dates.get(stamp, stamp)  # FM's date, not the file's: a save made later can be an older game
    ours = sorted({h["date"] for r in rows for h in r["history"] if h.get("club") == managed}, key=when)
    if not ours:
        return {"since": None, "seasons": []}
    first, newest = ours[0], max((h["date"] for r in rows for h in r["history"]), key=when)

    def season(stamp):
        d = dates.get(stamp)
        if not d:
            return None
        y = int(d[:4]) if int(d[5:7]) >= 7 else int(d[:4]) - 1
        return f"{y}/{str(y + 1)[2:]}"

    def intake_year(stamp):
        """The intake he came in. Each country's intake is on a fixed day, from January to early October and never in
        November or December (most of Europe in March; calendar-year countries in September). So he came between the
        read before and this one: the same year when both are in it; first seen in January or February after a read
        made by October, last autumn's intake (Brazil and Uruguay's January ones aside); else the year he was seen."""
        now = when(stamp)
        before = [d for d in map(when, ours) if d < now]
        prev = max(before) if before else None
        if prev and prev[:4] < now[:4] and int(now[5:7]) <= 2 and int(prev[5:7]) <= 10:
            return prev[:4]
        return now[:4]

    by = {}
    for r in rows:
        at = sorted((h for h in r["history"] if h.get("club") == managed), key=lambda h: when(h["date"]))
        if not at or at[0]["date"] == first or (at[0].get("age") or 99) > 16:
            continue
        last = max(r["history"], key=lambda h: when(h["date"]))  # where the newest game (by FM's date) has him
        status = "gone" if when(last["date"]) < when(newest) else "here" if last.get("club") == managed else "moved"
        by.setdefault(intake_year(at[0]["date"]), []).append({
            "name": r["name"], "joined": at[0].get("age"), "age": last.get("age") or r["age"], "status": status,
            "club": last.get("club") if status == "moved" else None, "score": r["score"], "best": r["best"],
            "ca": r.get("ca"), "pa": r.get("pa")})
    seasons = [{"season": s, "players": sorted(ps, key=lambda p: (p["status"] != "here", -(p["score"] or 0)))}
               for s, ps in sorted(by.items(), reverse=True)]
    return {"since": season(first) or first[:10], "seasons": seasons}


def squad_tabs(squad, lineup, cfg, save_dir, youth_age=21):
    """The data of Squad › Depth, Youth and Contracts: each player's role on the Lineup, his contract, ability,
    personality and warnings (save games give most of it), the formations (Depth draws XI vs second XI), the targets
    on your lists, and the club's money."""
    money = read_finances(save_dir)
    roles = squad_roles(lineup)
    people = {}
    for r in squad:
        h = r.get("hidden") or {}
        people[r["name"]] = {
            "name": r["name"], "age": r["age"], "best": r["best"], "score": r["score"], "vsAge": r["vsAge"],
            "role": roles.get(r["name"], "Youth" if (r["age"] or 99) <= youth_age else "Third"), "loan": r["loan"], "team": r.get("team"), "deal": r.get("deal"),
            "wage": r["wage"], "value": r["value"], "contract": r.get("contract"), "ca": r.get("ca"), "pa": r.get("pa"),
            "dev": development_factor(r), "warn": warnings(r),
            "traits": {k: h[k] for k in ("Professionalism", "Ambition", "Consistency", "Important Matches",
                                         "Injury Proneness") if k in h},
            "det": r["history"][-1]["attrs"].get("Determination"),
            "state": r.get("state"), "injured": r.get("injured"), "natfit": r["history"][-1]["attrs"].get("Natural Fitness"),
            "mstats": r.get("mstats"),
            "position": r.get("position")}
    return {"people": people, "formations": lineup["formations"], "targets": lineup["targets"],
            "money": money, "today": money.get("game_date"), "youthAge": youth_age, "players": {r["name"]: r for r in squad}}


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


CHECKPOINT_ORDER = []  # build(): the checkpoints' labels, oldest first
CHECKPOINT_META = {}  # label -> (first / mid / end / now, the season it belongs to)
FM_DATES = "fm_dates.json"  # in a save: each save game read (its file time, the snapshot's stamp) -> FM's date in it


def fm_dates(save_dir, config, stamps):
    """{stamp: FM's date (ISO)} for the snapshots. A stamp read before this was kept is looked up once in FM's games
    folder (a save game whose file time is that stamp), then kept in fm_dates.json."""
    path = save_dir / FM_DATES
    try:
        dates = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dates = {}
    missing = {s for s in stamps if s not in dates and "T" in s}
    if missing:
        try:
            import fm_save  # Python 3.14+; without it, those snapshots go by the file's date
            tail = Path("Sports Interactive", "Football Manager 27", "games")
            folders = [Path(config["games_dir"]).expanduser()] if config.get("games_dir") else []
            folders += [d / tail for d in (documents(), Path.home() / "OneDrive" / "Documents", Path.home() / "Documents")]
            found = False
            for folder in folders:
                for fm in (folder.glob("*.fm") if folder.is_dir() else []):
                    stamp = time.strftime("%Y-%m-%dT%H%M", time.localtime(fm.stat().st_mtime))
                    if stamp in missing:
                        data, sections = fm_save.open_save(fm)
                        dates[stamp] = fm_save.summary(data, sections)["date"].isoformat()
                        found = True
            if found:
                path.write_text(json.dumps(dates, indent=1, sort_keys=True), encoding="utf-8")
        except Exception as e:  # noqa: BLE001 (an old file, a moved folder: those go by the file's date)
            print(f"[{save_dir.name}] couldn't look up FM's dates: {e}")
    return dates


MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()


def checkpoints(stamps, dates):
    """The development checkpoints, by FM's date in each save game read: 31 Dec (mid-season, a six-month look) and
    30 Jun (the season's end, kept for history), each filled by the first save read on or after it, within a month (a
    save of 30 Dec doesn't count for 31 Dec), plus the newest save as Now. Returns ({stamp: label},
    [labels, oldest first])."""
    fm = {st: (dates.get(st) or st[:10]) for st in stamps}
    if not fm:
        return {}, []
    years = range(int(min(fm.values())[:4]) - 1, int(max(fm.values())[:4]) + 2)
    nice = lambda d: f"{int(d[8:10])} {MONTHS[int(d[5:7]) - 1]} {d[:4]}"
    # (the day, the last day a save still counts for it: a month later, label)
    points = sorted([(f"{y}-12-31", f"{y + 1}-01-31", f"31 Dec {y} · mid {y}/{str(y + 1)[2:]}") for y in years] +
                    [(f"{y}-06-30", f"{y}-07-31", f"30 Jun {y} · end of {y - 1}/{str(y)[2:]}") for y in years])
    CHECKPOINT_META.clear()
    out, order = {}, []
    season = lambda d: (lambda y, m: f"{y if m >= 7 else y - 1}/{str((y if m >= 7 else y - 1) + 1)[2:]}")(int(d[:4]), int(d[5:7]))
    for day, until, label in points:
        hit = min((st for st in fm if day <= fm[st] <= until), key=lambda st: (fm[st], st), default=None)
        if hit:
            out[hit] = label
            order.append(label)
            CHECKPOINT_META[label] = ("mid" if " mid " in label else "end", label.split(" of ")[-1] if " of " in label else label.split(" mid ")[-1])
    first = min(fm, key=lambda st: (fm[st], st))
    if first not in out and len(set(fm.values())) > 1:  # the first save read: where development starts from
        out[first] = f"First read · {nice(fm[first])}"
        order.insert(0, out[first])
        CHECKPOINT_META[out[first]] = ("first", season(fm[first]))
    newest = max(fm, key=lambda st: (fm[st], st))
    if newest not in out:
        out[newest] = f"Now · {nice(fm[newest])}"
        order.append(out[newest])
        CHECKPOINT_META[out[newest]] = ("now", season(fm[newest]))
    return out, order


def by_season(history):
    """A profile's history: the season-end checkpoints and Now (mid-season ones are for Development only)."""
    kept = [h for h in history if h.get("season") and " mid " not in h["season"]]
    return kept or history[-1:]


def development(rows, players, obs_files, managed, squad):
    """The development page: the managed club's season checkpoints (the newest save game read in each of FM's seasons),
    and for everyone in any of them their score, best position, vs Age, CA and attributes at each, for the page to
    compare two seasons."""
    squad_files = {name: f["date"] for name, f in obs_files.items() if not f["source"].startswith("search-")
                   and not PARTIAL_SQUAD.search(f["source"]) and managed in f["clubs"]}
    out, seasons = [], set()
    for r in rows:
        in_snaps = {squad_files[f] for f in players[r["name"]]["files"] if f in squad_files}
        if not in_snaps:
            continue
        at = {h["season"]: {"score": h["score"], "best": h["best"], "vsAge": h["vsAge"], "age": h["age"], "attrs": h["attrs"],
                            "ca": h.get("ca")}
              for h in r["history"] if h["date"] in in_snaps and h.get("season")}
        seasons |= set(at)
        out.append({"name": r["name"], "age": r["age"], "best": r["best"], "here": r["club"] == managed, "at": at})
    snaps = [c for c in CHECKPOINT_ORDER if c in seasons]
    # each season: from its start (the season before's end, else its first checkpoint) to its end (30 Jun; while it's on,
    # its mid-season, else now)
    by = {}
    for c in snaps:
        kind, s = CHECKPOINT_META.get(c, ("now", c))
        by.setdefault(s, []).append((kind, c))
    spans, prev_end = [], None
    for s in sorted(by):
        labels = by[s]
        rank = {"end": 3, "mid": 2, "now": 1, "first": 0}
        end = max(labels, key=lambda kc: (rank[kc[0]], snaps.index(kc[1])))
        start = prev_end if prev_end else labels[0][1]
        state = {"end": "season end", "mid": "to mid-season", "now": "so far", "first": "so far"}[end[0]]
        spans.append({"season": s, "start": start, "end": end[1], "state": state})
        prev_end = end[1] if end[0] == "end" else None
    return {"snapshots": snaps, "seasons": spans, "players": out, "rows": {r["name"]: r for r in squad}}  # rows: for the history overlay


def settings_tile(config, cfg, save):
    """Menu → About: the version, this save's main settings, where to change them, and the project page."""
    rows = [("App version", f"ElevenIndex {VERSION}"), ("Save", save), ("Club", cfg.get("managed_club", "–")), ("Attribute weights", "your own (Attributes page; every save)" if ATTRIBUTES_FILE.is_file() else "default"),
            ("Currency", config.get("currency", "–")), ("FM save games folder", config.get("games_dir") or "found under Documents")]
    table = "".join(f"<tr><td class=\"text\">{html.escape(k)}</td><td>{html.escape(str(v))}</td></tr>" for k, v in rows)
    table += (f'<tr><td class="text">Project page</td><td><a href="{PROJECT_URL}" target="_blank" rel="noopener">'
              f'{PROJECT_URL.removeprefix("https://")}</a> (download, guide, changelog)</td></tr>')
    table += (f'<tr><td class="text">Created by</td><td><b>{CREATOR}</b> · <a href="{CREATOR_URL}" target="_blank" '
              f'rel="noopener">profile on the official FM forum</a> (Sports Interactive Community)</td></tr>')
    table += (f'<tr><td class="text">On FM Scout</td><td><a href="{FMSCOUT_URL}" target="_blank" rel="noopener">'
              f'ElevenIndex on FM Scout</a></td></tr>')  # the last line
    return {"key": "", "title": "About and your settings",
            "text": f"<table>{table}</table>Switch, start or delete game profiles with <b>Menu → Manage…</b> (under the logo). Everything else is in "
                    f"<b>config.toml</b> (this computer) and <b>saves\\{html.escape(save)}\\save.toml</b> (this save) in the data "
                    f"folder: edit them with Notepad, then restart the app."}


def squad_moves(rows, cfg, managed, sources):
    """Who's at the club now. A player of yours missing from your newest full squad export has left (a youth or
    reserve squad export, PARTIAL_SQUAD, shows only part of the club); save.toml's released / signed / recalled say
    what happened since that export. Changes the rows' club and loan in place."""
    newest = by_fm(max((info["date"] for info in sources.values() if not info["source"].startswith("search-")
                        and not PARTIAL_SQUAD.search(info["source"]) and managed in info["clubs"]), key=by_fm, default=""))
    released, signed, recalled = (set(cfg.get(k, [])) for k in ("released", "signed", "recalled"))
    for r in rows:
        last_seen = by_fm(r["history"][-1]["date"])
        if r["club"] == managed and (last_seen < newest or r["name"] in released):
            r["club"] = f"left {managed}"
        if r["name"] in signed and last_seen >= newest:
            r["club"] = managed
        if r["name"] in recalled and r["loan"] == "out":
            r["loan"] = None


def latest_intake(people, sources):
    """The newest youth intake's players (a file whose source says intake), until a squad export shows them."""
    stamp = max((info["date"] for info in sources.values() if "intake" in info["source"]), key=by_fm, default="")
    in_intake = lambda f: sources[f]["date"] == stamp and "intake" in sources[f]["source"]
    in_squad_since = lambda f: by_fm(sources[f]["date"]) >= by_fm(stamp) and not sources[f]["source"].startswith("search-")
    return {name for name, person in people.items()
            if any(map(in_intake, person["files"])) and not any(map(in_squad_since, person["files"]))}


def short_money(v, symbol):
    """1500 -> £1.5K, 2100000 -> £2.1M."""
    for size, unit in ((1e6, "M"), (1e3, "K")):
        if abs(v) >= size:
            return f"{symbol}{v / size:.2f}".rstrip("0").rstrip(".") + unit
    return f"{symbol}{v:.0f}"


def budget_note(save_dir, config):
    """' · transfer budget €16.76M · wage room €350K p/w' from the last save game read (finances.json), else ''."""
    try:
        f = json.loads((save_dir / "finances.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    sym = config.get("currency", "€")
    return (f" · transfer budget {short_money(f['transfer_budget'], sym)}"
            f" · wage room {short_money(f['wage_budget'] - f['wages'], sym)} p/w (save of {f['date'][:10]})")


TOP_GROUPS = {"All players": None, "Goalkeepers": ["GK"], "Centre backs": ["CD"], "Full backs": ["DL/DR"],
              "Wing backs": ["WBL/WBR"], "Defensive midfielders": ["DM"], "Central midfielders": ["MC"],
              "Wide midfielders": ["ML/MR"], "Attacking midfielders": ["AMC"], "Wingers": ["AML/AMR"], "Strikers": ["ST"]}
SCORE_BANDS = [(0, 10), (10, 11), (11, 12), (12, 13), (13, 14), (14, 15), (15, 99)]  # Top lists' distribution


FAM_GROUP = {"GK": "GK", "SW": "CD", "DC": "CD", "DL": "DL/DR", "DR": "DL/DR", "DM": "DM", "ML": "ML/MR", "MR": "ML/MR",
             "MC": "MC", "AML": "AML/AMR", "AMR": "AML/AMR", "AMC": "AMC", "ST": "ST", "WBL": "WBL/WBR", "WBR": "WBL/WBR"}


def natural_position(r):
    """His most natural position: the one FM rates him highest at (its 15 position ratings), else his best score's."""
    fam = r.get("fam") or {}
    if fam:
        top = max(fam.values())
        groups = [FAM_GROUP[p] for p, v in fam.items() if v == top and FAM_GROUP.get(p) and r.get(FAM_GROUP[p]) is not None]
        if groups:
            return r["best"] if r["best"] in groups else max(groups, key=lambda g: r[g])
    return r["best"]


def scout_marks(rows):
    """For each row: where he ranks in the whole game at his most natural position ("rank": [pos, rank, of]), against
    everyone who plays it, each at his score there. The scout report's "In the game"."""
    plays = lambda r, g: g in (r.get("plays") or [r["best"]]) or natural_position(r) == g
    by = {}
    for r in rows:
        for g in {natural_position(r), *(r.get("plays") or [])}:
            if r.get(g) is not None and plays(r, g):
                by.setdefault(g, []).append(r[g])
    for scores in by.values():
        scores.sort()
    for r in rows:
        g = natural_position(r)
        scores = by.get(g)
        if scores and r.get(g) is not None:
            r["rank"] = [g, len(scores) - bisect.bisect_right(scores, r[g]) + 1, len(scores)]


def top_lists(rows, managed, n=25):
    """Top lists: for each position group, the best (score), the most promising (21 and under, by PA, else vs Age)
    and bargains (24 and under, a high PA, cheap: value under 1.5M or a free agent), plus how scores spread over the
    whole game and over your club (SCORE_BANDS)."""
    short = lambda r: {"name": r["name"], "club": r["club"], "age": r["age"], "best": r["best"], "position": r.get("position"),
                       "score": r["score"], "vsAge": r["vsAge"], "ca": r.get("ca"), "pa": r.get("pa"), "value": r.get("value"),
                       "mine": r["club"] == managed}
    out = {}
    for label, groups in TOP_GROUPS.items():
        pool = [r for r in rows if r.get("score") is not None and (groups is None or r["best"] in groups)]
        young = [r for r in pool if (r["age"] or 99) <= 21]
        promising = sorted(young, key=lambda r: (-(r.get("pa") or 0) if (r.get("pa") or 0) > 0 else 0, -(r["vsAge"] or 0)))
        cheap = [r for r in pool if (r["age"] or 99) <= 24 and (r.get("pa") or 0) >= 140
                 and (r["club"] == "Free agent" or r.get("value") is not None and r["value"] <= 1_500_000) and r["club"] != managed]
        bands = lambda rs: [sum(1 for r in rs if lo <= r["score"] < hi) for lo, hi in SCORE_BANDS]
        out[label] = {"best": [short(r) for r in sorted(pool, key=lambda r: -r["score"])[:n]],
                      "promising": [short(r) for r in promising[:n]],
                      "bargains": [short(r) for r in sorted(cheap, key=lambda r: (-(r.get("pa") or 0), r.get("value") or 0))[:n]],
                      "world": bands(pool), "mine": bands([r for r in pool if r["club"] == managed]),
                      "count": len(pool), "avg": sum(r["score"] for r in pool) / len(pool) if pool else None}
    return {"lists": out, "bands": [f"{lo}–{hi}" if hi < 99 else f"{lo}+" for lo, hi in SCORE_BANDS], "managed": managed}


YOUTH_COMP = re.compile(r"\bU-?1[6-9]\b|\bU-?2[0-3]\b|Sub-?\d|Youth|Juvenil|Junior|Primavera|Reserve|Academy|2 Division|"
                        r"International Cup|Under[- ]\d", re.I)


def youth_comps(competitions, ages):
    """The season's youth competitions: by name (U18, Sub-20, Primavera…), or because most players you used in it are
    20 or under (it works for any league and cup, in any country). `ages`: name -> age."""
    out = []
    for comp, players in competitions.items():
        if comp == "Friendly":
            continue
        known = sorted(ages[n] for n in players if ages.get(n) is not None)
        if YOUTH_COMP.search(comp) or (len(known) >= 3 and known[len(known) // 2] <= 20):
            out.append(comp)
    return out


def match_summary(save_dir, ages):
    """Each of your players this season, from the newest season's match stats: {name: {apps, starts, minutes, goals,
    assists, xg, rating (average), form: [last 5 ratings, oldest first], formAvg}}; first-team competitions only for
    the totals (friendlies and youth left out), every competitive match for the form."""
    files = sorted((save_dir / "stats").glob("*.json")) if (save_dir / "stats").is_dir() else []
    if not files:
        return {}
    try:
        d = json.loads(files[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    youth = set(youth_comps(d.get("competitions", {}), ages))
    for comp, players in d.get("competitions", {}).items():
        if comp in youth:  # his youth football, apart
            for name, t in players.items():
                y = out.setdefault(name, {"apps": 0, "starts": 0, "minutes": 0, "goals": 0, "assists": 0, "xg": 0.0, "rsum": 0.0, "rated": 0,
                                          "clean_sheets": 0, "conceded": 0})
                y["yapps"] = y.get("yapps", 0) + t.get("apps", 0)
                y["ymin"] = y.get("ymin", 0) + t.get("minutes", 0)
                y["yrsum"] = y.get("yrsum", 0) + t.get("rating", 0)
                y["yrated"] = y.get("yrated", 0) + t.get("rated", 0)
                y["ygoals"] = y.get("ygoals", 0) + t.get("goals", 0)
            continue
        if comp == "Friendly":
            continue
        for name, t in players.items():
            a = out.setdefault(name, {"apps": 0, "starts": 0, "minutes": 0, "goals": 0, "assists": 0, "xg": 0.0, "rsum": 0.0, "rated": 0,
                                      "clean_sheets": 0, "conceded": 0})
            for k in ("apps", "starts", "minutes", "goals", "assists", "xg", "clean_sheets", "conceded"):
                a[k] += t.get(k, 0)
            a["rsum"] += t.get("rating", 0)
            a["rated"] += t.get("rated", 0)
    for name, ms in d.get("matches", {}).items():
        played = [m for m in ms if m[2] >= 10 and 1 <= m[1] <= 10]  # 10 minutes or more
        last = played[-5:]
        a = out.setdefault(name, {"apps": 0, "starts": 0, "minutes": 0, "goals": 0, "assists": 0, "xg": 0.0, "rsum": 0.0, "rated": 0,
                                  "clean_sheets": 0, "conceded": 0})
        a["form"] = [m[1] for m in last]
        a["recent"] = sum(m[2] for m in ms[-3:])  # minutes in his club's last three matches he was in the squad for
        a["formAvg"] = round(sum(a["form"]) / len(a["form"]), 2) if a["form"] else None
    for a in out.values():
        a["rating"] = round(a.pop("rsum") / a["rated"], 2) if a["rated"] else None
        a.pop("rated")
        if a.get("yrated"):
            a["yrating"] = round(a.pop("yrsum") / a.pop("yrated"), 2)
        else:
            a.pop("yrsum", None), a.pop("yrated", None)
        a["xg"] = round(a["xg"], 2)
    out["_season"] = d.get("season")
    return out


def season_page(save_dir, squad, ages):
    """Squad › Season: every season's match stats read from save games (saves/<save>/stats/<season>.json, the newest
    read of each season), and your squad's scores beside them."""
    seasons = {}
    for f in sorted((save_dir / "stats").glob("*.json")) if (save_dir / "stats").is_dir() else []:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            d["youth"] = youth_comps(d.get("competitions", {}), ages)
            seasons[d["season"]] = d
        except (OSError, ValueError, KeyError):
            continue
    return {"seasons": seasons, "players": {r["name"]: r for r in squad}}


def club_page(squad, staff_rows, lineup, tabs, managed, team_info):
    """Club › Overview: the money, the squad in numbers, the best eleven, who's injured, contracts ending, the best
    youngsters and the staff's strongest jobs."""
    here = [r for r in squad if r["loan"] != "out"]
    best = (lineup.get("formations") or [None])[0]
    ages = [r["age"] for r in here if r["age"]]
    people = tabs["people"]
    soon = sorted((p for p in people.values() if p.get("contract")), key=lambda p: p["contract"])
    today = tabs.get("today") or ""
    year_on = f"{int(today[:4]) + 1}{today[4:]}" if today else ""
    return {"club": managed, "money": tabs["money"], "today": today,
            "squad": {"players": len(here), "loaned_out": len(squad) - len(here),
                      "age": sum(ages) / len(ages) if ages else None,
                      "value": sum(r["value"] or 0 for r in here), "wages": sum(r["wage"] or 0 for r in here),
                      "ca": sorted((r["ca"] for r in here if r.get("ca")), reverse=True)[:11]},
            "xi": {"name": best["name"], "avg": best["xi"], "players": best["players"], "weak": best.get("weak")} if best else None,
            "injured": sorted(([r["name"], r["injured"], r["best"], r.get("score")] for r in squad if r.get("injured")), key=lambda x: x[1]),
            "expiring": [[p["name"], p["contract"], p["role"], p.get("score")] for p in soon if year_on and p["contract"] <= year_on][:12],
            "youngsters": sorted(([p["name"], p["age"], p.get("ca"), p.get("pa"), p["best"]] for p in people.values()
                                  if (p["age"] or 99) <= 21 and (p.get("pa") or 0) > 0), key=lambda x: -x[3])[:8],
            "staff": [[j["name"], j["cover"][0], j["cover"][1]] for j in team_info["jobs"] if j.get("cover")][:8],
            "staffCount": sum(1 for r in staff_rows if r["club"] == managed),
            "players": {r["name"]: r for r in squad}}


AMATEUR_CLUBS = []  # build(): the amateur clubs, for SITE (the pages' "amateur" or "non-contract")


def build(save, config):
    """Every page: {page: (table columns, data[, extra])}."""
    save_dir = ROOT / "saves" / save
    if not (save_dir / "save.toml").is_file():
        raise SystemExit(f"There's no save called {save} (no saves/{save}/save.toml).")
    cfg = load_toml(save_dir / "save.toml")
    w = load_weights(cfg, save_dir)
    managed = cfg["managed_club"]

    people, sources = read_observations(save_dir / "obs", cfg.get("aliases"))
    staff_dir_stamps = {Path(f).name.split("_")[0] for f in (save_dir / "staff").glob("*.csv")} if (save_dir / "staff").is_dir() else set()
    dates = fm_dates(save_dir, config, {f["date"] for f in sources.values()} | staff_dir_stamps)
    FM_ORDER.clear()
    FM_ORDER.update(dates)
    rows = [row for name, person in people.items() if (row := player_row(name, person, w))]
    scored = {r["name"] for r in rows}
    if cfg.get("womens"):
        womens_vs_age(rows, w)
    if left_out := sorted(set(people) - scored):
        print(f"[{save}] left out, not enough attributes known to score: {', '.join(left_out)}")
    for key in ("released", "signed", "recalled"):
        require_known(cfg, key, people, "has players never imported")
    squad_moves(rows, cfg, managed, sources)
    # amateur clubs: most of their players (8+ known) have no contract end and no wage. The pages call such a deal
    # "amateur" there and "non-contract" elsewhere, as FM does (players and staff alike)
    by_club = {}
    for r in rows:
        c = by_club.setdefault(r["club"], [0, 0])
        c[0] += 1
        c[1] += not r.get("contract") and r.get("wage") == 0
    AMATEUR_CLUBS[:] = sorted(c for c, (n, free) in by_club.items() if n >= 8 and free / n >= 0.6)
    global CHECKPOINT_ORDER
    marks, CHECKPOINT_ORDER = checkpoints({f["date"] for f in sources.values()}, dates)
    for r in rows:
        for h in r["history"]:
            h["season"] = marks.get(h["date"])
    squad = [r for r in rows if r["club"] == managed]
    others = [r for r in rows if r["club"] != managed]

    roles, words = w.get("staff", {}), w.get("staff_words", {})
    staff = read_staff(save_dir / "staff", words)
    staff_rows = [staff_row(name, person, roles) for name, person in staff.items()]
    for r in staff_rows:  # one checkpoint per season, like the players'
        for h in r["history"]:
            h["season"] = marks.get(h["date"])
        r["history"] = by_season(r["history"])
    # every staff member in the game: the lists keep yours and the best others by score, so pages stay quick
    mine_staff = [r for r in staff_rows if r["club"] == managed]
    shown_staff, staff_note = keep_best([r for r in staff_rows if r["club"] != managed], STAFF_SHOW_MAX)
    staff_rows = mine_staff + shown_staff

    # your own staff against the rest: for each job your best two, and the best on your lists (other clubs, free agents)
    team_info = {"club": managed, **staff_tabs(save_dir / "staff", managed),
                 "jobs": staff_jobs(staff_rows, managed, roles, w.get("staff_names", {})),
                 # for Staff › Compare: everyone (yours first), each job's score and the attributes
                 "everyone": [{"name": r["name"], "club": r["club"], "job": r.get("job"), "age": r.get("age"), "personality": r.get("personality"),
                             "s": {k: round(r[k], 2) for k in roles if r.get(k) is not None}, "a": r["history"][-1]["attrs"]}
                            for r in sorted(staff_rows, key=lambda r: (r["club"] != managed, -(r.get("score") or 0)))]}
    lineup = grid(squad, w, cfg, latest_intake(people, sources))
    # Lineup: per position, the best on your imported lists whose best position it is (to beat your starter)
    lineup["targets"] = {pos: [{"name": r["name"], "club": r["club"], "age": r["age"], "wage": r["wage"], "v": r[pos],
                                "value": r["value"], "ca": r.get("ca"), "pa": r.get("pa"), "contract": r.get("contract")}
                               for r in sorted((r for r in others if r["best"] == pos and r.get(pos) is not None),
                                               key=lambda r: r[pos], reverse=True)[:30]]
                         for pos in FORMATION_ORDER}
    players_table, others_table = player_cols(w, False), player_cols(w, True)
    mstats = match_summary(save_dir, {r["name"]: r["age"] for r in rows})  # this season's match stats and form, on every player of yours
    for r in squad:
        if r["name"] in mstats:
            r["mstats"] = mstats[r["name"]]
    tabs = squad_tabs(squad, lineup, cfg, save_dir)
    many = lambda n, word: f"{n:,} {word}{'' if n == 1 else 's'}"
    # a very big save (tens of thousands imported): the lists, Compare and Research keep the best SHOW_MAX players by
    # score, so pages stay quick; the rest still count in Analysis and the Lineup's targets
    market_ranks(others)
    scout_marks(rows)
    tops = top_lists(rows, managed)
    dev = development(rows, people, sources, managed, squad)
    tabs["intakes"] = youth_intakes(rows, managed, dates)  # before the histories shrink to one checkpoint a season
    for r in rows:  # the profiles' History: one checkpoint per season
        r["history"] = by_season(r["history"])
    shown_others, hidden_note = keep_best(others)
    info = {
        "club": f"{managed} at a glance: money, squad, best eleven, injuries, contracts, youngsters, staff",
        "squad": f"{many(len(squad), 'player')}{budget_note(save_dir, config)} · your best eleven · click a position for who plays it",
        "formations": "26 shapes ranked by how well your squad fits them · click one to see it",
        "depth": "each position: starter, backup, third choice and the drop between them",
        "youth": f"your players {tabs['youthAge']} and under: how far they can grow",
        "contracts": "who to renew, who to sell, and what each player costs per point",
        "medical": "who's injured, unfit, rusty, tired or fragile",
        "transfers": "who could go, and who in the game would make you better",
        "analysis": "your squad position by position against the whole game",
        "development": "how your players changed between checkpoints (31 Dec, 30 Jun, now)",
        "training": "what each player should train next in FM",
        "season": "your players' match stats this season, from the save game: live as you play, the season report at the end",
        "elsewhere": f"{many(len(others), 'player')} in the game" + hidden_note + " · Filter to narrow it down",
        "toplists": "the best, the most promising and the bargains, by position",
        "compare": "two players side by side · pick them with the icon by a name",
        "staff": f"{many(len(staff_rows), 'staff member')} in the game{staff_note} · Filter to narrow it down",
        "team_staff": f"your {len(mine_staff)} staff: who covers each job, and who'd do it better",
        "staff_compare": "two staff side by side",
        "staff_research": "each job: the staff in the game against yours",
    }
    return cfg, with_info({
        "club": ([], club_page(squad, staff_rows, lineup, tabs, managed, team_info)),
        "squad": (players_table, squad, {"lineup": lineup}),
        "formations": ([], {"formations": lineup["formations"]}),
        "depth": ([], tabs),
        "youth": ([], tabs),
        "contracts": ([], tabs),
        "medical": ([], tabs),
        "training": ([], tabs),
        "season": ([], season_page(save_dir, squad, {r["name"]: r["age"] for r in rows})),
        "transfers": ([], tabs),
        "development": (players_table, dev),
        "analysis": (players_table, analysis(squad, others, lineup, w)),
        "elsewhere": (others_table, shown_others),
        "toplists": ([], tops),
        "charts": ([], charts(squad + shown_others, squad, w, cfg, set())),
        "compare": ([], compare_page(squad + shown_others, squad, w, cfg)),
        "staff": (staff_cols(roles, w.get("staff_names", {})), staff_rows),
        "team_staff": (staff_cols(roles, w.get("staff_names", {})), mine_staff, {"lineup": team_info}),
        "staff_compare": ([], team_info),
        "staff_research": ([], staff_research(staff_rows, managed, roles, w.get("staff_names", {}), set())),
        "attributes": ([], attributes_page(cfg, w, rows, save_dir)),
        "guide": ([], GUIDE),
    }, info)


OPTIONAL_COLS = {"height", "contract", "ca", "pa"}


def with_info(pages, info):
    """Each page's (columns, data, extra) with its info line (the strip under the bar) in extra."""
    out = {}
    for page, (columns, data, *extra) in pages.items():
        if isinstance(data, list):  # columns only a save game fills (height, contract): left off when no row has them
            columns = [c for c in columns if c["key"] not in OPTIONAL_COLS or any(r.get(c["key"]) is not None for r in data)]
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
    """page -> where it is in the menu, e.g. "3 · Players › Compare" (the Guide's tiles)."""
    where = {}
    for item in filter(None, MENU):
        if len(item) > 2:
            where.update({p: f"{item[0]} · {item[1]} › {SUB_LABELS[p]}" for p in item[2]})
        else:
            where[item[1]] = f"{item[0]} · {PAGE_LABELS[item[1]]}"
    return where


NAV_SCRIPT = """<script>(() => {  // the menu's groups: sub-tabs under the bar, and the keys
  const PAGE = %s, NAV = %s;
  window.fmNavTarget = key => {  // a key's page: a group's next sub-tab when you're in it, else its home page
    const t = NAV[key];
    if (!t || typeof t === "string") return t || null;
    const i = t.pages.indexOf(PAGE);
    return i >= 0 ? t.pages[(i + 1) %% t.pages.length] : t.home;
  };
  // as soon as the bar is there (template.html calls it right after the bar; else when the page has loaded):
  // a big page (the player database) would otherwise show the sub-tabs in the bar while it loads
  const place = window.fmPlaceNav = () => {
    const bar = document.querySelector(".bar"), info = document.getElementById("fm-info");
    if (!bar || document.getElementById("fm-subnav")) return;
    let strip = document.getElementById("fm-subnav-src");
    const own = document.querySelector(".subnav.rctl");  // Research's own strip (its choices): the sub-tabs go at its left
    if (strip && own) { const tabs = document.createElement("span"); tabs.className = "rtabs"; tabs.append(...strip.children); own.prepend(tabs); strip.remove(); strip = null; }
    if (strip) { strip.id = "fm-subnav"; strip.hidden = false; bar.after(strip); }
    if (info && info.dataset.on) {  // the page's line about its list, at the strip's right (a strip of its own without sub-tabs)
      if (!strip) { strip = document.createElement("nav"); strip.className = "subnav"; strip.id = "fm-subnav"; bar.after(strip); }
      info.hidden = false; strip.append(info);
    }
    // the app's Menu button, drawn here at once so it never blinks while a page loads; the app swaps in the real one
    const first = document.querySelector(".subnav") || (() => { const n = document.createElement("nav"); n.className = "subnav"; bar.after(n); return n; })();
    if (!document.getElementById("fm-import") && !document.getElementById("fm-import-ph")) {
      const ph = document.createElement("span"); ph.id = "fm-import-ph";
      ph.innerHTML = '<svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M2 3.5h12M2 8h12M2 12.5h12" stroke="#0f1412" stroke-width="2" stroke-linecap="round"/></svg>Menu';
      ph.style.cssText = "flex:none;align-self:center;display:flex;gap:7px;align-items:center;margin:4px 16px 4px 0;padding:4px 12px;border-radius:6px;" +
        "background:var(--accent-strong,#a9d93b);color:#0f1412;font:700 13px Inter,'Segoe UI',system-ui,sans-serif";
      first.style.paddingLeft = "14px"; first.prepend(ph);
    }
  };
  document.addEventListener("DOMContentLoaded", place);
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


def compact(value):
    """Floats to 3 decimals (scores, vs Age): the big pages shrink a lot, nothing shown changes."""
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, dict):
        return {k: compact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [compact(v) for v in value]
    return value


# A list page's rows send their attributes (each snapshot's), hidden attributes, FM's position ratings and personal
# details as plain lists in one shared order per field (the names once, not once a player): the page turns them back
# into {name: value} as it opens (a few milliseconds for 8,000 players).
PACKED = ("hidden", "fam", "personal")
UNPACK = ("(function (P, d) { const un = (K, v) => { const a = {}; v.forEach((x, i) => { if (x !== null) a[K[i]] = x; }); return a; };"
          " for (const r of d) { for (const f in P) if (f !== 'attrs' && Array.isArray(r[f])) r[f] = un(P[f], r[f]);"
          " for (const h of r.history || []) if (Array.isArray(h.attrs)) h.attrs = un(P.attrs, h.attrs); } return d; })")


def data_script(data):
    """The page's DATA: compact, a list page's dictionaries packed (UNPACK)."""
    data = compact(data)
    if not (isinstance(data, list) and data and isinstance(data[0], dict) and "history" in data[0]):
        return as_script(data)
    keys = {"attrs": sorted({k for r in data for h in r["history"] for k in (h.get("attrs") or {})})}
    for f in PACKED:
        keys[f] = sorted({k for r in data if isinstance(r.get(f), dict) for k in r[f]})
    pack = lambda d, ks: [d.get(k) for k in ks]
    rows = []
    for r in data:
        r = {**r, "history": [{**h, "attrs": pack(h["attrs"], keys["attrs"])} if isinstance(h.get("attrs"), dict) else h
                              for h in r["history"]]}
        for f in PACKED:
            if isinstance(r.get(f), dict):
                r[f] = pack(r[f], keys[f])
        rows.append(r)
    return f"{UNPACK}({as_script(keys)}, {as_script(rows)})"


def as_script(value):
    """JSON to put inside a <script>: a '</' (a name with '</script>' in it) can't end the script early."""
    return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")


# The small banner at the right of the top bar (blank url = no banner): Knap × ElevenIndex, the author's
# partnership with Knap (2026-11-17); the link goes to his FM27 tactics.
PROMO = {"url": "https://community.sports-interactive.com/forums/topic/451185-knap-fm26-and-fm24-fm23-fm22_fm-21-fm-20-fm-19-tactics/", "kicker": "Knap × ElevenIndex",
         "text": "Knap's FM27 tactics", "title": "Knap × ElevenIndex: Knap's tactics thread on the SI Games forum (opens in your browser)"}


def promo_html():
    """The banner at the right of the top bar: Sports Interactive's logo (on a white tile, inlined: pages work offline),
    then Knap × ElevenIndex; it opens Knap's tactics thread on SI's forum."""
    if not PROMO.get("url"):
        return ""
    logo = base64.b64encode((APP / "si_logo.png").read_bytes()).decode() if (APP / "si_logo.png").is_file() else ""
    img = (f'<span class="promo-si"><img src="data:image/png;base64,{logo}" alt="Sports Interactive"></span>'
           if logo else "")  # the round badge, cut out of its square (the banner's own colour shows around it)
    return (f'<a class="promo" href="{html.escape(PROMO["url"])}" target="_blank" rel="noopener" '
            f'title="{html.escape(PROMO["title"])}">{img}'
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
    money = read_finances(ROOT / "saves" / save)
    wages = [r["wage"] for r in squad_rows if r.get("wage") and r.get("loan") != "out"]
    # "within reach": a fee within the transfer budget and a wage no higher than your best-paid player's
    reach = {"budget": money.get("transfer_budget"), "maxWage": max(wages) if wages else None}
    site = json.dumps({"where": nav_where(), "currency": config.get("currency", "€"), "reach": reach,
                       "gameDate": max(stamps, default=None),  # the newest date: the profile's "years ago"
                       "today": read_finances(ROOT / "saves" / save).get("game_date"),  # FM's date in the last save read
                       "club": cfg.get("managed_club", ""), "squadBest": squad_best,
                       "staffBest": {j["role"]: j["cover"][:2] for j in (pages.get("staff_compare", ([], {}))[1] or {}).get("jobs", []) if j["cover"]},
                       "staffYou": YOU_ROLE, "amateur": AMATEUR_CLUBS,
                       "ageBench": {str(k): v for k, v in w.get("age_benchmark", {}).items()}}, ensure_ascii=False)
    weights = json.dumps({"GK": w["gk"], **w["outfield"], **w.get("staff", {})}, ensure_ascii=False)  # the profile's ordering
    fills = {"__FONTS__": font_css(), "__THEME__": (APP / "theme.css").read_text(encoding="utf-8"), "__SITE__": site, "__WEIGHTS__": weights,
             "__SAVE__": save, "__HOME__": html.escape(cfg["short_name"])}
    templates = {name: (APP / name).read_text(encoding="utf-8") for name in ("template.html", "charts.html")}
    for page, (columns, data, *extra) in pages.items():
        extra = extra[0] if extra else {}
        text = templates["charts.html" if page in ("charts", "staff_research") else "template.html"]
        for marker, value in {**fills, "__NAV__": menu(page), "__PROMO__": promo_html(), "__PAGE__": page,
                              "__TITLE__": f"{fills['__HOME__']} · {PAGE_LABELS[page]}", "__COLS__": json.dumps(columns),
                              "__LINEUP__": as_script(extra.get("lineup")), "__INFO__": as_script(extra.get("info")),
                              "__DATA__": data_script(data)}.items():
            text = text.replace(marker, value)
        text = text.replace('<html lang="en">', f'<html lang="en" data-theme="{theme_of(config)}">', 1)
        # written beside it, then swapped in: a window opening the page meanwhile never reads half a file
        tmp = out / f"{page}.html.tmp"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, out / f"{page}.html")
    for stale in out.glob("*.html"):
        if stale.stem not in pages:
            stale.unlink()
    tables = [p for p, spec in pages.items() if isinstance(spec[1], list) and p != "guide"]
    print(f"[{save}] out/{save}/: " + ", ".join(f"{p} ({len(pages[p][1])})" for p in tables))
    return out


# The guide page (key g): how to use the app and each page. Simple HTML; shown as tiles by template.html's renderGuide.
# A tile with a key links to that page. The version and settings are in Menu → About (settings_tile()).
GUIDE = [
    {"key": "", "title": "Start here",
     "text": "<ol><li>In FM, <b>save your game</b>.</li>"
             "<li>Here: <b>Menu</b> (key <b>M</b>) → <b>Read save game…</b> (key <b>I</b>), or drop the <b>.fm</b> file on the window.</li>"
             "<li>Be patient: your save is loaded soon, then every page is up to date.</li></ol>"
             "<b>Auto-read</b> (on unless you turn it off in the Menu) reads every new save of your game by itself. "
             "FM's save is only ever read, never changed."},
    {"key": "", "title": "What you get",
     "text": "<ul><li><b>Men's and women's football</b>: any club save, in either; national teams are on the way.</li>"
             "<li><b>Your club</b>: every player with exact attributes, CA and PA, hidden attributes, personality, "
             "contract, wage, value, fitness and injuries; your staff; your budgets.</li>"
             "<li><b>The whole game</b>: every player and staff member in your club's football (men's or women's) to compare with and scout.</li>"
             "<li><b>Checkpoints</b> by FM's date: <b>31 Dec</b> (mid-season) and <b>30 Jun</b> (season end), plus <b>now</b>, "
             "for Development and each profile's History.</li></ul>"},
    {"key": "", "title": "The numbers",
     "text": "<ul><li><b>Score</b>: how well his attributes suit a position, 1–100 (weights: the <b>Attributes</b> page).</li>"
             "<li><b>Best</b>: his best position and score there, among those FM rates him at.</li>"
             "<li><b>vs Age</b>: his score against what's normal at his age; 100% = on course for a solid top-flight player.</li>"
             "<li><b>CA / PA</b>: FM's hidden current and potential ability.</li>"
             "<li><b>Staff</b>: FM's words (Good, Very Good…) turned into a score for each job.</li></ul>"},
    {"key": "", "title": "Colours",
     "text": "<b>Scores</b>, one colour per point:<br>"
             + " ".join(f'<span style="color:{c};font-weight:700">■ {t}</span>' for c, t in (
                 ("#f87171", "under 9"), ("#fb923c", "9"), ("#fbd13d", "10"), ("#d7e35a", "11"), ("#a6e163", "12"),
                 ("#4ade80", "13"), ("#2dd4bf", "14"), ("#38bdf8", "15"), ("#c084fc", "16+"))) +
             "<ul><li><b>Attributes</b>, like FM: grey 1–5, white 6–9, yellow 10–14, green 15+.</li>"
             "<li><b>Changes</b>: green = better, red/orange = worse.</li>"
             "<li><b>Ages</b> on the pitch: sky blue 21 or under, white 22–29, amber 30+.</li></ul>"
             "Themes (Menu → Theme…) change the bars and panels, never these."},
    {"key": "", "title": "Profiles",
     "text": "<b>Click a name</b> anywhere (or <b>Enter</b> on a row). The header shows the key numbers.<ul>"
             "<li><b>Profile</b>: personal, contract, condition, attributes like FM's, the scout report, ability, personality, hidden attributes.</li>"
             "<li><b>Analysis</b>: what he's good for, strengths, weaknesses, what to train. <b>Positions</b>: FM's ratings and ours.</li>"
             "<li><b>History</b>: by checkpoint; pick two to compare. Staff have <b>Jobs</b>: every job and its key attributes.</li></ul>"},
    {"key": "club", "title": "Club",
     "text": "<b>Overview</b>: money, squad, best eleven, injuries, contracts ending, youngsters, staff. <b>Match stats</b>: your "
             "players' match stats this season (apps, minutes, rating, goals, assists, xG, passes, pressures, distance…), by "
             "competition, totals or per 90; live as you play, the season report at the end. <b>Contracts</b>: who to renew "
             "or sell. <b>Medical</b>: injured, unfit, rusty, tired, injury-prone. <b>Transfers</b>: who could go, and the best "
             "upgrade in the game for each position, within your budgets too."},
    {"key": "squad", "title": "Squad",
     "text": "<b>Lineup</b>: your best eleven; click a position for everyone who plays it. <b>Formations</b>: 26 shapes ranked, "
             "with a scout's view of the good and the bad. <b>Depth</b>: starter, backup and third choice at each position. "
             "<b>Analysis</b>: each position against the game, and the Squad plan. <b>Development</b>: changes between "
             "checkpoints. <b>Training</b>: each player's next individual routine in FM. <b>Youth</b>: how far your youngsters can grow."},
    {"key": "training", "title": "Training",
     "text": "<b>The week</b> (Squad › Training): a schedule for your first team and one for your youth teams (under-21s, "
             "under-18s, a B team), from what their players lack, how much they can still grow, minutes, form and load. "
             "Copy it into FM: <b>Training → Schedule</b>, for each team. Two matches a week: switch it on the page (it starts "
             "there when your starters are tired). The first team keeps two tactical understanding sessions (they keep the "
             "tactic's effectiveness up, which wins matches, and wear off within weeks); youth teams train only to grow. "
             "<b>Each player</b>: in FM, <b>Training → Individual</b>, pick him and set his <b>Training Focus</b> to the routine "
             "listed (one at a time); look again next month, a routine stops paying once its attributes catch up. A focus adds "
             "about +1 a season to its own attributes, so use it on a player's real gaps; set-piece routines only for your takers. "
             "Built on match-engine testing: a guide, not a law."},
    {"key": "elsewhere", "title": "Players",
     "text": "<b>Database</b>: every player in the game (the best 8,000 listed); <b>Filter</b> narrows it by anything "
             "(position, age, contract, value, CA, PA, attributes, personality) or start from a preset. <b>Top lists</b>: "
             "the best, the most promising, the bargains. <b>Research</b>: each position, the game against your best. "
             "<b>Compare</b>: two players side by side, with a head to head."},
    {"key": "team_staff", "title": "Staff",
     "text": "<b>Your staff</b>: who covers each job and the Staff plan (red: nobody, orange: a big gain, sand: worth a hire). "
             "<b>Database</b>: every staff member, with Filter. <b>Research</b>: each job against the game. <b>Compare</b>: two side by side. "
             "Your own job (manager) is left out."},
    {"key": "attributes", "title": "Attributes",
     "text": "One weight per attribute (1–20), used at every position. Change them and <b>Save</b>; <b>Export</b> / "
             "<b>Import</b> share them; <b>Reset to default</b> brings back the defaults. The defaults come from "
             "match-engine testing (every attribute from 1 to 20 against real Premier League tactics): what wins matches. Pace and Acceleration count most, then Stamina, Jumping Reach and Anticipation; "
             "Crossing, Tackling, Technique, Positioning and Flair barely change results, so they don't count. "
             "Goalkeeping attributes, which no test covers yet, are 15."},
    {"key": "", "title": "Searching and sorting",
     "text": "Type a name or club in <b>Search</b> (<b>/</b> jumps there, <b>Esc</b> clears it), or conditions like "
             "<b>age&lt;=21</b> <b>best=CD</b> <b>pa&gt;=160</b>. Click any heading to sort; again to reverse."},
    {"key": "", "title": "Menu",
     "text": "<b>Menu</b> (key <b>M</b>): your game profile with <b>Manage…</b> (one per club you manage, the club read from your "
             "save game: switch, new, delete, back up, restore), Read save game…, Auto-read, FM's saves folder (Open, Change…), "
             "Theme, Updates, About. Your data stays in "
             "<b>Documents\\ElevenIndex</b> when you update the app."},
    {"key": "", "title": "Keys",
     "text": "<b>1</b>–<b>4</b>, <b>a</b>, <b>g</b>: the menu (a number again: the next sub-tab) · <b>arrows</b> move · <b>Enter</b> "
             "opens a profile · <b>s</b> sorts · <b>c</b> copies a name · <b>x</b> adds to Compare · <b>?</b> every shortcut."},
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
    parser.add_argument("--open", nargs="?", const="club", choices=PAGES, metavar="PAGE", help="then open a page in the browser")
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


def write_config(save, currency, games_dir=None):
    """config.toml: this computer's settings (the save in use, the currency FM shows, where FM's save games are)."""
    ROOT.mkdir(parents=True, exist_ok=True)
    settings = {"active_save": save, "currency": currency, **({"games_dir": str(games_dir)} if games_dir else {})}
    text = "# ElevenIndex's settings for this computer.\n" + "".join(f"{k} = {toml_str(v)}\n" for k, v in settings.items())
    CONFIG.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
