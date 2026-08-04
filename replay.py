"""
Turns a raw hand history into a step-by-step replay.

Produces a list of "frames" - each one a complete snapshot of the table
(every seat's stack, chips in front, known/hidden cards, who's folded, the
board, the pot) at one moment in the hand. The UI just renders frame N, so
stepping back and forward is trivial and can't drift out of sync.

Seats are ordered hero-first and given fixed x/y positions around an
ellipse, so hero always sits bottom-centre regardless of how many players
are in the hand - the rest fill in clockwise from there, matching the order
action actually moves around a real table.
"""
import math
import re

SEAT_LINE_RE = re.compile(r"^Seat (\d+): (.+?) \(\$?([\d,.]+) in chips\)", re.MULTILINE)
BUTTON_RE = re.compile(r"Seat #(\d+) is the button")
STREET_RE = re.compile(r"^\*\*\* ([^*]+?) \*\*\*(.*)$")
DEALT_RE = re.compile(r"^Dealt to (\S+)((?:\s*\[[^\]]*\])+)")
BRACKET_RE = re.compile(r"\[([^\]]*)\]")
SHOWS_RE = re.compile(r"^(\S+): shows \[([^\]]*)\]")
MUCKS_RE = re.compile(r"^(\S+): mucks hand")
COLLECT_RE = re.compile(r"^(\S+) collected \$?([\d,.]+) from")
UNCALLED_RE = re.compile(r"^Uncalled bet \(\$?([\d,.]+)\) returned to (\S+)")

# (regex, kind) - kind drives how the amount is applied to the player's
# contribution for the current betting round.
#   'add' -> amount is extra chips on top of what they already have out
#   'to'  -> amount is their new TOTAL for this round (raises/completes)
ACTIONS = [
    (re.compile(r"^(\S+): posts the ante \$?([\d,.]+)"), "ante"),
    (re.compile(r"^(\S+): posts ante \$?([\d,.]+)"), "ante"),
    (re.compile(r"^(\S+): posts (?:the )?small blind \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): posts (?:the )?big blind \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): brings[- ]in for \$?([\d,.]+)"), "to"),
    (re.compile(r"^(\S+): completes it to \$?([\d,.]+)"), "to"),
    (re.compile(r"^(\S+): raises \$?[\d,.]+ to \$?([\d,.]+)"), "to"),
    (re.compile(r"^(\S+): bets \$?([\d,.]+)"), "add"),
    (re.compile(r"^(\S+): calls \$?([\d,.]+)"), "add"),
]
CHECK_RE = re.compile(r"^(\S+): checks")
FOLD_RE = re.compile(r"^(\S+): folds")
DISCARD_RE = re.compile(r"^(\S+): discards (\d+) card")
STANDPAT_RE = re.compile(r"^(\S+): stands pat")

STUD_GAME_TYPES = {"Razz", "Stud", "Stud Hi/Lo"}
DRAW_GAME_TYPES = {"2-7 Triple Draw", "2-7 Single Draw", "Badugi", "5 Card Draw"}

# How many hole cards each player holds in a non-stud game - used to work out
# how many face-down cards to draw for opponents whose cards we never see.
HOLE_CARD_COUNT = {
    "Hold'em": 2, "Omaha": 4, "Omaha Hi/Lo": 4,
    "2-7 Triple Draw": 5, "2-7 Single Draw": 5, "5 Card Draw": 5, "Badugi": 4,
}

# Total cards each stud player holds once a given street is dealt. PokerStars
# labels stud's 7th street "RIVER", same as a flop game's - hence both keys.
STUD_CARDS_BY_STREET = {
    "3rd street": 3, "4th street": 4, "5th street": 5,
    "6th street": 6, "7th street": 7, "river": 7,
}

# These headers open the first betting round rather than separating two of
# them - the blinds are posted *before* the header and are still live bets
# once it's passed. Clearing the table on these would forget the blinds and
# make a later "raises to X" cost the raiser their blind all over again.
NON_CLEARING_HEADERS = {"hole cards", "pre-draw"}

STREET_LABELS = {
    "hole cards": "Pre-flop", "flop": "Flop", "turn": "Turn", "river": "River",
    "3rd street": "3rd Street", "4th street": "4th Street", "5th street": "5th Street",
    "6th street": "6th Street", "7th street": "7th Street",
    "pre-draw": "Pre-draw", "first draw": "1st Draw", "second draw": "2nd Draw",
    "third draw": "3rd Draw", "draw": "Draw", "show down": "Showdown", "summary": "Summary",
}


def _money(s):
    return float(s.replace(",", ""))


def _seat_positions(n):
    """x/y percentages around an ellipse, index 0 at bottom-centre (hero) and
    the rest clockwise on screen - which is the direction action actually
    moves around a table, so seat order reads naturally."""
    out = []
    for i in range(n):
        angle = math.radians(90 + i * (360.0 / n))
        out.append((50 + 37 * math.cos(angle), 50 + 32 * math.sin(angle)))
    return out


def build_replay(raw_text, game_type, hero_name, big_blind=None):
    """Returns {seats, frames, ...} or None if the hand can't be replayed."""
    seats_raw = [(int(n), name.strip(), _money(chips)) for n, name, chips in SEAT_LINE_RE.findall(raw_text)]
    if not seats_raw or not hero_name:
        return None
    if not any(name == hero_name for _, name, _ in seats_raw):
        return None

    is_stud = game_type in STUD_GAME_TYPES
    hole_count = HOLE_CARD_COUNT.get(game_type, 2)

    btn_m = BUTTON_RE.search(raw_text)
    button_seat = int(btn_m.group(1)) if btn_m else None

    # Rotate so hero is first, keeping the original seat order after them.
    seats_raw.sort(key=lambda s: s[0])
    hero_idx = next(i for i, s in enumerate(seats_raw) if s[1] == hero_name)
    ordered = seats_raw[hero_idx:] + seats_raw[:hero_idx]
    positions = _seat_positions(len(ordered))

    seats = [
        {"name": name, "seat_no": no, "start_stack": chips, "is_hero": name == hero_name,
         "is_button": no == button_seat, "x": round(positions[i][0], 2), "y": round(positions[i][1], 2)}
        for i, (no, name, chips) in enumerate(ordered)
    ]
    names = [s["name"] for s in seats]

    stack = {s["name"]: s["start_stack"] for s in seats}
    street_bet = {n: 0.0 for n in names}
    committed = {n: 0.0 for n in names}
    folded = {n: False for n in names}
    known = {n: [] for n in names}      # cards we can actually see
    total_cards = {n: 0 for n in names}  # how many they hold (seen or not)
    won = {n: 0.0 for n in names}

    board = []
    frames = []
    street_label = "Pre-flop" if not is_stud else "3rd Street"
    paid_out = [0.0]  # chips already pushed to a winner - no longer in the pot

    def snapshot(label, actor=None):
        frames.append({
            "street": street_label,
            "board": list(board),
            "pot": round(sum(committed.values()) - paid_out[0], 2),
            "label": label,
            "seats": [
                {
                    "stack": round(stack[n], 2),
                    "bet": round(street_bet[n], 2),
                    "cards": list(known[n]),
                    "hidden": max(0, total_cards[n] - len(known[n])),
                    # Stud deals two down cards before the first up-card, so
                    # those two backs belong on the left of what we can see;
                    # any later hidden card (7th street) sits on the right.
                    "hidden_before": min(2, max(0, total_cards[n] - len(known[n]))) if is_stud else 0,
                    "folded": folded[n],
                    "acting": n == actor,
                    "won": round(won[n], 2),
                }
                for n in names
            ],
        })

    def apply_amount(player, amount, kind):
        if kind == "ante":
            delta = amount
            committed[player] += delta
        elif kind == "to":
            delta = amount - street_bet[player]
            street_bet[player] = amount
            committed[player] += delta
        else:  # 'add'
            delta = amount
            street_bet[player] += delta
            committed[player] += delta
        stack[player] = max(0.0, stack[player] - delta)

    # Frames for "everyone posted an ante" and "a new street was dealt" are
    # emitted lazily: antes collapse into one step instead of one per player,
    # and a street's frame waits until its "Dealt to" lines have been read so
    # the new cards are actually visible in it.
    pending = {"antes": False, "street": None}

    def flush():
        if pending["antes"]:
            pending["antes"] = False
            snapshot("Antes posted")
        if pending["street"]:
            label = pending["street"]
            pending["street"] = None
            snapshot("Hole cards dealt" if label == "Pre-flop" else f"{label} dealt")

    for raw_line in raw_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        m = STREET_RE.match(line)
        if m:
            flush()
            key = m.group(1).strip().lower()
            rest = m.group(2)
            # New betting round: whatever was out in front goes to the pot.
            if key not in NON_CLEARING_HEADERS:
                for n in names:
                    street_bet[n] = 0.0
            street_label = STREET_LABELS.get(key, m.group(1).strip().title())

            if key in ("flop", "turn", "river") and not is_stud:
                groups = BRACKET_RE.findall(rest)
                if groups:
                    board = groups[0].split() if key == "flop" else board + groups[-1].split()
            if is_stud and key in STUD_CARDS_BY_STREET:
                n_cards = STUD_CARDS_BY_STREET[key]
                for n in names:
                    if not folded[n]:
                        total_cards[n] = n_cards
            elif key in ("hole cards", "pre-draw"):
                for n in names:
                    total_cards[n] = hole_count

            if key not in ("show down", "summary"):
                pending["street"] = street_label
            continue

        m = DEALT_RE.match(line)
        if m:
            player, brackets = m.group(1), m.group(2)
            groups = BRACKET_RE.findall(brackets)
            if player in known:
                if len(groups) == 1:
                    known[player] = groups[0].split()
                else:
                    known[player] = known[player] + groups[-1].split()
                if not is_stud:
                    total_cards[player] = max(total_cards[player], len(known[player]))
            continue

        m = SHOWS_RE.match(line)
        if m and m.group(1) in known:
            flush()
            known[m.group(1)] = m.group(2).split()
            total_cards[m.group(1)] = len(known[m.group(1)])
            snapshot(f"{m.group(1)} shows", actor=m.group(1))
            continue

        m = MUCKS_RE.match(line)
        if m and m.group(1) in known:
            flush()
            snapshot(f"{m.group(1)} mucks", actor=m.group(1))
            continue

        m = UNCALLED_RE.match(line)
        if m and m.group(2) in stack:
            flush()
            amt, player = _money(m.group(1)), m.group(2)
            stack[player] += amt
            street_bet[player] = max(0.0, street_bet[player] - amt)
            committed[player] = max(0.0, committed[player] - amt)
            snapshot(f"Uncalled {amt:,.0f} returned to {player}", actor=player)
            continue

        m = COLLECT_RE.match(line)
        if m and m.group(1) in stack:
            flush()
            amt, player = _money(m.group(2)), m.group(1)
            stack[player] += amt
            won[player] += amt
            paid_out[0] += amt
            snapshot(f"{player} wins {amt:,.0f}", actor=player)
            continue

        matched = False
        for regex, kind in ACTIONS:
            am = regex.match(line)
            if not am or am.group(1) not in stack:
                continue
            player, amount = am.group(1), _money(am.group(2))
            if kind == "ante":
                apply_amount(player, amount, kind)
                pending["antes"] = True
            else:
                flush()
                apply_amount(player, amount, kind)
                verb = line.split(": ", 1)[1].rstrip() if ": " in line else line
                snapshot(f"{player} {verb}", actor=player)
            matched = True
            break
        if matched:
            continue

        for regex, verb in ((CHECK_RE, "checks"), (FOLD_RE, "folds"), (STANDPAT_RE, "stands pat")):
            am = regex.match(line)
            if am and am.group(1) in stack:
                flush()
                if verb == "folds":
                    folded[am.group(1)] = True
                snapshot(f"{am.group(1)} {verb}", actor=am.group(1))
                matched = True
                break
        if matched:
            continue

        am = DISCARD_RE.match(line)
        if am and am.group(1) in stack:
            flush()
            snapshot(f"{am.group(1)} discards {am.group(2)}", actor=am.group(1))
            continue

    flush()

    if not frames:
        return None

    return {
        "game_type": game_type,
        "hero": hero_name,
        "is_stud": is_stud,
        "big_blind": big_blind,
        "seats": seats,
        "frames": frames,
    }
