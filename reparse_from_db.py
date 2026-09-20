"""One-off: re-run the current parser over the raw hand text already stored in
tracker.db and write the fresh figures back.

Unlike the in-app "Re-read all hand histories" button, this needs neither the
original hand-history files on disk nor the web server - it works straight off
the raw_text column. Tags/notes live in their own table and are untouched.

    ./venv/bin/python reparse_from_db.py            # apply
    ./venv/bin/python reparse_from_db.py --dry-run  # just show what would change
"""
import sys

import db
import parser as hh_parser
import version

DRY = "--dry-run" in sys.argv


def main():
    hero_default = db.get_setting("hero_username") or None

    with db.get_conn() as conn:
        rows = conn.execute(
            "SELECT hand_id, raw_text, source_file, hero_name FROM hands"
        ).fetchall()

    changed = 0
    failed = 0
    field_deltas = {}
    for row in rows:
        raw = row["raw_text"]
        if not raw:
            continue
        try:
            parsed = hh_parser.parse_hand(
                raw,
                source_file=row["source_file"] or "",
                hero_username=hero_default or row["hero_name"],
            )
        except Exception as e:  # noqa: BLE001 - report and keep going
            failed += 1
            print(f"  parse failed for {row['hand_id']}: {e}")
            continue
        if not parsed:
            failed += 1
            continue

        before = _row_view(row["hand_id"])
        after = {k: parsed.get(k) for k in _TRACKED}
        diff = {k: (before[k], after[k]) for k in _TRACKED if _norm(before[k]) != _norm(after[k])}
        if not diff:
            continue

        changed += 1
        for k in diff:
            field_deltas[k] = field_deltas.get(k, 0) + 1
        if DRY:
            print(f"  {row['hand_id']} ({parsed.get('game_type')}): "
                  + ", ".join(f"{k} {v[0]!r}->{v[1]!r}" for k, v in diff.items()))
        else:
            db.upsert_hand(parsed)

    print()
    print(f"{'would change' if DRY else 'changed'}: {changed} / {len(rows)} hands"
          + (f"   ({failed} failed to parse)" if failed else ""))
    for k, n in sorted(field_deltas.items(), key=lambda kv: -kv[1]):
        print(f"    {k}: {n}")

    if not DRY:
        db.mark_parser_version()
        print(f"\nparser_version set to {version.PARSER_VERSION}")


_TRACKED = ("game_type", "pot_type", "went_to_showdown", "vpip", "num_players",
            "hero_net", "pot_total", "big_blind")


def _norm(v):
    if isinstance(v, float):
        return round(v, 2)
    return v


def _row_view(hand_id):
    with db.get_conn() as conn:
        r = conn.execute("SELECT * FROM hands WHERE hand_id=?", (hand_id,)).fetchone()
    return {k: r[k] for k in _TRACKED}


if __name__ == "__main__":
    main()
