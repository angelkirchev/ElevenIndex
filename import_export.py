"""Read an FM27 save game (fm_save) into ElevenIndex's observation CSVs: your club's players and staff, every other
player and staff member in the game, the club's budgets. import_save is what the app's Read save game… runs.

Goalkeepers and outfielders go to separate _gk and _outfield files; save.toml's last_batch names the newest.
"""
import csv
import re
import time
import tomllib
from pathlib import Path

import score  # the data folder (ROOT), the weights and the scoring rules

HERE = Path(__file__).parent

META_ORDER = ["Name", "Club", "Age", "Position", "Preferred Foot", "Left Foot", "Wage", "Transfer Value", "Loan", "Contract", "Height", "Minutes", "Rating"]
GK_CHECK = ["Handling", "Reflexes", "One on Ones", "Aerial Reach", "Command of Area"]  # outfielders show 1-5 here
GK_ATTRS = set(GK_CHECK) | {"Communication", "Eccentricity", "Kicking", "Punching", "Rushing Out (Tendency)", "Throwing"}
OUTFIELD_ONLY = {"Finishing", "Tackling", "Marking", "Heading", "Dribbling", "Crossing", "Long Shots", "Penalty Taking",
                 "Corners", "Free Kick Taking", "Long Throws", "Off The Ball"}
STAFF_META_ORDER = ["Name", "Club", "Age", "Job", "Personality", "Born", "Wage", "Contract", "Reputation",
                    "Current Ability", "Potential Ability", "Tactical style"]


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
    known_meta = set(META_ORDER) | {"Best Pos", "Nation", "Ability", "Potential", "Height", "Inf", "Weight", "DoB",
                                   "DOB", "Date Of Birth", "Date of Birth", "Nationality", "Personality", "Division", "Based",
                                   "Picked", "Best Role", "Playing Time", "Club"}  # not attributes (most aren't used)
    known_meta |= score.META  # what a save game adds (contract, ability, familiarity, hidden attributes…)
    attrs = [h for h in headers if h not in known_meta and h]
    out = {"outfield": [], "gk": []}
    for r in rows:
        if not r.get("Name"):
            continue
        view = "gk" if is_keeper(r) else "outfield"
        new = {"Name": r["Name"], "Club": r.get("Club") or club, "Age": r.get("Age", ""),
               "Wage": r.get("Wage", ""),
               "Transfer Value": r.get("Transfer Value", ""), "Loan": r.get("Loan", "")}
        for foot in ("Preferred Foot", "Left Foot"):  # the views now show Preferred Foot; older ones Left Foot
            if (r.get(foot) or "").strip() not in ("", "-"):
                new[foot] = r[foot].strip()
        for extra in ("Height", "Contract", "Familiarity", "Current Ability", "Potential Ability", "Reputation",
                      "Match Exercise", "Fitness", "Jadedness", "Injured until", "Traits", "Moves", "Born", "Left Foot Rating", "Right Foot Rating",
                      "Home Reputation", "World Reputation", *score.HIDDEN_COLS):
            # read from a save game: height in cm, the contract's end date, FM's hidden ability and attributes
            if (r.get(extra) or "").strip() not in ("", "-") or extra in ("Injured until", "Traits") and extra in r:
                new[extra] = (r[extra] or "").strip()  # a blank injury or trait list is news too (he's fit now)
        if (r.get("Position") or "").strip() not in ("", "-"):  # FM's own positions, e.g. "D/WB (R), M (RC)"
            new["Position"] = r["Position"].strip()
        if new["Club"] in ("-", ""):
            new["Club"] = "Free agent"
        # Research: the season's average rating (and minutes, when a view shows them). FM shows '-' for a player who
        # hasn't played, so each is kept only when it's a number (score.py reads them as such).
        minutes = (r.get("Minutes") or "").replace(",", "").strip()
        rating = (r.get("Rating") or "").strip()
        if re.fullmatch(r"\d+(\.\d+)?", rating):
            new["Rating"] = rating
            if minutes.isdigit():
                new["Minutes"] = minutes
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


def same_club(a, b):
    slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return slug(a) == slug(b)


SAVE_LIST = "search-save-game"  # every other player in a save game: one list, the newest save's only


def import_save(path, save=None, everyone=None):
    """An FM27 save game (.fm): your club's players (first team, youth teams, loans) and staff, read straight from it
    (fm_save), filed like a squad export and a staff screen export of the save file's date and time; with everyone
    (config.toml's save_everyone, on by default) also every other player in the save, as one list (SAVE_LIST).
    Returns (save, [(file name, row count)], [names skipped], "save")."""
    import fm_save  # needs Python 3.14+ (zstd); only a save import loads it
    path = Path(path)
    config = score.load_config()
    save = save or config["active_save"]
    save_toml = score.ROOT / "saves" / save / "save.toml"
    cfg = tomllib.loads(save_toml.read_text(encoding="utf-8"))
    club = cfg["managed_club"]
    everyone = config.get("save_everyone", True) if everyone is None else everyone
    got = fm_save.read(path, config.get("currency", "£"), everyone=everyone)
    if not (same_club(club, got["club"]) or same_club(club, got["long"])):
        raise ValueError(f"This FM save is {got['long']}'s, but the save open here is {club}'s. "
                         f"Start a new save for {got['club']} (Menu → Saves… → New), then import it there.")
    stamp = time.strftime("%Y-%m-%dT%H%M", time.localtime(path.stat().st_mtime))
    source = re.sub(r"[^a-z0-9]+", "-", club.lower()).strip("-")
    for r in got["players"] + got["staff"]:
        r["Club"] = club  # the club as this save names it
    written, skipped = [], []
    if got["players"]:
        headers = list(dict.fromkeys(k for r in got["players"] for k in r))
        obs_dir = save_toml.parent / "obs"
        obs_dir.mkdir(parents=True, exist_ok=True)
        converted, skipped = drop_unscorable(convert(got["players"], headers, club), obs_dir, cfg)
        for view, vrows in converted.items():
            if vrows:
                p = write_obs(obs_dir, f"{stamp}_{source}_{view}.csv", vrows)
                written.append((p.name, len(vrows)))
        set_last_batch(save_toml, [name for name, _ in written])
    if got.get("others"):  # everyone else: replaces the previous save game's list (it's all in the newer one)
        obs_dir = save_toml.parent / "obs"
        newest = max((p.name[:15] for p in obs_dir.glob(f"*_{SAVE_LIST}_*.csv")), default="")
        if stamp >= newest[:15]:
            for old in obs_dir.glob(f"*_{SAVE_LIST}_*.csv"):
                old.unlink()
            # the app tells players apart by name: a namesake (of yours, or of another in the list) gets his club added
            from collections import Counter
            mine, seen = {r["Name"] for r in got["players"]}, Counter(r["Name"] for r in got["others"])
            for r in got["others"]:
                if r["Name"] in mine or seen[r["Name"]] > 1:
                    r["Name"] = f"{r['Name']} ({r['Club']})"
            headers = list(dict.fromkeys(k for r in got["others"] for k in r))
            for view, vrows in convert(got["others"], headers, club).items():
                if vrows:
                    p = write_obs(obs_dir, f"{stamp}_{SAVE_LIST}_{view}.csv", vrows)
                    written.append((p.name, len(vrows)))
    if got["staff"]:
        staff_dir = save_toml.parent / "staff"
        staff_dir.mkdir(exist_ok=True)
        # the save file's time in the name: two saves read the same day stay two snapshots (the later one wins)
        p = write_obs(staff_dir, f"{stamp}_{source}-staff.csv", got["staff"], STAFF_META_ORDER, add_columns=True)
        written.append((p.name, len(got["staff"])))
        set_last_batch(save_toml, [p.name], "last_staff_batch", "last_staff_names")
    if got.get("others_staff"):  # every other club's staff (and coaches out of work): one list, the newest save's only
        staff_dir = save_toml.parent / "staff"
        staff_dir.mkdir(exist_ok=True)
        newest = max((p.name[:15] for p in staff_dir.glob(f"*_{SAVE_LIST}-staff.csv")), default="")
        if stamp >= newest[:15]:
            for old in staff_dir.glob(f"*_{SAVE_LIST}-staff.csv"):
                old.unlink()
            from collections import Counter
            mine, seen = {r["Name"] for r in got["staff"]}, Counter(r["Name"] for r in got["others_staff"])
            for r in got["others_staff"]:
                if r["Name"] in mine or seen[r["Name"]] > 1:
                    r["Name"] = f"{r['Name']} ({r['Club']})"
            p = write_obs(staff_dir, f"{stamp}_{SAVE_LIST}-staff.csv", got["others_staff"], STAFF_META_ORDER)
            written.append((p.name, len(got["others_staff"])))
    import json
    dates_file = save_toml.parent / score.FM_DATES  # FM's date in each save game read: the seasons it falls in
    try:
        dates = json.loads(dates_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dates = {}
    dates[stamp] = got["date"].isoformat()
    dates_file.write_text(json.dumps(dates, indent=1, sort_keys=True), encoding="utf-8")
    money_file = save_toml.parent / "finances.json"
    newest = score.read_finances(save_toml.parent).get("game_date", "")
    if got.get("finances") and got["date"].isoformat() >= newest:  # the club's budgets; an older save doesn't replace them
        import json
        money_file.write_text(
            json.dumps({"date": stamp, "game_date": got["date"].isoformat(), **got["finances"]}, indent=1), encoding="utf-8")
    if not written:
        raise ValueError(f"No players or staff of {club} found in {path.name}.")
    return save, written, skipped, "save"


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

