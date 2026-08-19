"""
Parses PokerStars Tournament Summary (TS*.txt) files - a second, optional
data source alongside hand histories.

Why this exists: hand histories alone can't reliably tell a re-entry from
a single entry (each entry just looks like a normal buy-in and a normal
bust-out in the text), and some finish lines omit the exact place entirely
(seen when several players bust in the same all-in). Tournament summaries
are PokerStars' own authoritative record - a single ranked list of every
entry - so when one's available for a tournament, its buy-in/finish
place/payout replace whatever hand-history parsing guessed, rather than
supplementing it.

Bounty cash is the one thing summaries don't carry - progressive knockout
payouts are a separate side-system PokerStars doesn't fold into the
"Total Prize Pool" a summary reports. That's why bounty stays sourced from
hand histories (parser.py's BOUNTY_WIN_RE) even when a summary is used for
everything else about a tournament.
"""
import glob
import os
import re

import db
import parser as hh_parser

HEADER_RE = re.compile(r"^PokerStars Tournament #(\d+), (.+)$", re.MULTILINE)
BUYIN_RE = re.compile(r"^Buy-In: \$([\d,]+(?:\.\d+)?)/\$([\d,]+(?:\.\d+)?)", re.MULTILINE)

# "  18: clynchh [2] (United Kingdom), $5.40 (1.278%)" - the [N] entry-count
# marker and the payout are both optional; a player who didn't cash just
# trails off with a comma, and one who hasn't busted yet (a satellite that
# ended by seat count rather than elimination) says "still playing". Not
# anchored to end-of-line: a payout is followed by "(N.NNN%)", which isn't
# needed for anything here, just skipped over rather than also matched.
RANK_LINE_TEMPLATE = r"^\s*(\d+): {name}(?: \[(\d+)\])? \([^)]*\),\s*(?:\$([\d,]+(?:\.\d+)?)|still playing)?"

TARGET_BUYIN_RE = re.compile(r"^Target Tournament #\d+ Buy-In: \$([\d,]+(?:\.\d+)?)", re.MULTILINE)
STARTED_RE = re.compile(r"^Tournament started (\d{4}/\d{2}/\d{2}[ \d:]+)", re.MULTILINE)


def _money(s):
    return float(s.replace(",", ""))


def parse_summary_file(filepath, hero_username):
    """Returns a dict describing the hero's result in this tournament, or
    None if they're not in it (or the file doesn't parse as a summary at
    all). Handles re-entries: every one of the hero's own lines in the
    ranked list is a separate entry, each with its own bust-out position
    and (possibly zero) payout."""
    if not hero_username:
        return None
    with open(filepath, "r", encoding="utf-8-sig", errors="replace") as f:
        text = f.read()

    header = HEADER_RE.search(text)
    if not header:
        return None
    tournament_id = header.group(1)

    buyin_m = BUYIN_RE.search(text)
    buy_in_per_entry = (_money(buyin_m.group(1)) + _money(buyin_m.group(2))) if buyin_m else None

    rank_re = re.compile(RANK_LINE_TEMPLATE.format(name=re.escape(hero_username)), re.MULTILINE)
    entries = list(rank_re.finditer(text))
    if not entries:
        return None

    target_m = TARGET_BUYIN_RE.search(text)
    ticket_value = _money(target_m.group(1)) if target_m else None

    positions = []
    total_payout = 0.0
    entry_count = None
    for m in entries:
        positions.append(int(m.group(1)))
        if m.group(2):
            entry_count = int(m.group(2))
        if m.group(3):
            total_payout += _money(m.group(3))
        elif "still playing" in m.group(0):
            # Only reachable for the hero's own line if they won a seat in a
            # satellite that ended by field size rather than elimination -
            # no cash changes hands, just a ticket into the target event.
            total_payout += ticket_value or 0.0

    if entry_count is None:
        entry_count = len(entries)

    started_m = STARTED_RE.search(text)
    date_played = hh_parser._parse_date(started_m.group(1)) if started_m else None

    return {
        "tournament_id": tournament_id,
        "game_desc": header.group(2),
        "date_played": date_played,
        "buy_in": (buy_in_per_entry * entry_count) if buy_in_per_entry is not None else None,
        "entries": entry_count,
        "finish_place": min(positions),
        "payout": round(total_payout, 4),
    }


def scan_folder(folder, hero_username=None):
    """Parses every summary file in folder and applies each one to the
    tournaments table. Returns count of tournaments updated."""
    if not folder or not os.path.isdir(folder) or not hero_username:
        return 0

    updated = 0
    for filepath in glob.glob(os.path.join(folder, "**", "*.txt"), recursive=True):
        try:
            result = parse_summary_file(filepath, hero_username)
        except Exception as e:
            print(f"[tourn_summary] failed to parse {filepath}: {e}")
            continue
        if result:
            db.set_tournament_summary(**result)
            updated += 1
    return updated
