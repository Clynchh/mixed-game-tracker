# Replayer table UI — reference for a draw-game pre-draw trainer

This describes the poker-table replay widget (`static/replayer.js` +
`replay.py`) in enough detail to reuse it as the display layer of a
**draw-game pre-draw trainer**: freeze the table at the moment hero has to
act before the first draw, ask the user what they'd do (bet/call/raise/fold
and how many to discard), then reveal what actually happened.

For the full copy-paste source (JS class + CSS block + colour variables)
see [REPLAYER_UI.md](REPLAYER_UI.md). This doc is the *trainer-facing*
view: what's on screen, what the data means, and where the draw-specific
bits live.

---

## 1. What the table looks like

The widget renders into one `<div>`. Everything below is built inside it.

```
┌─────────────────────────────  .replay-table  ──────────────────────────────┐
│                                                                            │
│                     ╭──────────  .felt (oval)  ──────────╮                  │
│         [seat: James UK7]                        [seat: Bob]                │
│              7♠ 3♣ 8♦                              ▨ ▨ ▨ ▨ ▨                │
│           ┌──────────────┐                     ┌──────────────┐            │
│           │ James UK7  D │  ← .seat-plate →    │ Bob          │            │
│           │ 22           │  (name/badge/stack) │ 21           │            │
│           └──────────────┘                     └──────────────┘            │
│                                                                            │
│                        .table-centre                                       │
│                     ┌──────────────────┐                                   │
│                     │  .board-cards    │  (empty in draw games)            │
│                     │  POT  6          │  ← .pot-display                    │
│                     └──────────────────┘                                   │
│                                                                            │
│                          ● 1.5   ← .seat-bet (chips in front this street)  │
│                        7♠ 3♣ 3♣ 9♣ 8♦   ← .seat-cards (hero, bigger)      │
│                     ┌────────────────────┐                                 │
│                     │ clynchh  [you]  D  │  ← hero .seat-plate            │
│                     │ 23                 │                                 │
│                     └────────────────────┘                                 │
└────────────────────────────────────────────────────────────────────────────┘
  |«   « Back    ▶ Play    Next »   »|   [chips|BB]   3 / 26 · Pre-draw · …   ← .replay-controls
  ────────────────●───────────────────────────────────────────────────────    ← .replay-scrub (range)
```

### Zones

| Element | Class | Contents |
|---|---|---|
| Felt | `.felt` | The green oval. Pure decoration. |
| Centre | `.table-centre` | Board cards + pot. In draw games `.board-cards` is always empty; only the pot shows. |
| Board | `.board-cards` | Community cards. **Draw games: always empty** — ignore for the trainer. |
| Pot | `.pot-display` | `POT <amount>`, formatted through `fmt()` (chips or BB). |
| Seat | `.seat[data-i=N]` | One per player, absolutely positioned at `left:x% top:y%`. `data-i` is the index into the `seats` array. |
| Controls | `.replay-controls` | Nav buttons, optional unit toggle, status line, showdown legend. |
| Scrubber | `.replay-scrub input` | `<input type=range>` from `0` to `frames.length - 1`. |

### Seat anatomy (top → bottom)

1. `.seat-bet` — chips wagered *this street*. Renders `● <amount>` when
   `bet > 0`, hidden otherwise. This is what hero is facing pre-draw.
2. `.seat-cards` — the player's cards, laid out left→right in deal order.
   - Hero's cards get class `hero` (larger, `.pc.hero`); everyone else
     gets `sm` (`.pc.sm`).
   - A visible card → `.pc` with rank + suit glyph. A face-down card →
     `.pc.pc-back` (red hatched rectangle).
   - **Draw games:** hero shows 5 real cards (4 for Badugi); every other
     unfolded player shows that many card-backs. `hidden_before` is always
     `0` for draw games, so backs render after any visible cards, not
     before. (The `up-card` nudge and stud's split deal order never apply.)
3. `.seat-plate` — panel with:
   - `.seat-name` — screen name (may contain spaces, e.g. `James UK7`).
   - `.you-badge` — the text `you`, only on `is_hero`.
   - `.btn-chip` — a `D` disc, only on `is_button`.
   - `.seat-stack` — remaining stack after this frame, in gold.

### Seat state classes (toggled per frame)

| Class | Meaning | Visual |
|---|---|---|
| `.folded` | `seat.folded` is true | whole seat dimmed to 35% opacity |
| `.acting` | `seat.acting` is true — this player is on the move in this frame | gold border + glow on the plate |
| `.winner` | `seat.won > 0` | green border + glow on the plate |

For the trainer, **`.acting` on hero's seat is the decision cue** — it's the
frame where hero must act.

### Card rendering (`cardHtml`)

- Card string = rank + lowercase suit letter: `"Ah"`, `"Td"` (ten is `T`,
  shown as `10`), `"2c"`.
- 4-colour deck: `s` black, `h` red, `d` blue, `c` green (`SUIT_INFO` /
  `.suit-*` classes).
- `used-hi` / `used-lo` ring classes exist for showdown highlighting but
  **draw games never set them** (see §3), so no rings ever appear.

---

## 2. The data contract

`build_replay(raw_text, game_type, hero_name, big_blind)` in
[replay.py](replay.py) returns:

```jsonc
{
  "game_type": "2-7 Triple Draw",
  "hero": "clynchh",
  "is_stud": false,          // always false for draw games
  "big_blind": 1.0,          // enables the chips/BB toggle
  "seats": [ /* fixed for the hand */ ],
  "frames": [ /* one snapshot per action */ ]
}
```

### `seats[]` — fixed for the whole hand

```jsonc
{
  "name": "clynchh",
  "seat_no": 1,          // original table seat number
  "start_stack": 25.0,
  "is_hero": true,       // hero is always seats[0]
  "is_button": true,
  "x": 50.0, "y": 82.0   // % position within the table oval
}
```

Seats are rotated so **hero is `seats[0]`, fixed at bottom-centre**; the
rest follow in original seat order, clockwise on screen (`_seat_positions`).

### `frames[]` — one complete table snapshot per step

```jsonc
{
  "street": "Pre-draw",              // display label, see §3
  "label": "Bob raises $1 to $2",    // human sentence for this action
  "board": [],                        // always [] for draw games
  "pot": 3.5,                         // sum of all commitments minus chips already paid out
  "highlight": null,                  // always null for draw games (see §3)
  "seats": [                          // SAME length & order as top-level seats[]
    {
      "stack": 23.0,                  // stack AFTER this action
      "bet": 1.5,                     // chips in front THIS street (0 if none)
      "cards": ["Qs","7s","3c","9c","8d"],  // cards currently visible to us
      "hidden": 0,                    // count of face-down cards to draw (5 for unseen opponents)
      "hidden_before": 0,             // always 0 for draw games
      "folded": false,
      "acting": true,                 // this seat is the actor in this frame
      "won": 0                        // >0 only on the frame they're paid
    }
    // ...one per seat
  ]
}
```

Notes that matter for the trainer:

- `seats[i].cards` for **hero** is the live hand at that frame. It changes
  the instant the draw is applied (see the redeal quirk in §3).
- `seats[i].cards` for **opponents** stays `[]` until/unless they show at
  showdown; `hidden` carries the count so the widget can draw backs.
- `pot` is computed as `sum(committed) - paid_out`. On the final
  "X wins N" frame this can briefly go **negative** — a known cosmetic
  quirk; don't rely on the last frame's pot.
- Amounts are in table currency. The widget divides by `big_blind` live
  when the BB toggle is active — you never precompute BB values.

---

## 3. How draw games flow through the replayer

### Street labels (`STREET_LABELS`)

| Hand-history header | `frame.street` |
|---|---|
| *(blinds, before any header)* | `Pre-flop` ← see quirk below |
| `*** DEALING HANDS ***` (Single Draw) | `Pre-draw` |
| `*** PRE-DRAW ***` (Triple Draw) | `Pre-draw` |
| `*** FIRST DRAW ***` | `1st Draw` |
| `*** SECOND DRAW ***` | `2nd Draw` |
| `*** THIRD DRAW ***` | `3rd Draw` |
| `*** DRAW ***` (Single Draw) | `Draw` |
| `*** SHOW DOWN ***` | `Showdown` |

`HOLE_CARD_COUNT`: `2-7 Triple Draw`, `2-7 Single Draw`, `5 Card Draw` → 5
cards; `Badugi` → 4. `DRAW_GAME_TYPES` gates the redeal-swap logic.

### Frame sequence for one pre-draw round (real dump, 3-handed Triple Draw)

```
 0  Pre-flop   clynchh posts small blind $0.50      pot 0.5
 1  Pre-flop   James UK7 posts big blind $1         pot 1.5
 2  Pre-draw   Pre-draw dealt                       pot 1.5   hero: Qs 7s 3c 9c 8d
 3  Pre-draw   Bob raises $1 to $2                  pot 3.5
 4  Pre-draw   clynchh calls $1.50                  pot 5.0   ← hero acts (seats[0].acting)
 5  Pre-draw   James UK7 calls $1                   pot 6.0
 6  1st Draw   1st Draw dealt                       pot 6.0
 7  1st Draw   clynchh discards 2                   pot 6.0   hero cards STILL Qs 7s 3c 9c 8d
 8  1st Draw   James UK7 stands pat                 pot 6.0   hero cards NOW 7s 3c 8d 2h 5h
 9  1st Draw   Bob discards 1                       pot 6.0
...
23  Showdown   James UK7 shows                      (highlight still null)
25  Showdown   James UK7 wins 18                    pot -2.0
```

### Action → `frame.label` strings

| Situation | `label` example | Frame emitted |
|---|---|---|
| Blind / ante | `clynchh posts small blind $0.50` | yes (antes collapse to one `Antes posted`) |
| Street dealt | `Pre-draw dealt`, `1st Draw dealt` | yes (lazily, after the deal lines are read) |
| Bet / call / raise / complete | `Bob raises $1 to $2`, `clynchh calls $1.50` | yes, `acting` = that player |
| Check | `James UK7 checks` | yes |
| Fold | `clynchh folds` | yes, seat marked `folded` |
| **Discard** | `clynchh discards 2` | yes, `acting` = that player |
| **Stand pat** | `James UK7 stands pat` | yes |
| Show | `James UK7 shows` | yes |
| Muck | `Bob mucks` | yes |
| Uncalled bet returned | `Uncalled 1 returned to James UK7` | yes |
| Win | `James UK7 wins 18` | yes, seat marked `winner` |

The `discards N` label does **not** list which cards (for hero the discard
cards *are* in the raw history — `clynchh: discards 2 cards [Qs 9c]` — but
`replay.py` only keeps the count in the label). If the trainer needs the
exact discarded cards, diff `frame.cards` before vs. after the redeal, or
re-parse the `discards ... [..]` bracket from `raw_text` yourself.

### Quirks to design around

- **Blind frames say `Pre-flop`, not `Pre-draw`.** `street_label` starts at
  `"Pre-flop"` and the blinds are posted before the `DEALING HANDS` /
  `PRE-DRAW` header. The first frame that actually reads `Pre-draw` is the
  `"Pre-draw dealt"` frame, once hero's cards are visible. Filter on that,
  not on the blind-post frames.
- **The redeal has no frame of its own.** `Dealt to clynchh [7s 3c 8d] [2h 5h]`
  replaces `known[hero]` but emits nothing. Hero's new hand first appears
  on the *next* action frame after their `discards N` frame. So on the
  `discards 2` frame the widget still shows the pre-draw hand.
- **`discards` / `stands pat` reset `bet` to 0** for everyone — they double
  as the street-bet clear when a game prints no header between betting
  rounds (2-7 Single Draw).
- **`highlight` is always `null` for draw games.** `equity.best_hands`
  returns `None` for every `DRAW_GAME_TYPES` entry, so no `used-hi` /
  `used-lo` rings, no showdown legend. If the trainer wants to grade the
  resulting hand you must evaluate 2-7 / Badugi lows yourself — the repo
  has no draw evaluator.
- **Opponent hands stay hidden** unless they `show`. Pre-draw you only ever
  know hero's 5 cards; everything else is `hidden: 5` backs.
- **Final-frame pot can be negative** (see §2).

---

## 4. Controls & keyboard

| Button (`data-act`) | Key | Effect |
|---|---|---|
| `|«` first | `Home` | jump to frame 0 |
| `« Back` prev | `←` | frame − 1 (clamped) |
| `▶ Play` / `❚❚ Pause` | `Space` | auto-advance every 900 ms |
| `Next »` next | `→` | frame + 1 (clamped) |
| `»|` last | `End` | jump to last frame |
| chips / BB toggle | — | only if `big_blind` set; re-formats every amount live |
| scrubber | — | drag to any frame; pauses playback |

Keyboard handlers are ignored while focus is in an `input` / `textarea` /
`select`, so the trainer's own answer form won't fight the arrow keys.

Status line text: `` `${i+1} / ${N} · ${street} · ${label}` `` — for the
trainer you'll likely blank or override this so it doesn't spoil the
answer.

---

## 5. Building the pre-draw trainer on top

### The decision point

A pre-draw decision frame is one where **all** of:

- `frame.street === "Pre-draw"`,
- `frame.seats[0].acting === true` (hero is the actor), and
- the label is a voluntary action (`calls` / `raises` / `folds` /
  `checks` / `completes` / `bets`) — i.e. not `"Pre-draw dealt"`.

There can be two hero decisions to quiz per hand:

1. **Pre-draw betting** — the frame above. Actual answer = that frame's
   `label` verb + amount.
2. **First-draw discard** — the frame with `street === "1st Draw"` (or
   `"Draw"`), `seats[0].acting`, label `clynchh discards N` or
   `clynchh stands pat`. Actual answer = `N` (0 = pat).

### What to show the user

Freeze the widget on the frame **immediately before** hero's action —
i.e. the last frame where hero is not yet `acting`, or the decision frame
itself with the label hidden. Everything the user needs is already in that
snapshot:

| Trainer input | Where from |
|---|---|
| Hero's hand | `frame.seats[0].cards` |
| Position | `seats[0].is_button`, plus hero's index vs. blinds by seat order |
| Players still in | `frame.seats.filter(s => !s.folded).length` |
| Pot | `frame.pot` |
| To call | `Math.max(...frame.seats.map(s => s.bet)) - frame.seats[0].bet` |
| Action so far | earlier `frames[]` labels on the same `street` |
| Stack / SPR | `frame.seats[0].stack`, `stack / pot` |

### Two ways to reuse the widget

- **Truncate the frames array.** Pass `HandReplayer` a copy of `data` with
  `frames` sliced to end at the decision frame. The user can scrub the
  betting that led here but can't peek ahead. After they answer, swap in
  the full `frames` and jump to the reveal frame.
- **Render one frozen frame.** If you don't need scrub-back, construct
  `data` with a single frame (the decision snapshot) and hide the
  controls/scrubber with CSS. Simplest for a rapid-fire drill.

Either way the widget is pure `render(frames[i])` — no internal game state
— so freezing, rewinding, and swapping datasets can't desync anything.

### Grading

- **Betting decision:** compare the user's choice to the decision frame's
  `label`. Exact-match verb (fold/call/raise) is the cheap version; for a
  "was this defensible" grade you need a pre-draw chart keyed on
  hand-class × position × action-facing.
- **Discard decision:** compare `N` to the `discards N` / `stands pat`
  label. To also grade the *specific* cards kept, re-parse
  `Dealt to <hero> [kept] [new]` from `raw_text` (the replayer drops the
  bracket detail).
- The repo has **no 2-7 / Badugi hand evaluator**. If the trainer scores
  hand strength you'll add one (2-7: A is high, straights/flushes count
  against you, best is `7-5-4-3-2`; Badugi: best is 4 cards, all
  different suits and ranks, lowest).

### Data source

`replay.build_replay(hand["raw_text"], hand["game_type"], hand["hero_name"], big_blind)`
is already called per hand in [app.py](app.py) at the `/hand/<hand_id>`
route. Filter `hand["game_type"] in {"2-7 Triple Draw", "2-7 Single Draw",
"Badugi", "5 Card Draw"}` and scan `frames` for the decision points above
to build a drill queue.

---

## 6. File map

| File | Role |
|---|---|
| [replay.py](replay.py) | `build_replay()` → `{seats, frames}`; all the draw parsing (`DISCARD_RE`, `STANDPAT_RE`, `DRAW_GAME_TYPES`, `STREET_LABELS`). |
| [static/replayer.js](static/replayer.js) | `HandReplayer` class — DOM build, `render(frame)`, controls, keyboard. |
| [templates/hand.html](templates/hand.html) | Mounts `new HandReplayer(el, {{ replay_data|tojson }})`. |
| [REPLAYER_UI.md](REPLAYER_UI.md) | Standalone copy-paste of the widget (JS + full CSS + colour vars). |
| [equity.py](equity.py) | `best_hands()` / evaluators — **no draw support**; returns `None`. |
| [tests/test_replay.py](tests/test_replay.py) | Draw-specific cases: redeal swap, single-draw hidden cards, names with spaces. |
