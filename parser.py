"""
Parses PokerStars .txt hand history files into structured hand dicts.

Design goal: be robust across the whole mixed rotation (Hold'em, Omaha Hi/Lo,
Razz, 7 Card Stud, 7 Card Stud Hi/Lo, 2-7 Triple Draw, 2-7 Single Draw NL,
Badugi) without needing a full per-street action model. We extract metadata
+ hero's net result + the raw text, which is what you actually need to
review a hand and tag a leak. Money tracking uses a per-street running
total so it holds up across differing action grammars (bring-ins,
completes, draws, etc.)
"""
import re
from datetime import datetime

HAND_SPLIT_RE = re.compile(r"(?=^PokerStars (?:Hand|Game) #\d+)", re.MULTILINE)

HEADER_RE = re.compile(
    r"^PokerStars (?:Hand|Game) #(?P<hand_id>\d+):\s*"
    r"(?:Tournament #(?P<tourney_id>\d+),\s*)?"
    r"(?P<game_desc>.+?)\s*-\s*"
    r"(?P<date>\d{4}/\d{2}/\d{2}[ \d:]+(?:ET|CET|GMT)?)\s*$",
    re.MULTILINE,
)

TABLE_RE = re.compile(r"^Table '(?P<table>[^']+)'", re.MULTILINE)
SEAT_RE = re.compile(r"^Seat \d+: (\S+) \(", re.MULTILINE)
STREET_HEADER_RE = re.compile(
    r"^\*\*\* (3rd STREET|4th STREET|5th STREET|6th STREET|7th STREET|RIVER|TURN|FLOP|"
    r"HOLE CARDS|PRE-DRAW|FIRST DRAW|SECOND DRAW|THIRD DRAW|DRAW|"
    r"SHOW DOWN|SUMMARY)",
    re.MULTILINE | re.IGNORECASE,
)
# These headers mark the START of the first betting round (right after blinds/antes
# are posted) rather than a boundary BETWEEN two rounds - flushing here would wrongly
# split a blind post from a raise that happens later in that same round.
NON_FLUSH_HEADERS = {"hole cards", "pre-draw"}
DEALT_TO_RE = re.compile(r"^Dealt to (\S+) \[([^\]]*)\]", re.MULTILINE)

# Money-moving action patterns: (regex, mode) where mode is
# 'add'  -> add amount to current street total
# 'set'  -> current street total becomes amount (raises/completes "to X")
# 'immediate' -> add straight to invested regardless of street tracking (antes/blinds/bring-in-set below handled separately)
ACTION_PATTERNS = [
    (re.compile(r"^(\S+): posts the ante \$?([\d,.]+)"), "immediate"),
    (re.compile(r"^(\S+): posts ante \$?([\d,.]+)"), "immediate"),
    (re.compile(r"^(\S+): posts small blind \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): posts the small blind \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): posts big blind \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): posts the big blind \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): brings[- ]in for \$?([\d,.]+)"), "set"),
    (re.compile(r"^(\S+): completes it to \$?([\d,.]+)"), "set"),
    (re.compile(r"^(\S+): bets \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): calls \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): raises \$?[\d,.]+ to \$?([\d,.]+)"), "set_captured2"),
]

COLLECTED_RE = re.compile(r"^(\S+) collected \$?([\d,.]+) from")
UNCALLED_RE = re.compile(r"^Uncalled bet \(\$?([\d,.]+)\) returned to (\S+)")
POT_TOTAL_RE = re.compile(r"^Total pot \$?([\d,.]+)")
SEAT_COUNT_RE = re.compile(r"^Seat \d+:")

GAME_TYPE_MAP = [
    # order matters: check specific/compound names before generic substrings
    (re.compile(r"7 Card Stud Hi/Lo|Stud Hi/Lo|Stud H/L", re.I), "Stud Hi/Lo"),
    (re.compile(r"Razz", re.I), "Razz"),
    (re.compile(r"7 Card Stud", re.I), "Stud"),
    (re.compile(r"Omaha Hi/Lo|Omaha H/L|Omaha8", re.I), "Omaha Hi/Lo"),
    (re.compile(r"Omaha", re.I), "Omaha"),
    (re.compile(r"2-7 Triple Draw", re.I), "2-7 Triple Draw"),
    (re.compile(r"2-7 Single Draw", re.I), "2-7 Single Draw"),
    (re.compile(r"Badugi", re.I), "Badugi"),
    (re.compile(r"5 Card Draw", re.I), "5 Card Draw"),
    (re.compile(r"Hold'?em", re.I), "Hold'em"),
]

LIMIT_TYPE_MAP = [
    (re.compile(r"No Limit", re.I), "NL"),
    (re.compile(r"Pot Limit", re.I), "PL"),
    (re.compile(r"Limit", re.I), "FL"),
]

DATE_FORMATS = ["%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M"]


def _parse_money(s):
    return float(s.replace(",", ""))


def _classify(patterns, text, default="Unknown"):
    for regex, label in patterns:
        if regex.search(text):
            return label
    return default


def _parse_date(raw):
    raw = raw.strip()
    # strip trailing timezone abbreviation, keep it separately if needed
    raw = re.sub(r"\s*(ET|CET|GMT)\s*$", "", raw).strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).isoformat()
        except ValueError:
            continue
    return raw  # fall back to raw string if unparseable


def split_hands(text):
    """Split a raw hand-history file into individual hand blocks."""
    chunks = HAND_SPLIT_RE.split(text)
    return [c.strip() for c in chunks if c.strip().startswith("PokerStars")]


def _find_hero(text, seats):
    """Hero = whoever has a 'Dealt to X [...]' line before showdown."""
    m = DEALT_TO_RE.search(text)
    if m:
        return m.group(1), m.group(2)
    return None, None


def _compute_money(text, hero_name):
    """Walk the hand line by line tracking hero's per-street contribution."""
    invested = 0.0
    collected = 0.0
    street_total = 0.0
    pot_total = None

    def flush():
        nonlocal invested, street_total
        invested += street_total
        street_total = 0.0

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue

        sh = STREET_HEADER_RE.match(line)
        if sh:
            if sh.group(1).lower() not in NON_FLUSH_HEADERS:
                flush()
            continue

        m = UNCALLED_RE.match(line)
        if m and hero_name and m.group(2) == hero_name:
            # returned bet reduces this street's contribution
            street_total -= _parse_money(m.group(1))
            continue

        m = COLLECTED_RE.match(line)
        if m and hero_name and m.group(1) == hero_name:
            collected += _parse_money(m.group(2))
            continue

        m = POT_TOTAL_RE.match(line)
        if m:
            pot_total = _parse_money(m.group(1))
            continue

        if hero_name and not line.startswith(hero_name + ":"):
            continue
        if not hero_name:
            continue

        matched = False
        for regex, mode in ACTION_PATTERNS:
            mm = regex.match(line)
            if not mm:
                continue
            matched = True
            amt = _parse_money(mm.group(2) if mm.lastindex and mm.lastindex >= 2 else mm.group(1))
            if mode == "immediate":
                invested += amt
            elif mode == "add":
                street_total += amt
            elif mode in ("set", "set_captured2"):
                street_total = amt
            break
        if matched:
            continue

    flush()
    return invested, collected, pot_total


def parse_hand(raw_text, source_file=""):
    """Parse a single hand block. Returns dict or None if unparseable."""
    header = HEADER_RE.search(raw_text)
    if not header:
        return None

    hand_id = header.group("hand_id")
    game_desc = header.group("game_desc")
    tourney_id = header.group("tourney_id")
    date_raw = header.group("date")

    game_type = _classify(GAME_TYPE_MAP, game_desc)
    limit_type = _classify(LIMIT_TYPE_MAP, game_desc, default="")

    stakes_m = re.search(r"\(([^)]+)\)", game_desc)
    stakes = stakes_m.group(1) if stakes_m else ""

    table_m = TABLE_RE.search(raw_text)
    table_name = table_m.group("table") if table_m else ""

    seats = SEAT_RE.findall(raw_text)
    hero_name, hero_cards = _find_hero(raw_text, seats)

    invested, collected, pot_total = _compute_money(raw_text, hero_name)
    net = round(collected - invested, 4)

    return {
        "hand_id": hand_id,
        "game_type": game_type,
        "limit_type": limit_type,
        "stakes": stakes,
        "is_tournament": 1 if tourney_id else 0,
        "tournament_id": tourney_id,
        "table_name": table_name,
        "date_played": _parse_date(date_raw),
        "hero_name": hero_name or "",
        "hero_cards": hero_cards or "",
        "hero_invested": round(invested, 4),
        "hero_collected": round(collected, 4),
        "hero_net": net,
        "pot_total": pot_total,
        "num_players": len(seats),
        "source_file": source_file,
        "raw_text": raw_text,
    }


def parse_file(filepath):
    """Parse every hand in a file. Returns list of hand dicts (skips unparseable blocks)."""
    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    results = []
    for block in split_hands(text):
        parsed = parse_hand(block, source_file=filepath)
        if parsed:
            results.append(parsed)
    return results
