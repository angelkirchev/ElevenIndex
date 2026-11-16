"""Turn an FM27 Exporter file (F8: .csv or .html) into ElevenIndex observation CSVs. No screenshots, no AI.

    python import_export.py EXPORT --date 2026-07-15                    a squad screen of your club
    python import_export.py EXPORT --date 2026-07-15 --source search-cb a player search
    python import_export.py EXPORT --date 2026-07-15 --score            ...then run score.py and open the 'last' page

Goalkeepers and outfielders are split into _gk and _outfield files automatically (export with all attributes:
Ctrl+F8 in FM). Rows merge into an existing file with the same date/source/view. save.toml's last_batch is updated.
"""
import argparse
import csv
import html
import re
import subprocess
import sys
import tomllib
from html.parser import HTMLParser
from pathlib import Path

import score  # the data folder (ROOT), the weights and the scoring rules

HERE = Path(__file__).parent

# Export header -> ElevenIndex column. Anything not listed keeps its name.
RENAME = {
    "Player": "Name", "Salary": "Wage", "Team Work": "Teamwork",
    "Command Of Area": "Command of Area", "One On Ones": "One on Ones",
    "Average Rating": "Rating", "Av Rat": "Rating", "Mins": "Minutes", "Based": "Division",
}
META_ORDER = ["Name", "Club", "Age", "Position", "Left Foot", "Wage", "Transfer Value", "Loan", "Minutes", "Rating", "Division"]
GK_CHECK = ["Handling", "Reflexes", "One on Ones", "Aerial Reach", "Command of Area"]  # outfielders show 1-5 here
GK_ATTRS = set(GK_CHECK) | {"Communication", "Eccentricity", "Kicking", "Punching", "Rushing Out (Tendency)", "Throwing"}
OUTFIELD_ONLY = {"Finishing", "Tackling", "Marking", "Heading", "Dribbling", "Crossing", "Long Shots", "Penalty Taking",
                 "Corners", "Free Kick Taking", "Long Throws", "Off The Ball"}
# An export needs some of these to be scored (a scouting view with only Recommendation etc. has none)
ATTRIBUTE_CHECK = GK_ATTRS | OUTFIELD_ONLY | {"Acceleration", "Pace", "Passing", "Decisions", "Composure", "Strength"}

# Staff exports (the Search - Staff View): FM's abbreviated headers -> the attribute names in the weights file.
# The exporter names staff files "Player Search ..." too, so a staff export is told apart by its Person/Preferred Job columns.
STAFF_RENAME = {
    "Person": "Name", "Preferred Job": "Job", "Club Job": "Job",
    "Det": "Determination", "Mot": "Motivating", "JCA": "Judging Player Ability", "Mgm": "People Management",
    "Auth": "Authority", "TCo": "Tactical Knowledge", "JPA": "Judging Player Potential", "Tec": "Technical",
    "Yng": "Working With Youngsters", "Pos": "Possession", "Tct": "Tactical", "Att": "Attacking", "Def": "Defending",
    "Phy": "Physiotherapy", "Fit": "Fitness", "SpS": "Sports Science", "Ana D": "Analysing Data", "Set": "Set Pieces",
    "GK": "Goalkeeping",
}
STAFF_META_ORDER = ["Name", "Club", "Age", "Job", "Personality"]


class _Table(HTMLParser):
    """Rows of the first <table>: a list of lists of cell text."""

    def __init__(self):
        super().__init__()
        self.rows, self._cell = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        elif tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None:
            self.rows[-1].append(html.unescape("".join(self._cell)).strip())
            self._cell = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def read_export(path):
    """(headers, rows as dicts) from an exporter .csv (any delimiter) or .html."""
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() in (".html", ".htm"):
        p = _Table()
        p.feed(text)
        table = [r for r in p.rows if r]
    else:
        dialect = csv.Sniffer().sniff(text.split("\n", 1)[0], delimiters=",;\t")
        table = [r for r in csv.reader(text.splitlines(), dialect) if r]
    headers = [RENAME.get(h, h) for h in table[0]]
    return headers, [dict(zip(headers, r)) for r in table[1:]]


def is_keeper(row):
    """Keepers have 7+ on average in the GK attributes, outfielders 1-5. Scouted ranges count as their midpoint."""
    vals = [int(v) for a in GK_CHECK if (v := attr_value(row.get(a) or ""))]
    return bool(vals) and sum(vals) / len(vals) >= 7


def attr_value(v):
    """'12' -> '12'; a scouted range '9-11' -> its midpoint; '-' or blank -> '' (unknown, carries forward)."""
    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", v.strip())
    if m:
        return str(round((int(m.group(1)) + int(m.group(2))) / 2))
    return v.strip() if v.strip().isdigit() else ""


def convert(rows, headers, club):
    """Split into {'outfield': rows, 'gk': rows} in ElevenIndex's format, plus the column list for each."""
    known_meta = set(META_ORDER) | {"Best Pos", "Nation", "Ability", "Potential", "Height", "Inf",
                                   "Picked", "Best Role", "Playing Time", "Club"}
    attrs = [h for h in headers if h not in known_meta and h]
    out = {"outfield": [], "gk": []}
    for r in rows:
        if not r.get("Name"):
            continue
        view = "gk" if is_keeper(r) else "outfield"
        new = {"Name": r["Name"], "Club": r.get("Club") or club, "Age": r.get("Age", ""),
               "Left Foot": r.get("Left Foot", ""), "Wage": r.get("Wage", ""),
               "Transfer Value": r.get("Transfer Value", ""), "Loan": r.get("Loan", "")}
        if (r.get("Position") or "").strip() not in ("", "-"):  # FM's own positions, e.g. "D/WB (R), M (RC)"
            new["Position"] = r["Position"].strip()
        if new["Club"] in ("-", ""):
            new["Club"] = "Free agent"
        # The charts page: season minutes and average rating. FM shows '-' for a player who hasn't played, so both are
        # kept only when they're numbers (score.py reads them as such).
        minutes = (r.get("Minutes") or "").replace(",", "").strip()
        rating = (r.get("Rating") or "").strip()
        if minutes.isdigit() and re.fullmatch(r"\d+(\.\d+)?", rating):
            new.update(Minutes=minutes, Rating=rating)
        if (r.get("Division") or "").strip() not in ("", "-"):
            new["Division"] = r["Division"].strip()
        for a in attrs:
            if view == "gk" and a in OUTFIELD_ONLY or view == "outfield" and a in GK_ATTRS:
                continue
            new[a] = attr_value(r.get(a, ""))
        out[view].append(new)
    return out


def write_obs(obs_dir, name, new_rows, meta=META_ORDER, add_columns=False):
    """Write (or merge into) obs_dir/name; a re-exported player replaces his old row. With add_columns (staff), a person
    already in the file keeps their row and gains the new export's values: FM's club staff screen shows the attributes
    over several views (Coaching, Mental, Medical, Scouting), exported one at a time."""
    path = obs_dir / name
    rows = {}
    if path.exists():
        with open(path, newline="", encoding="utf-8") as f:
            rows = {r["Name"]: r for r in csv.DictReader(f)}
    for r in new_rows:
        if add_columns and r["Name"] in rows:
            rows[r["Name"]].update({k: v for k, v in r.items() if v not in (None, "")})
        else:
            rows[r["Name"]] = r
    cols = [c for c in meta if any(c in r for r in rows.values())]
    cols += sorted({k for r in rows.values() for k in r} - set(cols))
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, restval="")
        w.writeheader()
        w.writerows(rows.values())
    return path


def set_last_batch(save_toml, files, key="last_batch", names_key="last_names"):
    """Point save.toml's last_batch (or last_staff_batch) at files, dropping last_names (or last_staff_names)."""
    text = save_toml.read_text(encoding="utf-8")
    line = f"{key} = [" + ", ".join(f'"{f}"' for f in files) + "]"
    text = re.sub(rf"^{key}\s*=.*$", line, text, flags=re.M) if re.search(rf"^{key}\s*=", text, re.M) \
        else text.rstrip("\n") + "\n" + line + "\n"
    text = re.sub(rf"^{names_key}\s*=.*\n?", "", text, flags=re.M)
    save_toml.write_text(text, encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("export", type=Path, help="the .csv or .html file FM27 Exporter wrote")
    ap.add_argument("--date", required=True, help="FM's game date, YYYY-MM-DD (the exporter can't read it)")
    ap.add_argument("--source", help="club slug for a squad screen (default), or search-<what> for a search")
    ap.add_argument("--club", help="club for rows without a Club column (default: your managed club)")
    ap.add_argument("--save", help="save folder (default: config.toml's active_save)")
    ap.add_argument("--score", action="store_true", help="run score.py afterwards and open the 'last' page")
    a = ap.parse_args()

    try:
        save, written, skipped, kind = import_file(a.export, a.date, a.source, a.club, a.save)
    except ValueError as e:
        sys.exit(str(e))
    for name, count in written:
        print(f"{count:>4} -> saves/{save}/{'staff' if kind == 'staff' else 'obs'}/{name}")
    if skipped:
        print(f"Skipped, not enough attributes to score: {', '.join(skipped)}")
    if a.score:
        page = "last_staff" if kind == "staff" else "last"
        subprocess.run([sys.executable, str(HERE / "score.py"), "--save", save, "--open", page], check=True)


def import_file(export, date, source=None, club=None, save=None):
    """Import one export (players or staff) into a save and set last_batch / last_staff_batch.
    Returns (save, [(file name, row count)], [names skipped], "players" or "staff")."""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(T\d{4})?", date):
        raise ValueError("The date must be YYYY-MM-DD (or YYYY-MM-DDThhmm), e.g. 2026-07-15")
    config = score.load_config()
    save = save or config["active_save"]
    save_toml = score.ROOT / "saves" / save / "save.toml"
    cfg = tomllib.loads(save_toml.read_text(encoding="utf-8"))
    club = club or cfg["managed_club"]
    source = source or re.sub(r"[^a-z0-9]+", "-", club.lower()).strip("-")
    obs_dir = save_toml.parent / "obs"
    obs_dir.mkdir(parents=True, exist_ok=True)

    headers, rows = read_export(Path(export))
    # staff: the staff search (Person, Preferred Job) or your club's staff screen (Club Job)
    if {"Person", "Preferred Job", "Club Job"} & set(headers):
        return (save, *import_staff(headers, rows, date, source, club, save_toml, cfg), "staff")
    if not set(headers) & ATTRIBUTE_CHECK:
        raise ValueError("This export has no player attributes, so there's nothing to score. In FM, point at the "
                         "list and press Ctrl+F8 to add every attribute to the view, then export again with F8.")
    converted = convert(rows, headers, club)
    if not any(converted.values()):
        raise ValueError("No players found in the export.")
    converted, skipped = drop_unscorable(converted, obs_dir, cfg)
    if not any(converted.values()):
        raise ValueError(f"None of the {len(skipped)} players have enough attributes to be scored: over 15% of "
                         "each position's weight is unknown (FM shows '-'). Scout them first, or add attributes (Ctrl+F8).")
    written = []
    for view, vrows in converted.items():
        if vrows:
            path = write_obs(obs_dir, f"{date}_{source}_{view}.csv", vrows)
            written.append((path.name, len(vrows)))
    set_last_batch(save_toml, [name for name, _ in written])
    return save, written, skipped, "players"


def import_staff(headers, rows, date, source, club, save_toml, cfg):
    """A staff export -> staff/<date>_<source>.csv, attributes as FM's words (score.py turns them into numbers with
    the weights file's [staff_words]). Returns ([(file name, count)], [names skipped])."""
    words = score.load_weights(cfg, save_toml.parent).get("staff_words", {})
    # a search named after "Player Search ..." is a staff search here
    source = source.replace("search-player-search", "search-staff") if source.startswith("search-") else source
    if "Club" not in headers:  # your club's staff screen: everyone on it works for you, like a squad screen
        source = re.sub(r"[^a-z0-9]+", "-", club.lower()).strip("-") + "-staff"
    out, skipped = [], []
    for r in rows:
        r = {STAFF_RENAME.get(k, k): (v or "").strip() for k, v in r.items()}
        if not r.get("Name"):
            continue
        # with a Club column, blank or '-' is a free agent; without one (your own staff screen), it's your club
        new = {"Name": r["Name"], "Club": r["Club"] if "Club" in r else club, "Age": r.get("Age", ""),
               "Job": r.get("Job", ""), "Personality": r.get("Personality", "")}
        if new["Club"] in ("-", ""):
            new["Club"] = "Free agent"
        attrs = {a: r[a] for a in STAFF_RENAME.values() if a in r and a not in STAFF_META_ORDER}
        new.update({a: v if v in words else "" for a, v in attrs.items()})  # '-' or anything else unknown: blank
        if any(new[a] for a in attrs):
            out.append(new)
        else:
            skipped.append(new["Name"])
    if not out:
        raise ValueError("No staff with attributes found in the export: use the Search - Staff View in FM.")
    staff_dir = save_toml.parent / "staff"
    staff_dir.mkdir(exist_ok=True)
    path = write_obs(staff_dir, f"{date}_{source}.csv", out, STAFF_META_ORDER, add_columns=True)
    set_last_batch(save_toml, [path.name], "last_staff_batch", "last_staff_names")
    return [(path.name, len(out))], skipped


def drop_unscorable(converted, obs_dir, cfg):
    """Leave out players score.py couldn't score at any position, even with what's known of them from earlier
    imports (it would stop on them). Returns (converted without them, their names)."""
    w = score.load_weights(cfg, obs_dir.parent)
    aliases = cfg.get("aliases", {})
    known, _ = score.read_observations(obs_dir, aliases)
    kept, skipped = {}, []
    for view, vrows in converted.items():
        kept[view] = []
        for r in vrows:
            prior = known.get(aliases.get(r["Name"], r["Name"]), {"keeper": False, "dates": {}})
            attrs = {}
            for d in sorted(prior["dates"]):
                attrs.update(prior["dates"][d]["attrs"])
            attrs.update({k: int(v) for k, v in r.items() if k not in score.META and v})
            positions = {"GK": w["gk"]} if view == "gk" or prior["keeper"] else w["outfield"]
            if score.weighted(attrs, positions, score.MIN_KNOWN_WEIGHT):
                kept[view].append(r)
            else:
                skipped.append(r["Name"])
    return kept, skipped


if __name__ == "__main__":
    main()
