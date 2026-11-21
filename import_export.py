"""Read an FM27 save game (fm_save) into ElevenIndex's observation CSVs: your club's players and staff, every other
player and staff member in the game, the club's budgets. import_save is what the app's Read save game… runs.

Goalkeepers and outfielders go to separate _gk and _outfield files; save.toml's last_batch names the newest.
"""
import csv
import os
import re
import time
import tomllib
from pathlib import Path

import score  # the data folder (ROOT), the weights and the scoring rules

HERE = Path(__file__).parent

META_ORDER = ["Name", "Club", "Age", "Position", "Preferred Foot", "Left Foot", "Wage", "Transfer Value", "Loan", "Team", "Contract", "Deal", "Height", "Minutes", "Rating"]
GK_CHECK = ["Handling", "Reflexes", "One on Ones", "Aerial Reach", "Command of Area"]  # outfielders show 1-5 here
GK_ATTRS = set(GK_CHECK) | {"Communication", "Eccentricity", "Kicking", "Punching", "Rushing Out (Tendency)", "Throwing"}
OUTFIELD_ONLY = {"Finishing", "Tackling", "Marking", "Heading", "Dribbling", "Crossing", "Long Shots", "Penalty Taking",
                 "Corners", "Free Kick Taking", "Long Throws", "Off The Ball"}
STAFF_META_ORDER = ["Name", "Club", "Age", "Job", "Personality", "Born", "Wage", "Contract", "Deal", "Reputation",
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
        for extra in ("Team", "Height", "Contract", "Deal", "Familiarity", "Current Ability", "Potential Ability", "Reputation",
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
    tmp = path.with_name(path.name + ".tmp")  # written whole, then swapped in: a read cut short leaves no half file
    with open(tmp, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, restval="")
        w.writeheader()
        w.writerows(rows.values())
    os.replace(tmp, path)
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
    """The same club, however it was typed: "Man Utd" = "man-utd", and "Tottenham" = "Tottenham Hotspur" (every word
    of the shorter one in the longer one)."""
    words = lambda s: re.sub(r"[^a-z0-9]+", " ", s.lower()).split()
    x, y = words(a), words(b)
    if not x or not y:
        return False
    short, long = sorted((x, y), key=len)
    return x == y or all(w in long for w in short)


# leagues played in the calendar year (spring to autumn), whose season is the year itself rather than July to June
CALENDAR_LEAGUES = re.compile(r"Eliteserien|OBOS|Allsvenskan|Superettan|Veikkausliiga|Ykkösliiga|Besta deild|Úrvalsdeild|Bestadeild|"
                              r"League of Ireland|Premier Division \(Ireland\)|SSE Airtricity|Brasileir|Série [AB]|Liga Profesional|"
                              r"Primera Nacional|Major League Soccer|MLS|USL|J1 League|J2 League|J\.League|K League|Super League \(China\)|"
                              r"Chinese Super League|Primera División de Chile|Primera A \(Colombia\)|Liga 1 \(Peru\)|Uruguayan", re.I)


def calendar_season(competitions):
    """True when the club's league runs in the calendar year: its season is then the year (2027), not 2026/27."""
    return any(CALENDAR_LEAGUES.search(c) for c in competitions)


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
    got = fm_save.read(path, config.get("currency", "€"), everyone=everyone)
    obs = save_toml.parent / "obs"
    if not any(obs.glob("*.csv")) if obs.is_dir() else True:
        # nothing read into this profile yet: the save game decides its club (whatever was typed at setup)
        if club != got["club"]:
            text = save_toml.read_text(encoding="utf-8")
            text = re.sub(r'(?m)^managed_club\s*=\s*".*"$', lambda m: f'managed_club = "{got["club"]}"', text, count=1)
            save_toml.write_text(text, encoding="utf-8")
            club = got["club"]
    elif not (same_club(club, got["club"]) or same_club(club, got["long"])):
        raise ValueError(f"This FM save is {got['long']}'s, but the game profile open here is {club}'s. "
                         f"Start a profile for {got['club']} (Menu → Manage… → New), then read it there.")
    # the career's manager, for the app's "another career?" question before a read (app.career_warning), and whether
    # it's women's football (vs Age then follows the save's own players, score.womens_vs_age)
    text = new = save_toml.read_text(encoding="utf-8")
    manager = got["manager"].replace(chr(92), "").replace(chr(34), "")
    for key, value in (("manager", f'"{manager}"'), ("womens", "true" if got.get("womens") else "false")):
        line = f"{key} = {value}"
        if re.search(rf"(?m)^{key}\s*=", new):
            new = re.sub(rf"(?m)^{key}\s*=.*$", lambda m: line, new, count=1)
        else:
            new = re.sub(r"(?m)^(managed_club\s*=.*)$", lambda m: m.group(1) + "\n" + line, new, count=1)
    if new != text:
        save_toml.write_text(new, encoding="utf-8")
    stamp = time.strftime("%Y-%m-%dT%H%M", time.localtime(path.stat().st_mtime))
    source = re.sub(r"[^a-z0-9]+", "-", club.lower()).strip("-")
    for r in got["players"] + got["staff"]:
        r["Club"] = club  # the club as this save names it
    written, skipped = [], []
    import json
    dates_file = save_toml.parent / score.FM_DATES  # FM's date in each save game read: the seasons it falls in
    try:
        dates = json.loads(dates_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        dates = {}
    # the lists of everyone keep the newest game's, by FM's date (a save game read later can be an older game)
    newer = lambda old: not old or (got["date"].isoformat(), stamp) >= (dates.get(old) or old[:10], old)
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
        if newer(newest):
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
        if newer(newest):
            for old in staff_dir.glob(f"*_{SAVE_LIST}-staff.csv"):
                old.unlink()
            from collections import Counter
            mine, seen = {r["Name"] for r in got["staff"]}, Counter(r["Name"] for r in got["others_staff"])
            for r in got["others_staff"]:
                if r["Name"] in mine or seen[r["Name"]] > 1:
                    r["Name"] = f"{r['Name']} ({r['Club']})"
            p = write_obs(staff_dir, f"{stamp}_{SAVE_LIST}-staff.csv", got["others_staff"], STAFF_META_ORDER)
            written.append((p.name, len(got["others_staff"])))
    dates[stamp] = got["date"].isoformat()
    dates_file.write_text(json.dumps(dates, indent=1, sort_keys=True), encoding="utf-8")
    if got.get("season_stats"):  # the season's match stats, one file a season (the newest read in it wins)
        d = got["date"]
        comps = got["season_stats"].get("competitions", {})
        if calendar_season(comps):  # a league played in the calendar year (Scandinavia, Brazil, MLS, Japan…)
            start, label, file_name = d.year, str(d.year), str(d.year)
        else:  # July to June: a new season from 1 July
            start = d.year if d.month >= 7 else d.year - 1
            label, file_name = f"{start}/{str(start + 1)[2:]}", f"{start}-{str(start + 1)[2:]}"
        stats_dir = save_toml.parent / "stats"
        stats_dir.mkdir(exist_ok=True)
        stats_file = stats_dir / f"{file_name}.json"
        try:
            old = json.loads(stats_file.read_text(encoding="utf-8")).get("game_date", "")
        except (OSError, ValueError):
            old = ""
        if d.isoformat() >= old:
            stats_file.write_text(json.dumps({"season": label, "game_date": d.isoformat(), "read": stamp,
                                              "competitions": got["season_stats"].get("competitions", {}),
                                              "matches": got["season_stats"].get("matches", {})}, ensure_ascii=False), encoding="utf-8")
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

