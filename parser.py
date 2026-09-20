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

# PokerStars screen names can contain internal spaces (e.g. "James UK7"), so a
# bare \S+ silently drops those players' action/result lines. Every such line is
# "<name>: <verb> ..." or "<name> <verb> ...", and a name never contains ": ",
# so match the name as a minimal run up to the delimiter instead of one token.
NAME = r"(.+?)"

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
# Only the roster lines up top ("Seat 3: Necrogenesis ($30 in chips)"), never
# the SUMMARY block's "Seat 3: Necrogenesis (big blind) folded ..." - hence the
# "in chips" tail. Without it, names with a space (unmatched, so skipped) and
# names without one (matched twice) both threw off num_players.
SEAT_RE = re.compile(rf"^Seat \d+: {NAME} \([^)]*\bin chips\b", re.MULTILINE)
# "Table 'Cepheus VI' 6-max Seat #5 is the button". Absent in stud/razz,
# which have no button at all - antes and a bring-in decide the order there,
# so those games genuinely have no position and must report none rather
# than a made-up one.
BUTTON_SEAT_RE = re.compile(r"Seat #(\d+) is the button")
# Same roster lines as SEAT_RE, but keeping the seat number and whatever
# follows the stack - position is worked out from where each seat sits
# relative to the button, and the tail is what marks a player as sitting out.
SEAT_NO_RE = re.compile(rf"^Seat (\d+): {NAME} \([^)]*\bin chips\b[^)]*\)(.*)$", re.MULTILINE)
# Seat lines in the SUMMARY block. A player who was dealt in always has
# something after their name there (a position, a fold, a result); one who
# was not gets a bare "Seat 5: James UK7".
SUMMARY_SEAT_RE = re.compile(r"^Seat (\d+): (.*)$", re.MULTILINE)

STREET_HEADER_RE = re.compile(
    r"^\*\*\* (3rd STREET|4th STREET|5th STREET|6th STREET|7th STREET|RIVER|TURN|FLOP|"
    r"HOLE CARDS|PRE-DRAW|DEALING HANDS|FIRST DRAW|SECOND DRAW|THIRD DRAW|DRAW|"
    r"SHOW DOWN|SUMMARY)",
    re.MULTILINE | re.IGNORECASE,
)
# These headers mark the START of the first betting round (right after blinds/antes
# are posted) rather than a boundary BETWEEN two rounds - flushing here would wrongly
# split a blind post from a raise that happens later in that same round.
NON_FLUSH_HEADERS = {"hole cards", "pre-draw", "dealing hands"}

# Single Draw hands print no header at all between the pre-draw and post-draw
# betting rounds - only "*** DEALING HANDS ***" appears once, before the
# pre-draw action, then nothing until "*** SHOW DOWN ***"/"*** SUMMARY ***".
# The discard/stand-pat sequence is the only signal that round has ended, so
# it doubles as a flush point. Safe to flush on every line in the sequence
# (not just the first) - street_total is back to 0 after the first flush, so
# later discard/stand-pat lines are no-ops.
DISCARD_RE = re.compile(r"^.+?: discards \d+ card")
STANDPAT_RE = re.compile(r"^.+?: stands pat")
# Captures every bracket group on the line - stud/draw games re-deal hero each
# street as "Dealt to X [previously known] [new card]", and only grabbing the
# first bracket (as a single-group regex would) misses every card dealt after
# 3rd street/first draw.
DEALT_TO_RE = re.compile(rf"^Dealt to {NAME}((?:\s*\[[^\]]*\])+)", re.MULTILINE)
BRACKET_RE = re.compile(r"\[([^\]]*)\]")

# The two numbers in a level/stakes string, e.g. "$0.01/$0.02 USD" or
# "600/1200". What they MEAN depends on the betting structure - see
# _big_blind_size below.
BLIND_SIZE_RE = re.compile(r"\$?([\d,]+(?:\.\d+)?)\s*/\s*\$?([\d,]+(?:\.\d+)?)")

# The big blind a player actually put in. This is the definitive source -
# it doesn't depend on interpreting the level notation at all.
BIG_BLIND_POST_RE = re.compile(r"^.+?: posts (?:the )?big blind \$?([\d,]+(?:\.\d+)?)", re.MULTILINE)

# Tournament buy-in, e.g. "$50+$5" (buy-in + fee) or "$50+$5+$5" (+bounty) at the
# very start of the game description. Free/satellite tournaments with no $ prefix
# won't match, which is fine - buy_in stays unknown rather than wrong.
BUYIN_RE = re.compile(r"^\$([\d,]+(?:\.\d+)?(?:\+\$[\d,]+(?:\.\d+)?)*)")

# Pulls just the tournament's own name - "8-Game", "HORSE", "Hold'em No
# Limit" - out of the full game_desc string, e.g. "$2.40+$2.50+$0.60 USD
# 8-Game (Triple Draw 2-7 Lowball Limit) - Level XVII (500/1000)". The
# parenthesised part names whichever single game in the rotation happens to
# be running at the level the hero busted at - not the tournament's
# identity, and not worth showing next to it on a tournament-level list.
TOURNEY_NAME_RE = re.compile(r"^(?:\$[\d,.]+(?:\+\$[\d,.]+)*\s+(?:[A-Z]{3}\s+)?)?(.+?)(?:\s*\(|\s*-\s*Level\b|$)")


def _clean_tourney_name(game_desc):
    m = TOURNEY_NAME_RE.match(game_desc)
    return m.group(1).strip() if m else game_desc

# Tournament elimination/finish line, e.g. "clynchh finished the tournament in 76th
# place" or "... in 1st place and received $110.00". The "in Nth place" part
# is itself optional - PokerStars sometimes writes just "X finished the
# tournament" with no place at all, seen in real hand histories when several
# players bust in the same all-in and the exact order between them isn't
# resolved in this hand's text. That still means the hand ended the
# tournament for X, just without knowing exactly where they placed.
FINISH_RE = re.compile(
    rf"^{NAME} finished the tournament(?: in (\d+)\w{{2}} place)?(?:,? and received \$([\d,]+(?:\.\d+)?))?",
    re.MULTILINE,
)

# Progressive knockout bounty payout, e.g. "clynchh wins $7.35 for eliminating
# pot2steal? and their own bounty increases by $7.35 to $12.25" - real cash,
# separate from the chip pot for the hand, and can happen on any hand in the
# tournament (not just the last one), so it's summed hand-by-hand rather than
# read off a single summary line the way the finish payout is.
BOUNTY_WIN_RE = re.compile(rf"^{NAME} wins \$([\d,]+(?:\.\d+)?) for eliminating", re.MULTILINE)

# Money-moving action patterns: (regex, mode) where mode is
# 'add'  -> add amount to current street total
# 'set'  -> current street total becomes amount (raises/completes "to X")
# 'immediate' -> add straight to invested regardless of street tracking (antes/blinds/bring-in-set below handled separately)
ACTION_PATTERNS = [
    (re.compile(rf"^{NAME}: posts the ante \$?([\d,]+(?:\.\d+)?)"), "immediate"),
    (re.compile(rf"^{NAME}: posts ante \$?([\d,]+(?:\.\d+)?)"), "immediate"),
    (re.compile(rf"^{NAME}: posts small blind \$?([\d,]+(?:\.\d+)?)"), "add"),
    (re.compile(rf"^{NAME}: posts the small blind \$?([\d,]+(?:\.\d+)?)"), "add"),
    (re.compile(rf"^{NAME}: posts big blind \$?([\d,]+(?:\.\d+)?)"), "add"),
    (re.compile(rf"^{NAME}: posts the big blind \$?([\d,]+(?:\.\d+)?)"), "add"),
    (re.compile(rf"^{NAME}: brings[- ]in for \$?([\d,]+(?:\.\d+)?)"), "set"),
    (re.compile(rf"^{NAME}: completes it to \$?([\d,]+(?:\.\d+)?)"), "set"),
    (re.compile(rf"^{NAME}: bets \$?([\d,]+(?:\.\d+)?)"), "add"),
    (re.compile(rf"^{NAME}: calls \$?([\d,]+(?:\.\d+)?)"), "add"),
    (re.compile(rf"^{NAME}: raises \$?[\d,]+(?:\.\d+)? to \$?([\d,]+(?:\.\d+)?)"), "set_captured2"),
]

COLLECTED_RE = re.compile(rf"^{NAME} collected \$?([\d,]+(?:\.\d+)?) from")
UNCALLED_RE = re.compile(r"^Uncalled bet \(\$?([\d,]+(?:\.\d+)?)\) returned to (.+?)\s*$")
POT_TOTAL_RE = re.compile(r"^Total pot \$?([\d,]+(?:\.\d+)?)")
SEAT_COUNT_RE = re.compile(r"^Seat \d+:")
SHOWDOWN_RE = re.compile(r"^\*\*\* SHOW DOWN \*\*\*", re.MULTILINE)

# --- All-in EV inputs -------------------------------------------------
BOARD_LINE_RE = re.compile(r"^\*\*\* (FLOP|TURN|RIVER) \*\*\*((?:\s*\[[^\]]*\])+)", re.MULTILINE)
FOLD_LINE_RE = re.compile(rf"^{NAME}: folds")
ALLIN_LINE_RE = re.compile(rf"^{NAME}: .*and is all-in")
SHOWS_RE = re.compile(rf"^{NAME}: shows \[([^\]]*)\]", re.MULTILINE)

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
    rf"^{NAME}: (?:raises \$?[\d,]+(?:\.\d+)? to \$?[\d,]+(?:\.\d+)?|completes it to \$?[\d,]+(?:\.\d+)?)", re.MULTILINE
)
CALL_ACTION_RE = re.compile(rf"^{NAME}: calls \$?[\d,]+(?:\.\d+)?", re.MULTILINE)
STUD_GAME_TYPES = {"Razz", "Stud", "Stud Hi/Lo"}

GAME_TYPE_MAP = [
    # order matters: check specific/compound names before generic substrings
    (re.compile(r"7 Card Stud Hi/Lo|Stud Hi/Lo|Stud H/L", re.I), "Stud Hi/Lo"),
    (re.compile(r"Razz", re.I), "Razz"),
    (re.compile(r"7 Card Stud", re.I), "Stud"),
    (re.compile(r"Omaha Hi/Lo|Omaha H/L|Omaha8", re.I), "Omaha Hi/Lo"),
    (re.compile(r"Omaha", re.I), "Omaha"),
    # Real PokerStars text is "Triple Draw 2-7 Lowball" / "Single Draw 2-7
    # Lowball" - reversed from what the earlier "2-7 Triple/Single Draw"
    # patterns expected, so those never matched a single real hand and
    # every draw hand silently fell through to "Unknown" (which the
    # replayer correctly refuses to render, making them look unsupported
    # rather than just misclassified). Matches either order.
    (re.compile(r"Triple Draw 2-7|2-7 Triple Draw", re.I), "2-7 Triple Draw"),
    (re.compile(r"Single Draw 2-7|2-7 Single Draw", re.I), "2-7 Single Draw"),
    (re.compile(r"Badugi", re.I), "Badugi"),
    (re.compile(r"5 Card Draw", re.I), "5 Card Draw"),
    (re.compile(r"Hold'?em", re.I), "Hold'em"),
    # "All-In Poker" is Hold'em - same two hole cards, same board, same hand
    # rankings - with a betting structure that only allows shoving or
    # folding. PokerStars never writes "Hold'em" in its description, so it
    # used to fall through to "Unknown", which meant no replayer and no
    # all-in EV for any of those hands. The structure is what differs, and
    # that already has a home in limit_type.
    (re.compile(r"All-?In Poker", re.I), "Hold'em"),
]

LIMIT_TYPE_MAP = [
    # Ahead of "No Limit", which the All-In Poker description also contains.
    # Keeping it as its own structure stops 92 shove-or-fold hands being
    # averaged into the real No Limit Hold'em numbers, where a VPIP or an
    # aggression factor from one means nothing about the other.
    (re.compile(r"All-?In Poker", re.I), "AI"),
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


def _split_opening_round(text):
    """(opening round text, everything after it). The opening round is
    preflop / 3rd street / pre-draw depending on the game."""
    headers = list(STREET_HEADER_RE.finditer(text))
    if not headers:
        return text, ""
    start = headers[0].end()
    end = headers[1].start() if len(headers) > 1 else len(text)
    block = text[start:end]
    # Single Draw prints no header between its two betting rounds - only
    # "*** DEALING HANDS ***" up front, then discards, then more betting - so
    # the first discard/stand-pat is what ends the opening round there.
    m = re.search(r"^.+?: (?:discards \d+ card|stands pat)", block, re.MULTILINE)
    if m:
        return block[: m.start()], block[m.start():] + text[end:]
    return block, text[end:]


def _opening_round_block(text):
    """Text of just the first betting round (preflop / 3rd street / pre-draw),
    used for pot-type classification so later-street action doesn't get counted."""
    return _split_opening_round(text)[0]


def _big_blind_size(text, stakes, game_type, limit_type):
    """Size of one big blind, for normalising results into BB.

    The two numbers in a level mean different things depending on the
    betting structure, which makes reading them alone unreliable:

      - No-limit/pot-limit "1/2"  -> small blind / big blind, so BB = 2
      - Fixed-limit "600/1200"    -> small bet / BIG BET. The big blind is
        the SMALL bet (600); the big bet only applies from the turn on.
      - Stud/razz "4000/8000"     -> small bet / big bet, and there are no
        blinds at all (antes + a bring-in instead). The small bet is the
        closest equivalent unit, which is what the completion is.

    So prefer what a player actually posted - that's unambiguous - and only
    fall back to interpreting the level when nobody posts a blind (stud).
    """
    level = BLIND_SIZE_RE.search(stakes or "")
    if not level:
        return None
    small_bet = _parse_money(level.group(1))
    big_bet = _parse_money(level.group(2))
    expected = small_bet if (limit_type == "FL" or game_type in STUD_GAME_TYPES) else big_bet

    posted = [_parse_money(x) for x in BIG_BLIND_POST_RE.findall(text)]
    if expected in posted:
        return expected  # the hand itself confirms the structural reading
    # Otherwise believe a posted blind, as long as it matches the level at all -
    # a player all-in for less than a full blind shouldn't set the unit.
    valid = [p for p in posted if p in (small_bet, big_bet)]
    if valid:
        return max(valid)
    return expected


def _hero_vpip(text, hero_name):
    """VPIP = hero voluntarily put money in during the opening round (calls,
    bets, raises, completes). Posting a blind/ante/bring-in doesn't count -
    those are forced - and neither does folding without ever having acted."""
    if not hero_name:
        return False
    block = _opening_round_block(text)
    pattern = re.compile(rf"^{re.escape(hero_name)}: (?:calls|bets|raises|completes it to)\b", re.MULTILINE)
    return bool(pattern.search(block))


# The late-position names, filled in backwards from the seat before the
# button. Only the seats between the big blind and the button get these; the
# blinds and the button itself are named from the hand history directly.
_LATE_POSITIONS = ["CO", "HJ", "LJ"]

# Stud has no button or blinds. The forced bet is the bring-in, posted by
# the worst up-card on 3rd street, and that's the only fixed reference point
# a stud hand has - so position there is distance from the bring-in.
BRING_IN_RE = re.compile(rf"^{NAME}: brings in for", re.MULTILINE)
# Any line where a player does something ("Name: folds", "Name: posts the
# ante 80"). Used to tell a seat that's in the hand from one that isn't.
ACTOR_LINE_RE = re.compile(rf"^{NAME}: (?:posts|folds|checks|calls|bets|raises|brings|completes|discards|stands|shows|mucks)\b", re.MULTILINE)
SMALL_BLIND_POSTER_RE = re.compile(rf"^{NAME}: posts (?:the )?small blind", re.MULTILINE)
BIG_BLIND_POSTER_RE = re.compile(rf"^{NAME}: posts (?:the )?big blind", re.MULTILINE)


def _middle_labels(m):
    """Labels for the m seats between the big blind and the button.

    Named backwards from the button - CO, HJ, LJ - because it's distance
    from the button, not from the blinds, that gives a seat its character.
    Only once those four are used up does a table need UTG names, which is
    why 6-max reads LJ/HJ/CO with no UTG at all, 7-handed adds UTG, 8-handed
    UTG+1 and 9-handed UTG+2."""
    if m <= 0:
        return []
    labels = [None] * m
    i = m - 1
    for name in _LATE_POSITIONS:
        if i < 0:
            break
        labels[i] = name
        i -= 1
    for j in range(i + 1):
        labels[j] = "UTG" if j == 0 else f"UTG+{j}"
    return labels


def _dealt_in_seats(text):
    """[(seat_no, name)] for the players in the hand, in seat order.

    The roster lists everyone AT the table, and a seat that isn't in the
    hand would shift every position after it by one if it were counted.

    "is sitting out" alone isn't enough to rule a seat out, though: the flag
    also gets set on a player who is in THIS hand but won't be dealt the
    next one, and they still post and still act. Anyone who does either is
    kept regardless of the flag."""
    acted = {m.group(1).strip() for m in ACTOR_LINE_RE.finditer(text)}
    seats = [
        (int(no), name.strip())
        for no, name, tail in SEAT_NO_RE.findall(text)
        if "sitting out" not in tail or name.strip() in acted
    ]
    seats.sort()
    return seats


def _bring_in_position(text, hero_name):
    """Hero's seat on 3rd street, counted round from the bring-in: "BI",
    then "+1", "+2" and so on in the order the seats actually act.

    How far the numbering goes depends on how many were dealt in - +5 is the
    last seat six-handed, +6 seven-handed, +4 five-handed. That does mean a
    single label covers the last seat at one table size and a middle seat at
    another, so a positional report spanning several table sizes blurs the
    late seats together. Filter to one table size to read those cleanly.

    This is 3rd street's order. From 4th onwards the best exposed board acts
    first, so stud position is a 3rd-street idea - which is where most of
    the decisions get made."""
    m = BRING_IN_RE.search(text)
    if m:
        bring_in = m.group(1).strip()
    else:
        # At antes big enough that the low card is already all-in, there is
        # no bring-in at all and 3rd street simply opens with a bet. The
        # seat that acts first is the one the forced bet would have fallen
        # to, and distance from where the action starts is what this is
        # measuring either way.
        opening = _opening_round_block(text)
        first = ACTOR_LINE_RE.search(opening)
        if not first:
            return None
        bring_in = first.group(1).strip()
    seats = _dealt_in_seats(text)
    names = [n for _, n in seats]
    if bring_in not in names or hero_name not in names:
        return None
    offset = (names.index(hero_name) - names.index(bring_in)) % len(names)
    return "BI" if offset == 0 else f"+{offset}"


def _betting_rounds(text):
    """[(name, block)] one entry per betting round, opening round first.

    Showdown and summary are not betting rounds and are dropped. Single Draw
    is the awkward one: it prints "*** DEALING HANDS ***" and then nothing
    until the showdown, so its two rounds share a block and the discard
    sequence is the only thing separating them."""
    headers = list(STREET_HEADER_RE.finditer(text))
    rounds = []
    for i, h in enumerate(headers):
        name = h.group(1).lower()
        if name in ("show down", "summary"):
            break
        start = h.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        block = text[start:end]
        if not rounds and name in NON_FLUSH_HEADERS:
            m = re.search(r"^.+?: (?:discards \d+ card|stands pat)", block, re.MULTILINE)
            if m:
                rounds.append((name, block[: m.start()]))
                rounds.append(("draw", block[m.start():]))
                continue
        rounds.append((name, block))
    return rounds


def _streets_seen(text, hero_name):
    """How many betting rounds past the opening one hero was still in for.

    0 means hero folded in the opening round, or the hand ended there (a
    walk, or everyone folding to hero's raise). 1 is the flop / 4th street /
    first draw, and so on. Folding ON a street still counts it - hero saw
    the cards and made a decision, which is exactly what the stat is asking
    about."""
    if not hero_name:
        return 0
    rounds = _betting_rounds(text)
    if not rounds:
        return 0
    folds = re.compile(rf"^{re.escape(hero_name)}: folds", re.MULTILINE)
    if folds.search(rounds[0][1]):
        return 0
    seen = 0
    for _, block in rounds[1:]:
        seen += 1
        if folds.search(block):
            break
    return seen


def _hero_position(text, hero_name):
    """Hero's seat relative to the button, or None when the game has no
    button (stud and razz) or the hand doesn't say where it was.

    The blinds and the button are taken from the hand history rather than
    counted round from the button, because those three are stated outright
    and counting is what goes wrong when a seat is empty, sitting out, or
    posting a dead blind. Only the seats in between have to be inferred, and
    an error there can't leak into the blinds or the button."""
    if not hero_name:
        return None

    bm = BUTTON_SEAT_RE.search(text)
    if not bm:
        return _bring_in_position(text, hero_name)
    button_seat = int(bm.group(1))

    sb = SMALL_BLIND_POSTER_RE.search(text)
    bb = BIG_BLIND_POSTER_RE.search(text)
    sb_name = sb.group(1).strip() if sb else None
    bb_name = bb.group(1).strip() if bb else None

    # Heads-up the button posts the small blind, so this order matters: the
    # seat is the small blind first and the button only incidentally.
    if sb_name and hero_name == sb_name:
        return "SB"
    if bb_name and hero_name == bb_name:
        return "BB"

    seats = _dealt_in_seats(text)
    if len(seats) < 2:
        return None
    numbers = [no for no, _ in seats]
    names = [n for _, n in seats]
    if hero_name not in names:
        return None
    if dict(seats).get(button_seat) == hero_name:
        return "BTN"

    # Everyone left sits between the big blind and the button. Walk round
    # from the seat after the big blind and stop at the button.
    if bb_name and bb_name in names:
        anchor = names.index(bb_name)
    elif button_seat in numbers:
        # No big blind posted - the two seats after the button are the blinds.
        anchor = (numbers.index(button_seat) + 1) % len(seats)
    else:
        return None

    # A button parked on a seat that's sitting out is a dead button: nobody
    # holds that position, and the last player before it acts last. Stopping
    # at the small blind instead then gives the same walk, minus the button.
    if button_seat in numbers:
        stop = numbers.index(button_seat)
    elif sb_name and sb_name in names:
        stop = names.index(sb_name)
    else:
        return None

    middle = []
    i = (anchor + 1) % len(seats)
    while i != stop and len(middle) < len(seats):
        middle.append(seats[i][1])
        i = (i + 1) % len(seats)
    for name, label in zip(middle, _middle_labels(len(middle))):
        if name == hero_name:
            return label
    return None


def _hero_raised_opening(text, hero_name):
    """Hero raised (or, in stud, completed) in the opening round - the
    mixed-game equivalent of PFR. Completing in stud is the open-raise, not a
    call, so it belongs on the same line as a raise."""
    if not hero_name:
        return False
    opening = _opening_round_block(text)
    pattern = re.compile(rf"^{re.escape(hero_name)}: (?:raises|completes it to)\b", re.MULTILINE)
    return bool(pattern.search(opening))


def _postflop_action_counts(text, hero_name):
    """(bets+raises, calls) for hero after the opening round - the two inputs
    to aggression factor.

    Kept as raw counts rather than a per-hand ratio because they have to be
    summed across thousands of hands and divided once at the end. Averaging
    per-hand ratios would weight a hand where hero acted once the same as one
    where they acted ten times, and hands with no calls have no ratio at
    all."""
    if not hero_name:
        return 0, 0
    _, rest = _split_opening_round(text)
    escaped = re.escape(hero_name)
    aggressive = len(re.findall(rf"^{escaped}: (?:bets|raises)\b", rest, re.MULTILINE))
    calls = len(re.findall(rf"^{escaped}: calls\b", rest, re.MULTILINE))
    return aggressive, calls


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

        if DISCARD_RE.match(line) or STANDPAT_RE.match(line):
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

    big_blind = _big_blind_size(raw_text, stakes, game_type, limit_type)

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
    vpip = _hero_vpip(raw_text, hero_name)
    hero_position = _hero_position(raw_text, hero_name)
    streets_seen = _streets_seen(raw_text, hero_name)
    saw_next_street = streets_seen >= 1
    raised_opening = _hero_raised_opening(raw_text, hero_name)
    postflop_aggr, postflop_calls = _postflop_action_counts(raw_text, hero_name)

    allin_ev = compute_allin_ev(raw_text, game_type, hero_name, hero_cards, went_to_showdown, pot_total)

    tourney_finished = False
    tourney_finish_place = None
    tourney_payout = None
    tourney_bounty_won = 0.0
    if tourney_id and hero_name:
        fm = FINISH_RE.search(raw_text)
        if fm and fm.group(1) == hero_name:
            tourney_finished = True
            if fm.group(2):
                tourney_finish_place = int(fm.group(2))
            tourney_payout = _parse_money(fm.group(3)) if fm.group(3) else 0.0
        tourney_bounty_won = sum(
            _parse_money(amt) for name, amt in BOUNTY_WIN_RE.findall(raw_text) if name == hero_name
        )

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
        "vpip": 1 if vpip else 0,
        "hero_position": hero_position,
        "saw_next_street": 1 if saw_next_street else 0,
        "streets_seen": streets_seen,
        "raised_opening": 1 if raised_opening else 0,
        "postflop_aggr": postflop_aggr,
        "postflop_calls": postflop_calls,
        "is_allin_ev": 1 if allin_ev else 0,
        "equity_pct": allin_ev["equity_pct"] if allin_ev else None,
        "ev_net": allin_ev["ev_net"] if allin_ev else None,
        "big_blind": big_blind,
        "num_players": len(seats),
        "source_file": source_file,
        "raw_text": raw_text,
        "tourney_game_desc": _clean_tourney_name(game_desc) if tourney_id else game_desc,
        "tourney_buyin": tourney_buyin,
        "tourney_finished": tourney_finished,
        "tourney_finish_place": tourney_finish_place,
        "tourney_payout": tourney_payout,
        "bounty_won": round(tourney_bounty_won, 4),
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
