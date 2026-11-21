"""Read an FM27 save game (.fm) straight from the file: your club's players and staff, as an export would give them.

ElevenIndex's own reader, worked out from the save file itself. Standard library only (Python 3.14+ for zstd). It only
reads the save: your own players' exact attributes, wages, values and contracts, your staff's attributes as FM's
words, and what FM keeps hidden (current and potential ability, hidden attributes, personality traits).

    python fm_save.py SAVE.fm            print your club's players and staff
"""
import re
import struct
import sys
from datetime import date, timedelta
from pathlib import Path

from compression import zstd

ZSTD = b"\x28\xb5\x2f\xfd"


def u16(b, p):
    return int.from_bytes(b[p:p + 2], "little")


def u32(b, p):
    return int.from_bytes(b[p:p + 4], "little")


def u64(b, p):
    return int.from_bytes(b[p:p + 8], "little")


# --- the container: a header, zstd frames, and a directory of sections at the end ---

def open_save(path):
    """(the file's bytes, {section name: (offset, compressed size)})."""
    data = Path(path).read_bytes()
    if data[2:6] != b"fmf.":
        raise ValueError(f"{Path(path).name} isn't an FM save game.")
    tail = data[u64(data, 9):]
    raw = zstd.ZstdDecompressor().decompress(tail[tail.find(ZSTD):])
    i = 4 + u32(raw, 0)  # the save's name
    count = u32(raw, i)
    i += 4
    sections = {}
    for _ in range(count):
        n = u32(raw, i)
        name = raw[i + 4:i + 4 + n].decode("utf-8", "replace")
        i += 4 + n
        i += 4 + u32(raw, i)  # extension
        sections[name] = (u64(raw, i), u64(raw, i + 8))
        i += 40
    return data, sections


def section(data, sections, name):
    off, size = sections[name]
    return zstd.ZstdDecompressor().decompress(data[26 + off:26 + off + size])


def summary(data, sections):
    """{manager, club, date}: the save's summary names the human manager, his club and the game's date."""
    b = section(data, sections, "save_game_summary")
    for i in range(len(b) - 16):
        n1 = u32(b, i)
        if not 2 <= n1 <= 80:
            continue
        j = i + 4 + n1
        n2 = u32(b, j)
        if not 2 <= n2 <= 80:
            continue
        k = j + 4 + n2
        year = u16(b, k + 6)
        if not 2000 <= year <= 2200:
            continue
        try:
            manager, club = b[i + 4:j].decode("utf-8"), b[j + 4:k].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if manager.isprintable() and club.isprintable():
            return {"manager": manager, "club": club, "date": game_date(u16(b, k + 4), year)}
    raise ValueError("Couldn't find the manager's club in the save.")


def game_date(day, year):
    """FM's dates: the day of the year (1-based) in the low 9 bits, then the year."""
    return date(year, 1, 1) + timedelta((day & 0x1FF) - 1)


# --- the database: names ---

def _entry(db, p):
    n = u32(db, p + 4)
    if not 1 <= n <= 80:
        return None
    try:
        s = db[p + 8:p + 8 + n].decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not s.isprintable() or not any(c.isalpha() for c in s):
        return None
    return u32(db, p), s, p + 8 + n


LIST_START = re.compile(rb"\x00\x00\x00\x00[\x01-\x50]\x00\x00\x00(?=[A-Za-z\xc0-\xff])")


def name_lists(db):
    """FM's three name lists, back to back: first names, surnames, common names; each entry `u32 number, u32 length,
    text`, numbered from 0. Returns three {number: name}."""
    starts = []
    for m in LIST_START.finditer(db):
        e = _entry(db, m.start())
        nxt = e and _entry(db, e[2])
        if nxt and nxt[0] == 1 and (third := _entry(db, nxt[2])) and third[0] == 2:
            starts.append(m.start())
    if len(starts) < 3:
        raise ValueError("Couldn't find FM's name lists in the save.")
    starts = starts[-3:]
    lists = []
    for k, start in enumerate(starts):
        stop = starts[k + 1] if k < 2 else len(db)
        names, p, last = {}, start, start
        while p < stop and p - last < 64:
            e = _entry(db, p)
            if e:
                names[e[0]] = e[1]
                p = last = e[2]
            else:
                p += 1
        lists.append(names)
    return lists


NONE = 0xFFFFFFFF
PERSON = re.compile(rb"(?=(.{4})\x00(.{4})\x00(.{4})\x00(.)\x00\x00\x00)", re.S)


def person(db, start, stop, lists):
    """The person block after a record: first name, surname, common name (numbers in the lists), full name, birth date.
    {name, born} or None."""
    first, sur, common = lists
    for m in PERSON.finditer(db, start, stop):
        f, s, c = (int.from_bytes(m.group(i), "little") for i in (1, 2, 3))
        if f == 0 or s == 0 or c == 0 or f not in first or s not in sur or (c != NONE and c not in common):
            continue
        n = m.group(4)[0]
        e = m.start() + 19
        try:
            full = db[e:e + n].decode("utf-8") if n else ""
        except UnicodeDecodeError:
            continue
        if full and (not full.isprintable() or not any(ch.isalpha() for ch in full)):
            continue
        day, year = u16(db, e + n), u16(db, e + n + 2)
        if not (1 <= day <= 366 and 1940 <= year <= 2013):
            continue
        traits = db[e + n + 4 + 18:e + n + 4 + 26]  # 18 bytes after the birth date: the 8 personality traits, 1-20
        return {"at": m.start(), "name": common[c] if c != NONE else f"{first[f]} {sur[s]}",
                "born": date(year, 1, 1) + timedelta(day - 1),
                "traits": {t: v for t, v in zip(TRAITS, traits) if 1 <= v <= 20}}
    return None


# FM's hidden personality traits, in the order a person block stores them (after the nation number)
TRAITS = ["Adaptability", "Ambition", "Loyalty", "Pressure", "Professionalism", "Sportsmanship", "Temperament", "Controversy"]
# a player's other hidden attributes, in the 54-byte block (×5)
HIDDEN = {41: "Consistency", 44: "Dirtiness", 47: "Important Matches", 48: "Injury Proneness", 49: "Versatility"}


def ability(db, p):
    """(current ability, potential ability) from two u16s; a negative potential is FM's code for a range."""
    ca, pa = u16(db, p), int.from_bytes(db[p + 2:p + 4], "little", signed=True)
    return (ca, pa) if 1 <= ca <= 200 and -200 <= pa <= 200 else (None, None)


def age_on(born, today):
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# --- clubs and teams ---

CLUB = re.compile(rb"(?=(.{4})\1.{27}([\x02-\x3c])\x00\x00\x00)", re.S)


CLUB_AREA = 16_000_000  # club records lie in the first ~10 MB of game_db (FM27): look there first


def clubs(db, limit=None):
    """{club ID: (long name, short name)}: a club record holds its ID twice, 39 and 35 bytes before its long name."""
    out = {}
    for m in CLUB.finditer(db, 0, limit or len(db)):
        s = m.start()
        cid = int.from_bytes(m.group(1), "little")
        if cid in out or cid in (0, NONE):
            continue
        n = m.group(2)[0]
        a = s + 39
        try:
            long = db[a:a + n].decode("utf-8")
        except UnicodeDecodeError:
            continue
        n2 = u32(db, a + n)
        if not (long.isprintable() and long[:1].isalnum() and 2 <= n2 <= 40):
            continue
        try:
            short = db[a + n + 4:a + n + 4 + n2].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if short.isprintable() and short[:1].isalnum():
            out[cid] = (long, short, a)
    return out


TEAM_TAIL = re.compile(rb"\x0a[\x00\x10]")


def teams(db):
    """{team number as others refer to it: (club ID or None, [record index + 1 of each player])}. A first team's head:
    `[u32 r][club ID][club ID] 0a 00`, others `0a 10`; byte 41 is 0, 42-45 ff, u16 player count at 46, then the list.
    A first team is referred to as r + 1 (contracts), other teams by the number in their head."""
    out = {}
    for m in TEAM_TAIL.finditer(db, 12):
        h = m.start() - 12
        if db[h + 25:h + 29] != b"\xff\xff\xff\xff":
            continue
        n = u16(db, h + 46)
        if n > 200:
            continue
        players = [u32(db, h + 48 + 4 * k) for k in range(n)]
        if len(set(players)) != n or any(not 0 < v < 2_000_000 for v in players):
            continue  # a real list: different record numbers
        if db[h + 13] == 0:
            if db[h + 4:h + 8] == db[h + 8:h + 12]:
                out.setdefault(u32(db, h) + 1, (u32(db, h + 4), players))
        else:
            out.setdefault(u32(db, h), (None, players))
    return out


def named_teams(db):
    """Teams that carry their own name (second teams like "Napier City B", "CSKA Sofia II", whose club ID has no club
    record): `[u32 len][long name] 00 [u32 len][short name] 00` right before the team's head. {club ID: (long, short)}."""
    out = {}
    for m in TEAM_TAIL.finditer(db, 12):
        h = m.start() - 12
        if db[h + 13] != 0 or db[h + 4:h + 8] != db[h + 8:h + 12] or db[h - 1] != 0:
            continue
        for n in range(2, 60):
            if u32(db, h - 1 - n - 4) == n:
                try:
                    short = db[h - 1 - n:h - 1].decode("utf-8")
                except UnicodeDecodeError:
                    break
                q = h - 1 - n - 4 - 1  # the long name ends with 00 there
                long = short
                for m2 in range(2, 80):
                    if db[q] == 0 and u32(db, q - m2 - 4) == m2:
                        try:
                            long = db[q - m2:q].decode("utf-8")
                        except UnicodeDecodeError:
                            pass
                        break
                if short.isprintable():
                    out.setdefault(u32(db, h + 4), (long, short, None))
                break
    return out


def name_teams(players):
    """Your club's teams for its players' "Team": 0 is the first team; the others are named by their players' ages
    (the middle one): 17 or under the Under-18s, 20 or under the Under-21s, 22 or under the Under-23s, else the B team."""
    ages = {}
    for r in players:
        if isinstance(r.get("Team"), int) and r["Team"] > 0 and str(r.get("Age", "")).isdigit():
            ages.setdefault(r["Team"], []).append(int(r["Age"]))
    label = {0: "First team"}
    for k, xs in ages.items():
        mid = sorted(xs)[len(xs) // 2]
        label[k] = "Under-18s" if mid <= 17 else "Under-21s" if mid <= 20 else "Under-23s" if mid <= 22 else "B team"
    for r in players:
        if isinstance(r.get("Team"), int):
            r["Team"] = label.get(r["Team"], "First team")


class NotSupported(ValueError):
    """A save ElevenIndex doesn't read yet: a national team's."""


NOT_YET = ("ElevenIndex reads club saves, in men's and women's football. National teams aren't available yet: "
           "they're on the way.")
# national sides, to tell a national team's save from a club the reader can't find
NATIONS = frozenset(['Afghanistan', 'Albania', 'Algeria', 'Andorra', 'Angola', 'Anguilla', 'Argentina', 'Armenia', 'Aruba', 'Australia', 'Austria', 'Azerbaijan', 'Bahamas', 'Bahrain', 'Bangladesh', 'Barbados', 'Belarus', 'Belgium', 'Belize', 'Benin', 'Bermuda', 'Bhutan', 'Bolivia', 'Bosnia and Herzegovina', 'Botswana', 'Brazil', 'Brunei', 'Bulgaria', 'Burkina Faso', 'Burundi', 'Cambodia', 'Cameroon', 'Canada', 'Cape Verde', 'Cayman Islands', 'Central African Republic', 'Chad', 'Chile', 'China', 'China PR', 'Chinese Taipei', 'Colombia', 'Comoros', 'Congo', 'Congo DR', 'Cook Islands', 'Costa Rica', 'Croatia', 'Cuba', 'Curaçao', 'Cyprus', 'Czech Republic', 'Czechia', "Côte d'Ivoire", 'DR Congo', 'Denmark', 'Djibouti', 'Dominica', 'Dominican Republic', 'Ecuador', 'Egypt', 'El Salvador', 'England', 'Equatorial Guinea', 'Eritrea', 'Estonia', 'Eswatini', 'Ethiopia', 'Faroe Islands', 'Fiji', 'Finland', 'France', 'Gabon', 'Gambia', 'Georgia', 'Germany', 'Ghana', 'Gibraltar', 'Greece', 'Grenada', 'Guam', 'Guatemala', 'Guinea', 'Guinea-Bissau', 'Guyana', 'Haiti', 'Honduras', 'Hong Kong', 'Hungary', 'Iceland', 'India', 'Indonesia', 'Iran', 'Iraq', 'Ireland', 'Israel', 'Italy', 'Ivory Coast', 'Jamaica', 'Japan', 'Jordan', 'Kazakhstan', 'Kenya', 'Korea Republic', 'Kosovo', 'Kuwait', 'Kyrgyzstan', 'Laos', 'Latvia', 'Lebanon', 'Lesotho', 'Liberia', 'Libya', 'Liechtenstein', 'Lithuania', 'Luxembourg', 'Macau', 'Madagascar', 'Malawi', 'Malaysia', 'Maldives', 'Mali', 'Malta', 'Mauritania', 'Mauritius', 'Mexico', 'Moldova', 'Mongolia', 'Montenegro', 'Montserrat', 'Morocco', 'Mozambique', 'Myanmar', 'Namibia', 'Nepal', 'Netherlands', 'New Caledonia', 'New Zealand', 'Nicaragua', 'Niger', 'Nigeria', 'North Korea', 'North Macedonia', 'Northern Ireland', 'Norway', 'Oman', 'Pakistan', 'Palestine', 'Panama', 'Papua New Guinea', 'Paraguay', 'Peru', 'Philippines', 'Poland', 'Portugal', 'Puerto Rico', 'Qatar', 'Republic of Ireland', 'Romania', 'Russia', 'Rwanda', 'Samoa', 'San Marino', 'Saudi Arabia', 'Scotland', 'Senegal', 'Serbia', 'Seychelles', 'Sierra Leone', 'Singapore', 'Slovakia', 'Slovenia', 'Solomon Islands', 'Somalia', 'South Africa', 'South Korea', 'South Sudan', 'Spain', 'Sri Lanka', 'Sudan', 'Suriname', 'Sweden', 'Switzerland', 'Syria', 'Tahiti', 'Tajikistan', 'Tanzania', 'Thailand', 'Timor-Leste', 'Togo', 'Tonga', 'Trinidad & Tobago', 'Tunisia', 'Turkey', 'Turkmenistan', 'Türkiye', 'UAE', 'USA', 'Uganda', 'Ukraine', 'United Arab Emirates', 'United States', 'Uruguay', 'Uzbekistan', 'Vanuatu', 'Venezuela', 'Vietnam', 'Wales', 'Yemen', 'Zambia', 'Zimbabwe'])


def club_teams(db, club_at, first_team):
    """The numbers of a club's teams: its record lists `[u8 count][first team][the others]` before its name; the
    others are referred to by number + 1."""
    r = first_team - 1
    pat = r.to_bytes(4, "little")
    lo = max(0, club_at - 2000)
    p = db.rfind(pat, lo, club_at)
    while p != -1:
        k = db[p - 1]
        if 1 <= k <= 12:
            return [first_team] + [u32(db, p + 4 * i) + 1 for i in range(1, k)]
        p = db.rfind(pat, lo, p)
    return [first_team]


# --- people ---

# the record's start, its index, its ID twice and its kind (1 or 2), all checked by the regex engine (fast)
RECORD = re.compile(rb"(?=[\x00-\xff][\x00-\xff][\x00-\x0f]\x00\x00\x00[\x00-\xff]{4}([\x00-\xff]{4})\1[\x01-\x03])", re.S)
POSITIONS = ["GK", "SW", "DL", "DC", "DR", "DM", "ML", "MC", "MR", "AML", "AMC", "AMR", "ST", "WBL", "WBR"]
# the 54 attribute bytes (×5) after the position ratings; 41, 44, 47-49 are hidden ones (HIDDEN), 24-25 the feet
ATTRIBUTES = {0: "Crossing", 1: "Dribbling", 2: "Finishing", 3: "Heading", 4: "Long Shots", 5: "Marking",
              6: "Off The Ball", 7: "Passing", 8: "Penalty Taking", 9: "Tackling", 10: "Vision", 11: "Handling",
              12: "Aerial Reach", 13: "Command of Area", 14: "Communication", 15: "Kicking", 16: "Throwing",
              17: "Anticipation", 18: "Decisions", 19: "One on Ones", 20: "Positioning", 21: "Reflexes",
              22: "First Touch", 23: "Technique", 26: "Flair", 27: "Corners", 28: "Teamwork", 29: "Work Rate",
              30: "Long Throws", 31: "Eccentricity", 32: "Rushing Out (Tendency)", 33: "Punching", 34: "Acceleration",
              35: "Free Kick Taking", 36: "Strength", 37: "Stamina", 38: "Pace", 39: "Jumping Reach", 40: "Leadership",
              42: "Balance", 43: "Bravery", 45: "Aggression", 46: "Agility", 50: "Natural Fitness", 51: "Determination",
              52: "Composure", 53: "Concentration"}
LEFT_FOOT, RIGHT_FOOT = 24, 25
# staff: attributes ×5 from the record's base (after its list of 7-byte items); two are 1-20 as they are
STAFF_X5 = {54: "Determination", 68: "Goalkeeping", 69: "Judging Player Ability", 70: "Judging Player Potential",
            71: "People Management", 72: "Motivating", 73: "Physiotherapy", 74: "Tactical Knowledge", 75: "Attacking",
            76: "Defending", 77: "Fitness", 78: "Possession", 79: "Technical", 80: "Tactical",
            85: "Analysing Data", 88: "Sports Science", 92: "Set Pieces"}
STAFF_RAW = {45: "Authority", 53: "Working With Youngsters"}
# a staff member's hidden tactical and chairman preferences, 1-20 as they are (FM shows none of them)
STAFF_PREFS = {41: "Attacking", 60: "Depth", 44: "Directness", 61: "Flamboyancy", 62: "Flexibility", 46: "Free Roles",
               48: "Marking", 49: "Offside", 51: "Pressing", 57: "Sitting Back", 65: "Tempo", 58: "Use Of Playmaker",
               59: "Use Of Substitutions", 66: "Width", 42: "Business", 47: "Interference", 50: "Patience",
               52: "Resources", 55: "Buying Players", 63: "Hardness Of Training", 56: "Mind Games", 64: "Squad Rotation"}
NOT_FOR_SALE = 300_000_000


def shown(raw):
    return int(raw / 5 + 0.5)


def records(db):
    """Every person record's start x: `[flags] ?? 0? 00 00 00`, u32 index at x+6, its ID twice at x+10 and x+14;
    x+18 = 2 for a player with attributes, 1 for staff and lighter records. The flags byte is mostly 00 or 40 but can
    be almost anything (Luis Díaz's is 60): such a record counts only when its index follows the last record's within
    5 (records are stored in index order), so other data that happens to look like one is left out."""
    last = -10
    for m in RECORD.finditer(db):
        x = m.start()
        rid = db[x + 10:x + 14]
        if rid in (b"\x00\x00\x00\x00", b"\xff\xff\xff\xff"):
            continue
        index = u32(db, x + 6)
        if db[x] not in (0x00, 0x40) and not 0 < index - last <= 5:
            continue
        kind = db[x + 18]
        if kind in (2, 3):  # 3: a player-coach (a player's record; his contract's job is 3)
            if all(1 <= v <= 20 for v in db[x + 49:x + 64]) and all(1 <= v <= 100 for v in db[x + 64:x + 118]):
                last = index
                yield x, "player"
        elif kind == 1:
            k = u16(db, x + 19)
            if k <= 30 and db[x + 40 + 7 * k] == 12:
                last = index
                yield x, "staff"


TODAY = None  # read(): the save's date, for finding a contract's end date when it isn't where it usually is


def contracts(db, start, stop, index1):
    """Every contract in a record: `[u32 the person's record index + 1][u16 team][6 × 00][u32 weekly wage, pounds]
    [u16 job (low byte 1: a player)]`, the end date 43 bytes before it (then the date he joined, then the start).
    A list of (team, wage, job, end date or None)."""
    out, ref = [], index1.to_bytes(4, "little")
    p = db.find(ref, start, stop)
    while p != -1:
        if db[p + 6:p + 12] == bytes(6) and db[p + 18] == 0:
            year, day = u16(db, p - 41), u16(db, p - 43)
            ends = game_date(day, year) if 2000 <= year <= 2100 and 1 <= day & 0x1FF <= 366 else None
            if ends is None and TODAY is not None and year != 1900:  # 1900: no end date (non-contract, amateur)
                # a long-serving player's contract carries more (clauses, bonuses: dated entries) between its end date
                # and here, up to ~400 bytes: the end date is then a date still to come followed by the date he joined
                # (already past)
                def joined_after(q):  # the date right after the one whose year is at q
                    y2, d2 = u16(db, q + 4), u16(db, q + 2)
                    return game_date(d2, y2) if 1990 <= y2 <= TODAY.year and 1 <= d2 & 0x1FF <= 366 else None
                later = []
                for q in range(max(start, p - 420), p - 4):
                    y, d = u16(db, q), u16(db, q - 2)
                    if TODAY.year <= y <= TODAY.year + 12 and 1 <= d & 0x1FF <= 366:
                        e, j = game_date(d, y), joined_after(q)
                        if e >= TODAY and j is not None and j <= TODAY:
                            later.append(e)
                ends = max(later) if later else None
            out.append((u16(db, p + 4), u32(db, p + 12), u16(db, p + 16), ends))
        p = db.find(ref, p + 1, stop)
    return out


PLAYER_JOBS = {1, 3}  # a contract's job (low byte): 1 a player, 3 a player-coach


def contract(db, start, stop, index1):
    """A player's contract (team, weekly wage in pounds, job, end date), or None: a free agent."""
    return next((c for c in contracts(db, start, stop, index1) if c[2] & 0xFF in PLAYER_JOBS), None)


def staff_block(db, x, stop):
    """A player-coach's staff data (laid out like a staff record's, from its start): inside his player record, after
    the player part. Found by its shape: a staff member's ability, attributes 0-100 (most set) and his preferred job."""
    for b in range(x + 118, min(stop, x + 4000) - 100):
        if db[b + 31] not in STAFF_JOBS or ability(db, b + 27)[0] is None:
            continue
        vals = [db[b + i] for i in STAFF_X5]
        if all(v <= 100 for v in vals) and sum(v > 0 for v in vals) >= 10:
            return b
    return None


def staff_contracts(db, start, stop, index1):
    """A staff member's contracts (team, weekly wage in pounds, job code, end date)."""
    return [c for c in contracts(db, start, stop, index1) if c[2] & 0xFF not in PLAYER_JOBS]


DOCTOR_JOBS = {0x26, 0x2E}  # Chief Doctor, Doctor: FM shows no salary, end date or F/PT for them (like the board)
# a staff contract's job (its low byte), as FM's Club Job column names it
BOARD = {"Chairperson", "Director", "Owner", "President", "Board"}
BOARD_CODES = {0x04, 0x06, 0x08, 0x42, 0x46}  # the board's job codes; a staff record's byte 31 is his preferred job
STAFF_JOBS = {0x02: "Coach", 0x03: "Player / Coach", 0x04: "Chairperson", 0x06: "Director", 0x26: "Chief Doctor", 0x2E: "Doctor", 0x42: "Owner", 0x46: "President", 0x08: "Board", 0x0A: "General Manager", 0x0C: "Physio", 0x0E: "Scout", 0x10: "Head Coach",
              0x14: "Assistant Coach", 0x1A: "Fitness Coach", 0x22: "Goalkeeping Coach", 0x28: "Head of Sports Science",
              0x2C: "Chief Scout", 0x30: "Sports Scientist", 0x32: "Head Physio", 0x3A: "Recruitment Analyst",
              0x3C: "Performance Analyst", 0x3E: "Head Performance Analyst", 0x40: "Head of Youth Development",
              0x56: "Loan Manager", 0x58: "Director of Football"}


def fm_position_text(ratings):
    """FM's Position text from the 15 ratings: the ones rated 15+, written as FM does: neighbouring roles on the same
    sides share one bracket, e.g. "D/WB (R), DM, M/AM (C)"; GK, DM and ST stand alone."""
    good = {p for p, v in zip(POSITIONS, ratings) if v >= 15}
    parts = []  # [roles, sides]
    for role, (left, centre, right) in (("GK", (None, "GK", None)), ("D", ("DL", "DC", "DR")),
                                        ("WB", ("WBL", None, "WBR")), ("DM", (None, "DM", None)),
                                        ("M", ("ML", "MC", "MR")), ("AM", ("AML", "AMC", "AMR")),
                                        ("ST", (None, "ST", None))):
        sides = "".join(s for s, p in zip("RLC", (right, left, centre)) if p in good)
        if not sides:
            continue
        if role in ("GK", "DM"):
            parts.append([[role], None])
        elif parts and role != "ST" and parts[-1][1] == sides and parts[-1][0][-1] != "ST":
            parts[-1][0].append(role)
        else:
            parts.append([[role], sides])
    return ", ".join("/".join(roles) + (f" ({sides})" if sides else "") for roles, sides in parts)


def foot_text(left, right):
    if left >= 18 and right >= 18:
        return "Either"
    return "Left-Footed" if left > right else "Right-Footed"


STAFF_WORDS = [(20, "Elite"), (18, "Outstanding"), (15, "Very Good"), (12, "Good"), (10, "Average"), (7, "Competent"),
               (4, "Reasonable"), (1, "Unsuited")]


def staff_word(v):
    """A staff attribute as FM shows it now: a word for its band (1-3 Unsuited … 20 Elite)."""
    return next((w for low, w in STAFF_WORDS if v >= low), "")  # 0: not set (blank, like FM's "-")


def currency_rate(db, symbol):
    """How many of `symbol` one pound buys, from FM's currency table (`[u32 len][symbol][f32 rate]`); 1 for £."""
    if symbol in ("£", "", None):
        return 1.0
    sym = symbol.encode("utf-8")
    p = db.find(b"\x00\x00\x00\x00" + len(sym).to_bytes(4, "little") + sym)
    while p != -1:
        rate = struct.unpack_from("<f", db, p + 8 + len(sym))[0]
        if 0.01 < rate < 10000:
            return rate
        p = db.find(b"\x00\x00\x00\x00" + len(sym).to_bytes(4, "little") + sym, p + 1)
    return 1.0


def money(v):
    """1500 -> '1.5K', 2100000 -> '2.1M' (as FM writes it)."""
    for size, unit in ((1e6, "M"), (1e3, "K")):
        if v >= size:
            return f"{v / size:.2f}".rstrip("0").rstrip(".") + unit
    return f"{v:.0f}"


def read(path, symbol="£", club=None, everyone=False):
    """Your club's players and staff from an FM27 save. Returns {club, manager, date (FM's), players: [rows],
    staff: [rows], others: [rows] (with everyone: every other player in the save, at the club he plays for)}; rows use
    the export's column names, players' attributes 1-20, staff attributes as FM's words."""
    data, sections = open_save(path)
    info = summary(data, sections)
    hurt = injuries(data, sections, info["date"])
    db = section(data, sections, "game_db")
    lists = name_lists(db)
    names = clubs(db, CLUB_AREA)  # the club table sits early in the database; the whole of it only if needed
    if not any((club or info["club"]) in c[:2] for c in names.values()):
        names = clubs(db)
    all_teams = teams(db)
    second = named_teams(db)
    clubs_with_record = set(names)  # real clubs; a named second team has no club record of its own
    for c, named in second.items():  # second teams named in their own head
        names.setdefault(c, named)
    want = club or info["club"]
    mine = [(cid, c) for cid, c in names.items() if want in (c[0], c[1])]
    first = {cid: t for t, (cid, _) in all_teams.items() if cid is not None}
    mine = [(cid, c) for cid, c in mine if cid in first]
    if not mine:  # not in the club table's usual place (or only a namesake there): the whole database
        names = clubs(db)
        for c, named in named_teams(db).items():
            names.setdefault(c, named)
        mine = [(cid, c) for cid, c in names.items() if want in (c[0], c[1]) and cid in first]
    if not mine:
        if want in NATIONS:
            raise NotSupported(f"This save manages {want}, a national team. {NOT_YET}")
        raise ValueError(f"Couldn't find {want}'s teams in the save.")
    # namesakes (a women's side, a second database entry): the original club, whose IDs are the small ones
    mine.sort(key=lambda m: (m[0] >= 2_000_000_000, m[0]))
    cid, (long, short, at) = mine[0]
    team_ids = club_teams(db, at, first[cid])
    contract_teams = {team_ids[0]} | {t + 1 for t in team_ids[1:]}  # a contract names a team by its head's number + 1
    # the club's teams as their heads are numbered: the record names the others by number + 1, though for some clubs
    # (Real Madrid) by the number itself; a youth team's head has no club ID
    heads = [team_ids[0]] + [t if t in all_teams and all_teams[t][0] is None else t - 1 for t in team_ids[1:]]
    playing = {i for t in heads if t in all_teams for i in all_teams[t][1]}  # record index + 1
    team_no = {i: k for k, t in reversed(list(enumerate(heads))) if t in all_teams for i in all_teams[t][1]}  # index+1 -> 0 first team, 1… the others
    plays_in = {i: c for t, (c, members) in all_teams.items() if c is not None for i in members}  # index+1 -> club of his team
    in_youth = {i for t, (c, members) in all_teams.items() if c is None for i in members}  # in a youth team (no club ID)
    elsewhere = {i for t, (_, members) in all_teams.items() if t not in heads for i in members}  # at other clubs
    rate = currency_rate(db, symbol)
    today = info["date"]
    global TODAY
    TODAY = today
    team_club = {t: c for t, (c, _) in all_teams.items() if c is not None}  # a first team (as contracts name it) -> club
    plays_for = {i: c for t, (c, members) in all_teams.items() if c is not None for i in members}  # index+1 -> club
    if everyone:  # every club's other teams (U21, U18, reserves): their players, and contracts naming them (+1)
        for c, (_, _, c_at) in names.items():
            if c in first and c_at is not None:
                for t in club_teams(db, c_at, first[c])[1:]:
                    team_club.setdefault(t + 1, c)
                    for i in all_teams.get(t, (None, []))[1]:
                        plays_for.setdefault(i, c)
    # each club's professional status, the byte after its short name: 1 professional, 2 semi-pro, 3 amateur
    status = {}
    for c in clubs_with_record:
        long_b = names[c][0].encode("utf-8")
        a = names[c][2]
        e = a + len(long_b) + 4 + u32(db, a + len(long_b))
        if db[e] in (1, 2, 3):
            status[c] = db[e]
    def deal_kind(deal):
        """FM's contract status: Full-time at a professional club, else Part-time; with no end date and no wage,
        Amateur at an amateur club, else Non-contract. A loanee's is his own club's contract."""
        if not deal:
            return ""
        st = status.get(cid if deal[0] in contract_teams else team_club.get(deal[0]))
        if deal[3] is None and deal[1] == 0:
            return "Amateur" if st == 3 else "Non-contract"
        return {1: "Full-time", 2: "Part-time", 3: "Part-time"}.get(st, "")
    def add_staff(base, end, index1, pc_deal=None, pc_who=None):
        """A staff member from his staff data at base (a player-coach's: inside his player record, with his contract
        and name from the player side)."""
        deals = [pc_deal] if pc_deal else staff_contracts(db, base + 93, max(end, base + 2000), index1)
        ours = any(d[0] in contract_teams for d in deals)
        if not ours and not everyone:
            return
        who = pc_who or person(db, base + 100, max(end, base + 2000), lists)
        if not who:
            return
        if not pc_deal:
            deals = staff_contracts(db, base + 93, who["at"], index1)
        deal = next((d for d in deals if d[0] in contract_teams), None) if ours else                 next((d for d in deals if team_club.get(d[0]) in names), None)
        if ours and not deal:
            return
        ca, pa = ability(db, base + 27)
        if not deal and (deals or not ca or ca < 60):
            return
        if (deal[2] & 0xFF if deal else db[base + 31]) in BOARD_CODES:
            return  # the board (chairman, president, owner, director), working or out of work  # no club: kept when he's a real out-of-work coach (some ability), not a national side's
        at_club = cid if ours else team_club[deal[0]] if deal else None
        label = short if ours else names[at_club][1] if deal else "Free agent"
        if not ours and label in (short, long):
            label = f"{label} (another club)"
        row = {"Name": who["name"], "Club": label, "Age": str(age_on(who["born"], today)),
               "Job": (STAFF_JOBS.get(deal[2] & 0xFF) or STAFF_JOBS.get(db[base + 31], "")) if deal else "", "Born": who["born"].isoformat(),
               "Reputation": str(u16(db, base + 23))}
        if deal and deal[2] & 0xFF not in DOCTOR_JOBS:  # FM shows a doctor's (and the board's) salary, end and F/PT as N/A
            row["Wage"] = f"{symbol}{money(deal[1] * rate)} p/w"
            if deal[3]:
                row["Contract"] = deal[3].isoformat()
            row["Deal"] = deal_kind(deal)
        if row["Job"] in BOARD:  # the board: FM's staff screen shows no attributes for them, and they coach nobody
            return
        row.update({a: staff_word(shown(db[base + i])) for i, a in STAFF_X5.items()})
        row.update({a: staff_word(db[base + i]) for i, a in STAFF_RAW.items() if 1 <= db[base + i] <= 20})
        if who["traits"].get("Adaptability"):  # the staff screen's Ada is the person's trait
            row["Adaptability"] = staff_word(who["traits"]["Adaptability"])
        if ca:
            row.update({"Current Ability": str(ca), "Potential Ability": str(pa)})
        row["Tactical style"] = ";".join(f"{a}:{db[base + o]}" for o, a in STAFF_PREFS.items() if 1 <= db[base + o] <= 20)
        if ours:
            staff.append(row)
        else:
            others_staff.append(row)
            where_staff.append(at_club)

    players, staff, others = [], [], []
    mine_index = {}  # your players' record index + 1 -> name (their match stats)
    first_name, where = {}, []  # each player's first name number; each other's (club, record index + 1)
    others_staff, where_staff = [], []  # with everyone: every other club's staff, and their club
    # each record's own data runs to the next record: a player's grows with every season (history, stats), so a fixed
    # window misses the name and contract of long-serving players (Sunderland in 2028: 5,906 bytes in)
    recs = list(records(db))
    for k, (x, kind) in enumerate(recs):
        index1 = u32(db, x + 6) + 1
        end = min(recs[k + 1][0] if k + 1 < len(recs) else len(db), x + 200_000)
        stop = end
        if kind == "player":
            near = contract(db, x + 118, end, index1)  # quick look before the slower name lookup
            if not everyone and (near is None or near[0] != first[cid]) and index1 not in playing:
                continue
            who = person(db, x + 118, end, lists)
            if not who:
                continue
            first_name[index1] = u32(db, who["at"])
            # several contracts (a national team's too): the one with a club that has a name, else the first
            deals = [c for c in contracts(db, x + 118, who["at"] + 600, index1) if c[2] & 0xFF in PLAYER_JOBS]
            deal = next((c for c in deals if team_club.get(c[0]) in names), deals[0] if deals else None)
            ours = deal is not None and deal[0] == first[cid]
            if not ours and index1 not in playing:
                if not everyone:
                    continue
                # on loan: two contracts with different clubs; the owner's is the long one (job code 0x0B01), he
                # plays for the other. Else the club whose team he's in, or his contract's.
                named = [c for c in deals if team_club.get(c[0]) in names]
                clubs_of = {team_club[c[0]] for c in named}
                if len(clubs_of) >= 2:
                    deal = next((c for c in named if c[2] >> 8 == 0x0B), named[0])
                    owner = team_club[deal[0]]
                    at_club = next(team_club[c[0]] for c in named if team_club[c[0]] != owner)
                else:
                    owner = team_club.get(deal[0]) if deal else None
                    at_club = owner if owner in names else plays_for.get(index1)
                # no club with a name: his only team is a national side (its ID is a nation's): no club, like a free agent
                label = names[at_club][1] if at_club in names else "Free agent"
                if at_club != cid and label in (short, long):  # another club of the same name (e.g. its women's side)
                    label = f"{label} (another club)"
                row = {"Name": who["name"], "Club": label,
                       "Age": str(age_on(who["born"], today)), "Position": fm_position_text(db[x + 49:x + 64]),
                       "Preferred Foot": foot_text(shown(db[x + 64 + LEFT_FOOT]), shown(db[x + 64 + RIGHT_FOOT])),
                       "Familiarity": ",".join(str(v) for v in db[x + 49:x + 64])}
                if owner and at_club and owner != at_club and owner in names:
                    row["Loan"] = f"from {names[owner][1]}"
                others.append(fill_player(row, db, x, who, deal, symbol, rate))
                row["Deal"] = deal_kind(deal)
                if db[x + 18] == 3 and deal and deal[2] & 0xFF == 3 and (b := staff_block(db, x, end)):
                    add_staff(b, end, index1, deal, who)
                row["Injured until"] = hurt[index1].isoformat() if index1 in hurt else ""
                where.append((at_club, index1))
                continue
            # a B team of its own (Castilla, Jong Ajax, Celtic B): its players are under contract to the club itself and
            # to no other club, so they're the club's; a loan out has a second contract, with the club he's at
            b_team = None
            if ours and index1 not in playing:
                b_club = plays_in.get(index1)
                others_deals = [d for d in deals if team_club.get(d[0]) not in (cid, None)]
                # a loan's second contract is a plain one (job 0x01); a B team's is the club's kind (0x0B01), same end
                if all(d[2] >> 8 == 0x0B for d in others_deals):  # not loaned out
                    if b_club is not None and b_club != cid:
                        b_team = names[b_club][1] if b_club in names else "B team"
                    elif b_club is None and index1 in in_youth:  # a youth team the club's record doesn't point to plainly
                        b_team = "Youth"
            loan = "" if b_team else "out" if ours and (index1 not in playing or index1 in elsewhere) else "in" if not ours else ""
            row = {"Name": who["name"], "Club": short, "Age": str(age_on(who["born"], today)),
                   "Position": fm_position_text(db[x + 49:x + 64]),
                   "Preferred Foot": foot_text(shown(db[x + 64 + LEFT_FOOT]), shown(db[x + 64 + RIGHT_FOOT])),
                   "Loan": loan, "Familiarity": ",".join(str(v) for v in db[x + 49:x + 64])}
            row["Team"] = b_team if b_team else "" if loan == "out" else team_no.get(index1, 0)
            players.append(fill_player(row, db, x, who, deal, symbol, rate))
            row["Deal"] = deal_kind(deal)
            if db[x + 18] == 3 and deal and deal[2] & 0xFF == 3 and (b := staff_block(db, x, end)):
                add_staff(b, end, index1, deal, who)
            mine_index[index1] = who["name"]
            row["Injured until"] = hurt[index1].isoformat() if index1 in hurt else ""
        else:
            add_staff(x + 7 * u16(db, x + 19), end, index1)
    mine_women = False
    if others or others_staff:  # leave out women's football: a men's club can't sign them (and the reverse)
        women, share = womens_clubs(db, first_name)
        mine_women = cid in women
        fem = lambda i: FEMALE_NAMES[0] <= first_name.get(i, 0) < FEMALE_NAMES[1]
        # a women's club, or a female first name at a club that isn't clearly a men's one (too few players known)
        woman = lambda c, i: c in women or (fem(i) and share.get(c, 1.0) >= 0.2)
        others = [r for r, (c, i) in zip(others, where) if woman(c, i) == mine_women]
        others_staff = [r for r, c in zip(others_staff, where_staff) if (c in women) == mine_women]
    season_stats = match_stats(data, sections, mine_index)
    money_now = finances(db, at)
    if money_now:  # in the currency the app shows
        money_now = {k: round(v * rate) for k, v in money_now.items()}
    name_teams(players)
    return {"club": short, "long": long, "manager": info["manager"], "date": today, "players": players, "staff": staff,
            "others": others, "others_staff": others_staff, "womens": mine_women, "finances": money_now, "season_stats": season_stats}


# FM27's first-name list keeps a block of female names here; a club whose players mostly have one is a women's club.
# The split is clean (clubs are near 0% or 100%), and a league is all one or the other, which covers small clubs.
FEMALE_NAMES = (251000, 300000)


def womens_clubs(db, first_name):
    """The club IDs of women's clubs: most of its first team have female first names; a club with too few players
    known goes with its league (the u32 at its first team's head +17)."""
    fem = lambda f: FEMALE_NAMES[0] <= f < FEMALE_NAMES[1]
    share, league_of, by_league = {}, {}, {}
    for m in TEAM_TAIL.finditer(db, 12):
        h = m.start() - 12
        if db[h + 13] != 0 or db[h + 4:h + 8] != db[h + 8:h + 12] or db[h + 25:h + 29] != b"\xff\xff\xff\xff":
            continue
        n = u16(db, h + 46)
        if n > 200:
            continue
        c, league = u32(db, h + 4), u32(db, h + 17)
        names = [first_name[i] for i in (u32(db, h + 48 + 4 * k) for k in range(n)) if i in first_name]
        league_of[c] = league
        if len(names) >= 5:
            share[c] = sum(map(fem, names)) / len(names)
            by_league.setdefault(league, []).append(share[c])
    women = set()
    for c, league in league_of.items():
        if c in share:
            if share[c] >= 0.5:
                women.add(c)
        elif by_league.get(league) and sum(by_league[league]) / len(by_league[league]) >= 0.5:
            women.add(c)
    return women, share


def injuries(data, sections, today):
    """{record index + 1: the date he's expected back} for everyone injured now. Section `injury_manager`: lists of
    `[u32 count]` + count × `[01][date][date][u32 record index + 1][00]`; the second date is FM's "out until"."""
    try:
        b = section(data, sections, "injury_manager")
    except KeyError:
        return {}
    out, p = {}, 12
    while p + 4 <= len(b):
        n = u32(b, p)
        if n > 20000 or p + 4 + 14 * n > len(b):
            break
        for i in range(n):
            q = p + 4 + 14 * i
            year, day = u16(b, q + 7), u16(b, q + 5) & 0x1FF
            if b[q] == 1 and 2000 <= year <= 2100 and 1 <= day <= 366:
                back = game_date(day, year)
                if back > today:
                    ref = u32(b, q + 9)
                    out[ref] = max(out.get(ref, back), back)
        p += 4 + 14 * n
    return out


# a player's preferred moves: a 64-bit set 8 bytes before his person block. FM keeps their names in its language files,
# so these are the ones matched so far against FM's player screens (bit -> name); the others stay unnamed.
TRAIT_BITS = {3: "Gets Into Opposition Area", 5: "Gets Forward Whenever Possible", 7: "Tries Killer Balls Often",
              8: "Shoots From Distance", 20: "Plays One Twos", 32: "Tries To Play Way Out Of Trouble",
              43: "Tries Long Range Passes", 51: "Runs With Ball Often",
              # from 10 more Genie Positions pages (a test save): each name is the bit those players share
              2: "Runs With Ball Through Centre", 10: "Places Shots", 11: "Curls Ball", 17: "Argues With Officials",
              19: "Comes Deep To Get Ball", 29: "Moves Ball To Left Foot", 35: "Tries Tricks", 39: "Cuts Inside From Both Wings",
              41: "Gets Crowd Going", 42: "Tries First Time Shots", 44: "Likes Ball Played Into Feet",
              46: "Likes To Beat Man Repeatedly", 55: "Uses Long Throw To Start Counter Attacks", 58: "Cuts Inside From Right Wing",
              60: "Brings Ball Out Of Defence", 4: "Moves Into Channels", 27: "Knocks Ball Past Opponent",
              37: "Dives Into Tackles", 9: "Shoots With Power", 16: "Winds Up Opponents", 47: "Likes To Switch Ball To Other Flank",
              49: "Will Play Football As Long As Possible", 57: "Cuts Inside From Left Wing",
              14: "Uses Outside Of Foot", 12: "Likes To Round Keeper", 31: "Arrives Late In Opposition Area",
              13: "Likes To Try To Beat Offside Trap", 23: "Attempts Overhead Kicks", 52: "Runs With Ball Rarely"}
# still open: Hits Free Kicks With Power is 45 or 61 (Ronaldo has both); 62 (Kane and Ronaldo have it) isn't on Genie


def fill_player(row, db, x, who, deal, symbol, rate):
    """A player row's contract, height, value, attributes and what FM keeps hidden."""
    if deal:
        row["Wage"] = f"{symbol}{money(deal[1] * rate)} p/w"
        if deal[3]:
            row["Contract"] = deal[3].isoformat()
    if 140 <= u16(db, x + 146) <= 220:
        row["Height"] = str(u16(db, x + 146))  # cm
    value = u32(db, x + 118)
    row["Transfer Value"] = "Not for Sale" if value == NOT_FOR_SALE else f"{symbol}{money(value * rate)}"
    row.update({a: str(shown(db[x + 64 + i])) for i, a in ATTRIBUTES.items()})
    # hidden: ability (u16s after the three reputations at x+19/21/23), the hidden attributes, personality
    ca, pa = ability(db, x + 25)
    if ca:
        row.update({"Current Ability": str(ca), "Potential Ability": str(pa), "Reputation": str(u16(db, x + 21))})
    row.update({a: str(shown(db[x + 64 + i])) for i, a in HIDDEN.items()})
    row.update({t: str(v) for t, v in who["traits"].items()})
    row["Born"] = who["born"].isoformat()
    row["Left Foot Rating"], row["Right Foot Rating"] = str(shown(db[x + 64 + LEFT_FOOT])), str(shown(db[x + 64 + RIGHT_FOOT]))
    row["Home Reputation"], row["World Reputation"] = str(u16(db, x + 19)), str(u16(db, x + 23))
    moves = u64(db, who["at"] - 8)
    row["Moves"] = f"{moves:x}"  # the raw set, for naming more of them later
    row["Traits"] = "; ".join(name for bit, name in sorted(TRAIT_BITS.items()) if moves >> bit & 1)
    # today's state, as FM's player screen shows it: match exercise and fitness (× 100), jadedness (higher = more tired)
    me, fit = u16(db, x + 131), u16(db, x + 135)
    if me <= 10000 and fit <= 10000:
        row.update({"Match Exercise": f"{me / 100:.1f}", "Fitness": f"{fit / 100:.1f}",
                    "Jadedness": str(int.from_bytes(db[x + 133:x + 135], "little", signed=True))})
    return row


FINANCES = re.compile(rb"(?=\x00\x00\x00\x01(.{4})(.{4})\2(.{4})(.{4}))", re.S)


def finances(db, club_at):
    """The club's money, after its record: `… 00 00 00 01`, balance, transfer budget (twice), wage budget, wages paid
    (pounds; wages a week). {balance, transfer_budget, wage_budget, wages} or None."""
    # the first money block after the club's record is its own; the record grows with each season (Sunderland in 2028:
    # 7,749 bytes in), so the search runs well past where it used to stop
    for m in FINANCES.finditer(db, club_at, club_at + 60_000):
        balance, budget, wage_budget, wages = (int.from_bytes(m.group(i), "little", signed=i == 1) for i in (1, 2, 3, 4))
        if 0 <= budget < 2_000_000_000 and 0 < wages < 100_000_000 and 0 < wage_budget < 100_000_000:
            # a month-end history: one 49-byte entry per month, the season's transfer budget in each; the last is newest
            p = m.start() + 4
            while u32(db, p + 49 + 4) == budget and 0 < u32(db, p + 49 + 16) < 100_000_000:
                p += 49
            balance = int.from_bytes(db[p:p + 4], "little", signed=True)
            wage_budget, wages = u32(db, p + 12), u32(db, p + 16)
            return {"balance": balance, "transfer_budget": budget, "wage_budget": wage_budget, "wages": wages}
    return None


if __name__ == "__main__":
    import time
    t = time.time()
    got = read(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "£")
    print(f"{got['long']} ({got['club']}), {got['date']}: {len(got['players'])} players, {len(got['staff'])} staff "
          f"in {time.time() - t:.1f} s")
    for r in got["players"][:5] + got["staff"][:3]:
        print(r)


# ---- the season's match stats (match_stats_cfg): one zstd frame per match played in the save this season ----
# In a match, every player in a matchday squad has a record `02 [index+1 u32] [position] [flag] 02 [4] 02 [4] [byte]`,
# 227 bytes plus 18 per shot. Offsets from its start, checked against FM's own season totals (17 players, every column):
MATCH_U8 = {"goals": 20, "assists": 56, "dribbles": 58, "fouls": 60, "fouled": 61, "passes": 86, "passes_done": 87,
            "crosses": 89, "crosses_done": 90, "headers": 95, "headers_won": 96, "mistakes": 149, "pressures": 165,
            "pressures_done": 166, "poss_won": 156, "prog_passes": 191}
MATCH_RECORD = re.compile(rb"(?=\x02(....)([\x00-\x3f])([\x00-\x01])\x02(....)\x02(....)([\x00-\x20\xff]))", re.S)
MATCH_COMP = re.compile(rb"([\x04-\x30])\x00\x00\x00([A-Z][A-Za-z .'0-9-]{3,45})")


def match_goals(match, everyone):
    """A match's goals, from its start: [(scorer index+1, assister or 0xFFFFFFFF, type, minute)], type 3 an own goal
    (2 a penalty). Each `[scorer][assister][4][side u8][type u8][minute u8][added u8]` after a u32 count; found by its
    scorers being players in the match."""
    for c in range(300, 900):
        n = struct.unpack_from("<I", match, c)[0]
        if not 1 <= n <= 20:
            continue
        out = []
        for k in range(n):
            sc, asst, _, side, kind, minute, _added = struct.unpack_from("<IIIBBBB", match, c + 4 + 16 * k)
            if sc not in everyone or side > 1 or not 1 <= minute <= 130:
                break
            out.append((sc, asst, kind, minute))
        if len(out) == n:
            return out
    return []


def match_stats(data, sections, mine):
    """Your players' stats this season, from every match in the save: {competitions: {competition: {name: totals}},
    matches: {name: [[competition, rating, minutes, goals, assists, started], …] oldest first}}, totals with apps,
    starts, minutes, rating (sum of match ratings, `rated` matches), xg and MATCH_U8's counts. Only your club's
    matches count (5 or more of your players in it): a call-up to a national side doesn't. Friendlies are kept apart."""
    if "match_stats_cfg" not in sections or not mine:
        return {"competitions": {}, "matches": {}}
    b = section(data, sections, "match_stats_cfg")
    view = memoryview(b)
    starts = []
    p = b.find(b"\x28\xb5\x2f\xfd")
    while p != -1:
        starts.append(p)
        p = b.find(b"\x28\xb5\x2f\xfd", p + 4)
    out, recent = {}, {}
    for a, z in zip(starts, starts[1:] + [len(b)]):
        try:
            match = zstd.ZstdDecompressor().decompress(view[a:z])
        except zstd.ZstdError:
            continue
        found, everyone = {}, set()
        for m in MATCH_RECORD.finditer(match, 0, 40000):
            p = m.start()
            if match[p + 17:p + 22] == b"\x02\xff\xff\xff\xff":  # a name record, not stats
                continue
            pid = int.from_bytes(m.group(1), "little")
            everyone.add(pid)
            if pid in mine and pid not in found:
                found[pid] = p
        if len(found) < 5:  # not one of your club's matches
            continue
        c = MATCH_COMP.search(match, 40, 200)
        comp = c.group(2).decode("utf-8", "replace").strip() if c else "Other"
        when = struct.unpack_from("<f", match, 11)[0]  # rises from match to match: their order in the season
        # goals against you: the other side's, and your own goals (type 3); the other side's own goals are yours
        against = [g[3] for g in match_goals(match, everyone) if (g[0] in mine) == (g[2] == 3)]
        table = out.setdefault(comp, {})
        for pid, p in found.items():
            r = match[p:p + 227]
            if len(r) < 227 or r[140] == 0:  # in the squad, didn't play
                continue
            t = table.setdefault(mine[pid], {"apps": 0, "starts": 0, "minutes": 0, "rating": 0.0, "rated": 0, "xg": 0.0,
                                             "distance": 0.0, **{k: 0 for k in MATCH_U8}})
            on, off = r[71], r[67] or 90
            t["apps"] += 1
            t["starts"] += on == 0
            t["minutes"] += max(0, off - on)
            rating = struct.unpack_from("<H", r, 141)[0] / 100
            if 1 <= rating <= 10:
                t["rating"] += rating
                t["rated"] += 1
            t["xg"] += struct.unpack_from("<f", r, 25)[0]
            conceded = sum(1 for g in against if on <= g <= off)
            t["conceded"] = t.get("conceded", 0) + conceded
            # a clean sheet: he started, wasn't taken off, and nothing went in (the pages show it for keepers)
            t["clean_sheets"] = t.get("clean_sheets", 0) + int(on == 0 and r[67] == 0 and conceded == 0)
            if comp != "Friendly":  # his matches in order, for his form
                recent.setdefault(mine[pid], []).append([round(when, 6), comp, round(rating, 2), max(0, off - on), r[20], r[56], int(on == 0)])
            t["distance"] += struct.unpack_from("<f", r, 145)[0]
            for k, o in MATCH_U8.items():
                t[k] += r[o]
    return {"competitions": out, "matches": {n: [m[1:] for m in sorted(ms)] for n, ms in recent.items()}}
