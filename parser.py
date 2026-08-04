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

import equity

HAND_SPLIT_RE = re.compile(r"(?=^PokerStars (?:Hand|Game) #\d+)", re.MULTILINE)

HEADER_RE = re.compile(
    r"^PokerStars (?:Hand|Game) #(?P<hand_id>\d+):\s*"
    r"(?:Tournament #(?P<tourney_id>\d+),\s*)?"
    r"(?P<game_desc>.+?)\s*-\s*"
    r"(?P<date>\d{4}/\d{2}/\d{2}[ \d:]+)"
    r"[A-Z]*"  # local timezone abbreviation, e.g. ET, WET, CET, GMT, BST
    r"(?:\s*\[[^\]]*\])?"  # optional bracketed duplicate, e.g. [2026/08/03 14:06:32 ET]
    r"\s*$",
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
# Captures every bracket group on the line - stud/draw games re-deal hero each
# street as "Dealt to X [previously known] [new card]", and only grabbing the
# first bracket (as a single-group regex would) misses every card dealt after
# 3rd street/first draw.
DEALT_TO_RE = re.compile(r"^Dealt to (\S+)((?:\s*\[[^\]]*\])+)", re.MULTILINE)
BRACKET_RE = re.compile(r"\[([^\]]*)\]")

# Big blind / blind-level size: cash stakes look like "$0.01/$0.02 USD", tournament
# levels look like "600/1200" (chips, no $ sign) - same pattern works for both, take
# the second (larger) number.
BLIND_SIZE_RE = re.compile(r"\$?([\d,]+(?:\.\d+)?)\s*/\s*\$?([\d,]+(?:\.\d+)?)")

# Tournament buy-in, e.g. "$50+$5" (buy-in + fee) or "$50+$5+$5" (+bounty) at the
# very start of the game description. Free/satellite tournaments with no $ prefix
# won't match, which is fine - buy_in stays unknown rather than wrong.
BUYIN_RE = re.compile(r"^\$([\d,.]+(?:\+\$[\d,.]+)*)")

# Tournament elimination/finish line, e.g. "clynchh finished the tournament in 76th
# place" or "... in 1st place and received $110.00".
FINISH_RE = re.compile(
    r"^(\S+) finished the tournament in (\d+)\w{2} place(?:,? and received \$([\d,.]+))?",
    re.MULTILINE,
)

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
SHOWDOWN_RE = re.compile(r"^\*\*\* SHOW DOWN \*\*\*", re.MULTILINE)

# --- All-in EV inputs -------------------------------------------------
BOARD_LINE_RE = re.compile(r"^\*\*\* (FLOP|TURN|RIVER) \*\*\*((?:\s*\[[^\]]*\])+)", re.MULTILINE)
FOLD_LINE_RE = re.compile(r"^(\S+): folds")
ALLIN_LINE_RE = re.compile(r"^(\S+): .*and is all-in")
SHOWS_RE = re.compile(r"^(\S+): shows \[([^\]]*)\]", re.MULTILINE)

# Games equity.py has evaluators for. 2-7 draw games are excluded - hands
# only ever fully reveal at showdown there too, but modeling a draw (players
# choosing which cards to replace) is a different kind of simulation than
# "deal N more random cards", and isn't built.
EQUITY_GAME_TYPES = {"Hold'em", "Omaha", "Omaha Hi/Lo", "Razz", "Stud", "Stud Hi/Lo"}

STUD_STREET_ORDER = ["3rd street", "4th street", "5th street", "6th street", "7th street"]
STUD_STREET_CARDS = {"3rd street": 3, "4th street": 4, "5th street": 5, "6th street": 6, "7th street": 7}
FLOP_STREET_ORDER = ["hole cards", "flop", "turn", "river"]
FLOP_STREET_BOARD_CARDS = {"hole cards": 0, "flop": 3, "turn": 4, "river": 5}

# Pot-type classification (limped / raised / 3-bet / 4-bet, or stud's
# limped / completed / 3-bet / 4-bet) is based on the opening betting round
# only - preflop for flop/draw games, 3rd street for stud - same convention
# PT4/HM use. "completes it to" and "raises ... to" both count as a raise
# for this purpose: the bring-in itself is bet 1, so 1 raise over it = bet 2
# ("completed"/open-equivalent), 2 raises = bet 3 ("3-bet"), etc. - same
# counting flop games use with the big blind as bet 1.
RAISE_ACTION_RE = re.compile(
    r"^(\S+): (?:raises \$?[\d,.]+ to \$?[\d,.]+|completes it to \$?[\d,.]+)", re.MULTILINE
)
CALL_ACTION_RE = re.compile(r"^(\S+): calls \$?[\d,.]+", re.MULTILINE)
STUD_GAME_TYPES = {"Razz", "Stud", "Stud Hi/Lo"}

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


def _find_hero(text, hero_username=None):
    """Hero = the configured PokerStars username, if set.

    In Hold'em/Omaha only hero's own hole cards appear in "Dealt to X [...]"
    lines, so "first match in the file" used to be a safe proxy for hero. That
    breaks in stud/razz games, where every player gets a "Dealt to X [...]"
    line each street showing their up-cards - the first one is just whoever's
    in the lowest seat number, not hero. With a configured username we match
    it directly instead of guessing by position.
    """
    matches = DEALT_TO_RE.findall(text)
    if hero_username:
        matches = [m for m in matches if m[0] == hero_username]
    if not matches:
        return None, None
    name, brackets = matches[-1]  # last = most complete card set across streets
    cards = " ".join(BRACKET_RE.findall(brackets))
    return name, cards


def _opening_round_block(text):
    """Text of just the first betting round (preflop / 3rd street), used for
    pot-type classification so later-street action doesn't get counted."""
    headers = list(STREET_HEADER_RE.finditer(text))
    if not headers:
        return text
    start = headers[0].end()
    end = headers[1].start() if len(headers) > 1 else len(text)
    return text[start:end]


def _classify_pot_type(text, game_type):
    block = _opening_round_block(text)
    raises = len(RAISE_ACTION_RE.findall(block))
    if raises == 0:
        return "Limped" if CALL_ACTION_RE.search(block) else "Walk"
    if raises == 1:
        return "Completed" if game_type in STUD_GAME_TYPES else "Raised (SRP)"
    if raises == 2:
        return "3-bet"
    if raises == 3:
        return "4-bet"
    return f"{raises + 1}-bet+"


def _went_to_showdown(text, hero_name):
    """Hero reached showdown = didn't fold anywhere in the hand AND the hand
    actually had a showdown (if hero never folds but everyone else does, the
    hand ends uncontested with no showdown - hero still "won without
    showdown" in that case)."""
    if not hero_name:
        return False
    if re.search(rf"^{re.escape(hero_name)}: folds", text, re.MULTILINE):
        return False
    return bool(SHOWDOWN_RE.search(text))


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


def _street_blocks(text):
    """[(street_name_lower, block_text), ...] in order (drops the preamble
    before the first street header - nothing before "*** ... ***" matters
    for all-in/board tracking)."""
    headers = list(STREET_HEADER_RE.finditer(text))
    blocks = []
    for i, h in enumerate(headers):
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        blocks.append((h.group(1).lower(), text[start:end]))
    return blocks


def _board_at_street(text, street_name):
    """Community board cards known as of (and including) street_name."""
    if street_name == "hole cards":
        return []  # preflop - no board dealt yet
    board = []
    for m in BOARD_LINE_RE.finditer(text):
        street = m.group(1).lower()
        groups = BRACKET_RE.findall(m.group(2))
        board.extend(groups[-1].split() if groups else [])
        if street == street_name:
            break
    return board


def _find_lock_street(text, hero_name, street_order):
    """Earliest street after which the pot hero can win is fully decided -
    hero is all-in, or hero isn't but every other still-in player is (hero
    covers the table). None if that never happens, or only happens on the
    final street (nothing left to run out, so no randomness to adjust for)."""
    seats = set(SEAT_RE.findall(text))
    if hero_name not in seats:
        return None

    folded, allin = set(), set()
    for street_name, block in _street_blocks(text):
        if street_name not in street_order:
            continue
        for line in block.splitlines():
            line = line.strip()
            fm = FOLD_LINE_RE.match(line)
            if fm:
                folded.add(fm.group(1))
                continue
            am = ALLIN_LINE_RE.match(line)
            if am:
                allin.add(am.group(1))

        still_in = seats - folded
        if hero_name not in still_in:
            return None  # hero folded - no EV question to ask

        others = still_in - {hero_name}
        hero_locked = hero_name in allin
        others_all_allin = bool(others) and others.issubset(allin)
        if hero_locked or others_all_allin:
            return None if street_name == street_order[-1] else street_name

    return None


def compute_allin_ev(raw_text, game_type, hero_name, hero_cards, went_to_showdown, pot_total, trials=3000):
    """Runs an equity simulation for hands where the pot was effectively
    decided before all the cards were dealt. Returns {"equity_pct",
    "ev_net", "eligible_pot"} or None if this hand doesn't qualify - not an
    all-in-before-completion pot, unsupported game type, or a contesting
    opponent's cards were never revealed (mucked without showing, so their
    range is unknowable from the hand history alone)."""
    if not went_to_showdown or game_type not in EQUITY_GAME_TYPES or not hero_name or pot_total is None:
        return None

    is_stud = game_type in STUD_GAME_TYPES
    street_order = STUD_STREET_ORDER if is_stud else FLOP_STREET_ORDER

    lock_street = _find_lock_street(raw_text, hero_name, street_order)
    if lock_street is None:
        return None

    shown = dict(SHOWS_RE.findall(raw_text))
    if hero_name not in shown:
        shown[hero_name] = hero_cards  # hero's own cards are always known regardless of a "shows" line
    contestants = list(shown.keys())
    if len(contestants) < 2:
        return None

    known_hands = {}
    if is_stud:
        # Stud "shows" lines (and hero's own reconstructed hand, from
        # multi-street "Dealt to" concatenation) list cards in deal order:
        # 2 down, then one up-card per street 3rd-6th, then the final down
        # card on 7th - verified against real hand histories, where the
        # up-cards embedded in that order line up exactly with what each
        # street's "Dealt to" reveal showed. That means slicing to the
        # first N cards for a given street is exactly "what was known then"
        # - including hidden down cards - without needing to identify which
        # specific cards were down vs up.
        board_to_deal, known_board = 0, []
        cutoff = STUD_STREET_CARDS[lock_street]
        cards_per_player = 7 - cutoff
        for name in contestants:
            full_cards = shown[name].split()
            if len(full_cards) < cutoff:
                return None
            cards = equity.parse_cards(" ".join(full_cards[:cutoff]))
            if len(cards) != cutoff:
                return None
            known_hands[name] = cards
    else:
        known_board = equity.parse_cards(" ".join(_board_at_street(raw_text, lock_street)))
        board_to_deal = 5 - FLOP_STREET_BOARD_CARDS[lock_street]
        cards_per_player = 0
        for name in contestants:
            cards = equity.parse_cards(shown[name])
            if len(cards) < 2:
                return None
            known_hands[name] = cards

    if board_to_deal <= 0 and cards_per_player <= 0:
        return None  # already fully dealt - nothing random left to adjust for

    # Real side pots: work out what every player at the table actually put
    # in (including anyone who folded earlier - their money still counts
    # toward whichever pot layer it landed in), then split the total into
    # main-pot/side-pot layers the same way a cardroom would, and only let
    # players compete for the layers they put money into.
    seats = set(SEAT_RE.findall(raw_text))
    investments = {name: _compute_money(raw_text, name)[0] for name in seats}
    hero_inv = investments[hero_name]
    layers = equity.build_side_pots(investments, eligible_to_win=set(contestants))
    if not layers:
        return None

    payouts = equity.simulate_layered_equity(
        game_type, known_hands, known_board, board_to_deal, cards_per_player, layers, trials=trials
    )
    if not payouts or hero_name not in payouts:
        return None

    hero_eligible_pot = sum(amount for amount, eligible in layers if hero_name in eligible)
    hero_payout = payouts[hero_name]
    return {
        "equity_pct": round(hero_payout / hero_eligible_pot, 4) if hero_eligible_pot else 0.0,
        "eligible_pot": round(hero_eligible_pot, 4),
        "ev_net": round(hero_payout - hero_inv, 4),
    }


def parse_hand(raw_text, source_file="", hero_username=None):
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

    # Tournament game_desc has two parenthesized groups - "(Hold'em Limit)" and
    # the blind level "(600/1200)". Cash games only ever have the one. The
    # blind level (last group) is what's actually useful to show/compute from.
    parens = re.findall(r"\(([^)]+)\)", game_desc)
    stakes = (parens[-1] if tourney_id else parens[0]) if parens else ""

    # Stud levels are written "(small bet/big bet)", not "(small blind/big
    # blind)" - stud has no blinds at all, just antes + a bring-in. The
    # completion size on 3rd/4th street is the SMALL bet (the first number),
    # which is the actual unit hands are effectively played in. Using the
    # second number (the big bet, only in play from 5th street on) as "one
    # big blind" would understate every stud/razz hand's size by half.
    big_blind = None
    bb_m = BLIND_SIZE_RE.search(stakes)
    if bb_m:
        big_blind = _parse_money(bb_m.group(1) if game_type in STUD_GAME_TYPES else bb_m.group(2))

    tourney_buyin = None
    if tourney_id:
        buyin_m = BUYIN_RE.match(game_desc.strip())
        if buyin_m:
            tourney_buyin = sum(_parse_money(p.lstrip("$")) for p in buyin_m.group(1).split("+"))

    table_m = TABLE_RE.search(raw_text)
    table_name = table_m.group("table") if table_m else ""

    seats = SEAT_RE.findall(raw_text)
    hero_name, hero_cards = _find_hero(raw_text, hero_username)

    invested, collected, pot_total = _compute_money(raw_text, hero_name)
    net = round(collected - invested, 4)
    pot_type = _classify_pot_type(raw_text, game_type)
    went_to_showdown = _went_to_showdown(raw_text, hero_name)

    allin_ev = compute_allin_ev(raw_text, game_type, hero_name, hero_cards, went_to_showdown, pot_total)

    tourney_finish_place = None
    tourney_payout = None
    if tourney_id and hero_name:
        fm = FINISH_RE.search(raw_text)
        if fm and fm.group(1) == hero_name:
            tourney_finish_place = int(fm.group(2))
            tourney_payout = _parse_money(fm.group(3)) if fm.group(3) else 0.0

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
        "pot_type": pot_type,
        "went_to_showdown": 1 if went_to_showdown else 0,
        "is_allin_ev": 1 if allin_ev else 0,
        "equity_pct": allin_ev["equity_pct"] if allin_ev else None,
        "ev_net": allin_ev["ev_net"] if allin_ev else None,
        "big_blind": big_blind,
        "num_players": len(seats),
        "source_file": source_file,
        "raw_text": raw_text,
        "tourney_game_desc": game_desc,
        "tourney_buyin": tourney_buyin,
        "tourney_finish_place": tourney_finish_place,
        "tourney_payout": tourney_payout,
    }


def parse_file(filepath, hero_username=None):
    """Parse every hand in a file. Returns list of hand dicts (skips unparseable blocks)."""
    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()
    results = []
    for block in split_hands(text):
        parsed = parse_hand(block, source_file=filepath, hero_username=hero_username)
        if parsed:
            results.append(parsed)
    return results
