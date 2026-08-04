"""
All-in equity calculator: hand evaluators (high, and ace-to-five low for
razz/8-or-better) plus a Monte Carlo runout simulator, used to compute
"All-in EV" - what a hand was mathematically worth at the moment no more
decisions were possible, vs. what the actual card runout paid out.

Monte Carlo (not exact enumeration) is used uniformly across every game
here, including community-card games where exact enumeration would be
possible - a preflop multi-way holdem all-in has hundreds of thousands of
possible run-outs, which is too slow in pure Python to compute per-hand
at import time. A few thousand trials converges within ~1% of true equity,
which is precise enough for a "how'd I run" stat.

Card representation: (rank, suit) tuples, rank 2-14 (ace high = 14).
Ace-low contexts (razz, 8-or-better low) remap 14 -> 1 internally.
"""
import random
from collections import Counter
from itertools import combinations

RANKS = "23456789TJQKA"
SUITS = "cdhs"

CARD_RE_CACHE = {}


def parse_card(s):
    """'Kc' -> (13, 'c'). Returns None for anything unparseable."""
    s = s.strip()
    if len(s) != 2:
        return None
    rank_c, suit_c = s[0].upper(), s[1].lower()
    if rank_c not in RANKS or suit_c not in SUITS:
        return None
    return (RANKS.index(rank_c) + 2, suit_c)


def parse_cards(s):
    """'Kc Td 9h' -> [(13,'c'), (10,'d'), (9,'h')], skipping anything unparseable."""
    out = []
    for tok in s.split():
        c = parse_card(tok)
        if c:
            out.append(c)
    return out


def full_deck():
    return [(r, s) for r in range(2, 15) for s in SUITS]


# ---------------------------------------------------------------------------
# High-hand evaluation (standard poker ranking). Returns a tuple where a
# LARGER tuple is a better hand - directly comparable with max()/>.
# ---------------------------------------------------------------------------

def _high_key_5(cards5):
    ranks = sorted((r for r, s in cards5), reverse=True)
    suits = [s for r, s in cards5]
    is_flush = len(set(suits)) == 1

    unique_ranks = sorted(set(ranks), reverse=True)
    is_straight = False
    straight_high = None
    if len(unique_ranks) == 5:
        if unique_ranks[0] - unique_ranks[4] == 4:
            is_straight, straight_high = True, unique_ranks[0]
        elif unique_ranks == [14, 5, 4, 3, 2]:
            is_straight, straight_high = True, 5

    counts = Counter(ranks)
    groups = sorted(counts.items(), key=lambda rc: (-rc[1], -rc[0]))
    shape = tuple(c for r, c in groups)
    group_ranks = tuple(r for r, c in groups)

    if is_straight and is_flush:
        return (8, straight_high)
    if shape == (4, 1):
        return (7,) + group_ranks
    if shape == (3, 2):
        return (6,) + group_ranks
    if is_flush:
        return (5,) + tuple(ranks)
    if is_straight:
        return (4, straight_high)
    if shape == (3, 1, 1):
        return (3,) + group_ranks
    if shape == (2, 2, 1):
        return (2,) + group_ranks
    if shape == (2, 1, 1, 1):
        return (1,) + group_ranks
    return (0,) + tuple(ranks)


def best_high(cards):
    """Best 5-card high hand from 5-7 cards. Larger key = better."""
    if len(cards) <= 5:
        return _high_key_5(cards)
    best = None
    for combo in combinations(cards, 5):
        k = _high_key_5(combo)
        if best is None or k > best:
            best = k
    return best


def best_omaha_high(hole4, board):
    """Omaha rule: exactly 2 from the 4 hole cards + exactly 3 from the board."""
    best = None
    for h2 in combinations(hole4, 2):
        for b3 in combinations(board, 3):
            k = _high_key_5(list(h2) + list(b3))
            if best is None or k > best:
                best = k
    return best


# ---------------------------------------------------------------------------
# Ace-to-five low evaluation (razz, and the low side of 8-or-better splits).
# Returns a tuple where a SMALLER tuple is a better (lower) hand.
# Any hand containing a pair or worse always loses to any hand with 5
# distinct ranks, regardless of values - the leading "severity" term
# guarantees that. Within a paired hand the tiebreak is a reasonable but not
# perfectly rule-exact ordering; that only matters in the very rare case of
# a forced pair (fewer than 5 distinct ranks among the 7 cards).
# ---------------------------------------------------------------------------

def _to_ace_low(rank):
    return 1 if rank == 14 else rank


def _low_key_5(ranks5):
    counts = Counter(ranks5)
    severity = sum(c * (c - 1) // 2 for c in counts.values())
    return (severity,) + tuple(sorted(ranks5, reverse=True))


def best_low(cards, qualify_8=False):
    """Best 5-card ace-to-five low from up to 7 cards (rank/suit tuples).
    If qualify_8, only cards ranked 8-or-under (ace counts low) are eligible,
    and returns None if fewer than 5 such cards exist (no qualifying low).
    Smaller key = better; compare with min()."""
    ranks = [_to_ace_low(r) for r, s in cards]
    if qualify_8:
        ranks = [r for r in ranks if r <= 8]
        if len(ranks) < 5:
            return None
    if len(ranks) <= 5:
        return _low_key_5(ranks) if len(ranks) == 5 else None
    best = None
    for combo in combinations(ranks, 5):
        k = _low_key_5(combo)
        if best is None or k < best:
            best = k
    return best


def best_omaha_low8(hole4, board):
    """Omaha hi/lo: exactly 2 hole + 3 board, ace-to-five, 8-or-better qualifier."""
    best = None
    for h2 in combinations(hole4, 2):
        for b3 in combinations(board, 3):
            cards5 = list(h2) + list(b3)
            ranks = [_to_ace_low(r) for r, s in cards5]
            if any(r > 8 for r in ranks):
                continue
            k = _low_key_5(ranks)
            if best is None or k < best:
                best = k
    return best


# ---------------------------------------------------------------------------
# Pot-split logic for a single showdown of known full hands.
# ---------------------------------------------------------------------------

def _split_by_best(keys, higher_better):
    """keys: {name: key or None}. Returns {name: share} summing to 1.0,
    split evenly among ties. None keys are excluded from winning."""
    valid = {n: k for n, k in keys.items() if k is not None}
    if not valid:
        return {n: 0.0 for n in keys}
    best = max(valid.values()) if higher_better else min(valid.values())
    winners = [n for n, k in valid.items() if k == best]
    share = 1.0 / len(winners)
    return {n: (share if n in winners else 0.0) for n in keys}


def score_showdown(game_type, hands, board):
    """hands: {name: [cards]} (each player's full final hand). board: [cards]
    (empty for stud/razz). Returns {name: share of the pot, sums to 1.0}."""
    if game_type == "Hold'em":
        keys = {n: best_high(c + board) for n, c in hands.items()}
        return _split_by_best(keys, higher_better=True)

    if game_type == "Omaha":
        keys = {n: best_omaha_high(c, board) for n, c in hands.items()}
        return _split_by_best(keys, higher_better=True)

    if game_type == "Omaha Hi/Lo":
        hi = {n: best_omaha_high(c, board) for n, c in hands.items()}
        lo = {n: best_omaha_low8(c, board) for n, c in hands.items()}
        return _split_hi_lo(hi, lo)

    if game_type == "Razz":
        keys = {n: best_low(c) for n, c in hands.items()}
        return _split_by_best(keys, higher_better=False)

    if game_type == "Stud":
        keys = {n: best_high(c) for n, c in hands.items()}
        return _split_by_best(keys, higher_better=True)

    if game_type == "Stud Hi/Lo":
        hi = {n: best_high(c) for n, c in hands.items()}
        lo = {n: best_low(c, qualify_8=True) for n, c in hands.items()}
        return _split_hi_lo(hi, lo)

    # Unsupported game type (draw games, badugi) - shouldn't be reached since
    # callers filter these out before invoking the simulator.
    share = 1.0 / len(hands)
    return {n: share for n in hands}


def _split_hi_lo(hi_keys, lo_keys):
    hi_share = _split_by_best(hi_keys, higher_better=True)
    if all(v is None for v in lo_keys.values()):
        # no qualifying low - hi side scoops the whole pot
        return hi_share
    lo_share = _split_by_best(lo_keys, higher_better=False)
    return {n: 0.5 * hi_share.get(n, 0.0) + 0.5 * lo_share.get(n, 0.0) for n in hi_keys}


# ---------------------------------------------------------------------------
# Monte Carlo runout simulation.
# ---------------------------------------------------------------------------

def simulate_equity(game_type, known_hands, known_board, board_to_deal, cards_per_player, trials=3000):
    """known_hands: {name: [cards]} - each contesting player's currently-known
    cards. known_board: [cards] already dealt (empty for stud/razz).
    board_to_deal: how many more community cards will be dealt (0 for
    stud/razz). cards_per_player: how many more private cards each player
    will receive (0 for flop games once all board cards are known).
    Returns {name: equity 0..1}, summing to 1.0."""
    used = set(known_board)
    for cards in known_hands.values():
        used.update(cards)
    deck = [c for c in full_deck() if c not in used]

    names = list(known_hands.keys())
    equity = {n: 0.0 for n in names}
    needed = board_to_deal + cards_per_player * len(names)
    if needed > len(deck):
        return None  # not enough unseen cards to simulate - shouldn't happen with a real deck

    rng = random.Random()
    for _ in range(trials):
        draw = rng.sample(deck, needed)
        pos = 0
        board = known_board + draw[pos:pos + board_to_deal]
        pos += board_to_deal
        hands = {}
        for n in names:
            extra = draw[pos:pos + cards_per_player]
            pos += cards_per_player
            hands[n] = known_hands[n] + extra
        shares = score_showdown(game_type, hands, board)
        for n, s in shares.items():
            equity[n] += s

    return {n: v / trials for n, v in equity.items()}
