# Replicating the hand replayer UI

This is the poker-table replay widget from the hand detail page: a felt
table with seats laid out around it, playing cards, a scrubber with
play/pause, a chips/BB toggle, and gold/blue rings around the cards that
made up the winning hand at showdown.

It's built from three pieces, all copy-pasteable into another project:

1. **A data contract** — your backend turns whatever you're replaying
   (a poker hand, in this case) into a plain JSON object: a list of seats
   and a list of "frames," one full snapshot of the table per step.
2. **`replayer.js`** — a single dependency-free class that owns a `<div>`
   and renders whichever frame you're on into it.
3. **CSS** — a self-contained block, themed through a handful of colour
   variables.

The core idea worth keeping if you reuse this elsewhere: **the JS never
replays any logic.** Your backend computes every intermediate state up
front as a plain array. The widget just renders `frames[i]`. Stepping
backward, jumping to frame 0, or dragging the scrubber can never drift out
of sync with reality, because there's no running state to drift — every
frame is a complete, independent snapshot.

## 1. HTML mount point

```html
<div id="replayer"></div>
<script src="replayer.js"></script>
<script>
  new HandReplayer(document.getElementById('replayer'), data);
</script>
```

`data` is the JSON object described below. The widget builds its entire
DOM into that one div — no other markup needed.

## 2. The data contract

```jsonc
{
  "is_stud": false,        // optional - see "stud up-cards" note below
  "big_blind": 0.5,         // optional - omit to hide the chips/BB toggle

  "seats": [                // fixed for the whole replay - one entry per seat
    {
      "name": "Hero",
      "is_hero": true,       // hero's cards render larger
      "is_button": true,     // shows a "D" dealer-button chip
      "x": 50, "y": 82       // position as a % within the table ellipse
    }
    // ...one per player
  ],

  "frames": [                // one entry per step of the replay
    {
      "street": "flop",              // free-text, shown in the status line
      "label": "Alice raises to $6", // free-text, shown in the status line
      "board": ["Ah", "Kd", "2c"],   // community cards, if any
      "pot": 14.5,
      "highlight": {                  // OPTIONAL - only on showdown frames
        "seat": 2,                    // index into the top-level `seats` array
        "hi": ["Ah", "Kd", "2c", "Ts", "9h"], // cards making the best HIGH hand
        "lo": ["7c", "5d", "4h", "3s", "2c"]  // cards making the best LOW hand
      },
      "seats": [                      // same length/order as top-level `seats`
        {
          "stack": 94.5,              // remaining stack after this frame
          "bet": 6,                   // chips in front of them this street (0 if none)
          "cards": ["Ah", "Kd"],      // cards actually known/visible right now
          "hidden": 2,                // TOTAL cards this seat holds (visible + face-down)
          "hidden_before": 0,         // how many face-down cards come BEFORE the visible ones
          "folded": false,
          "acting": true,             // true for whoever is on the move - gold ring
          "won": 0                    // > 0 if they won chips this frame - green ring
        }
        // ...one per seat, same order as top-level `seats`
      ]
    }
    // ...one frame per action in the hand, in order
  ]
}
```

Notes:

- **Cards are strings**: rank + lowercase suit letter, e.g. `"Ah"`, `"Td"`,
  `"2c"`. Ten is `"T"`. The renderer maps suit letters to symbols and
  colours (`s`/`c` dark, `h` red, `d` blue) — see `SUIT_INFO` in the JS.
- **`hidden` / `hidden_before`** exist so a seat's card row lays out in the
  order cards were actually dealt, not always "known cards first." A stud
  player's 1st and 2nd cards are face-down, 3rd–6th face-up, 7th face-down
  again — `hidden_before` tells the renderer how many blank card-backs to
  draw before the visible ones so the row matches the real deal order.
  For a simple game where hidden cards are always opponents' unseen hole
  cards, `hidden_before` is just `0`.
- **`highlight`** only needs to exist on the frame(s) where you want a
  ring drawn — typically showdown. A card can be in both `hi` and `lo`
  (a split-pot game) and gets both rings at once.
- Every amount (`stack`, `bet`, `pot`) is a plain number in your base
  unit. If `big_blind` is set, the widget offers a second unit toggle that
  divides every one of these by it live — you don't need to precompute a
  second set of numbers.

## 3. Seat layout math

Seats sit around an ellipse, seat 0 (hero, if you have one) fixed at
bottom-centre, the rest continuing clockwise — which reads naturally
because it's the direction action actually moves around a real table.
Compute `x`/`y` once per seat count with this and store the result on
each seat object:

```python
import math

def seat_positions(n):
    """Returns [(x_pct, y_pct), ...], index 0 at bottom-centre, clockwise."""
    out = []
    for i in range(n):
        angle = math.radians(90 + i * (360.0 / n))
        out.append((50 + 37 * math.cos(angle), 50 + 32 * math.sin(angle)))
    return out
```

This is independent of the domain — works for any fixed ring of seats
around an oval, poker or otherwise.

## 4. `replayer.js`

Drop this in as-is. Nothing in it is poker-specific except the card
rank/suit formatting (`cardHtml`) — swap that function out and everything
else (frame stepping, play/pause, scrubber, keyboard shortcuts, the
seat-ring highlight logic) carries over unchanged to any other "step
through precomputed snapshots" UI.

```javascript
const SUIT_INFO = {
  s: { sym: "♠", cls: "suit-s" },
  h: { sym: "♥", cls: "suit-h" },
  d: { sym: "♦", cls: "suit-d" },
  c: { sym: "♣", cls: "suit-c" },
};

function cardHtml(card, extraClass) {
  const rank = card.slice(0, -1);
  const suit = card.slice(-1).toLowerCase();
  const info = SUIT_INFO[suit] || { sym: "?", cls: "" };
  const shown = rank === "T" ? "10" : rank;
  return `<span class="pc ${info.cls} ${extraClass || ""}"><b>${shown}</b><i>${info.sym}</i></span>`;
}

function backHtml(n, extraClass) {
  let out = "";
  for (let i = 0; i < n; i++) out += `<span class="pc pc-back ${extraClass || ""}"></span>`;
  return out;
}

/** Ring classes for a card that's part of the currently-highlighted best
 *  hand. A card can serve both ways in a split game, so both can apply. */
function usedClasses(card, hl) {
  if (!hl) return "";
  const out = [];
  if (hl.hi && hl.hi.indexOf(card) !== -1) out.push("used-hi");
  if (hl.lo && hl.lo.indexOf(card) !== -1) out.push("used-lo");
  return out.join(" ");
}

function fmtChips(v) {
  if (v === 0) return "0";
  if (Math.abs(v) >= 1000000) return (v / 1000000).toFixed(2) + "M";
  return Math.round(v).toLocaleString();
}

class HandReplayer {
  constructor(root, data) {
    this.root = root;
    this.data = data;
    this.i = 0;
    this.playing = false;
    this.timer = null;
    this.unit = "chips";
    this._build();
    this.render();
  }

  /** Every amount on the table goes through here, so the BB toggle flips
   *  stacks, bets and the pot together rather than just one of them. */
  fmt(v) {
    if (this.unit === "bb" && this.data.big_blind) {
      const bb = v / this.data.big_blind;
      if (bb === 0) return "0 bb";
      return (Math.abs(bb) >= 100 ? bb.toFixed(0) : bb.toFixed(1)) + " bb";
    }
    return fmtChips(v);
  }

  _build() {
    const seats = this.data.seats;
    this.root.innerHTML = `
      <div class="replay-table">
        <div class="felt"></div>
        <div class="table-centre">
          <div class="board-cards"></div>
          <div class="pot-display"></div>
        </div>
        ${seats.map((s, i) => `
          <div class="seat" data-i="${i}" style="left:${s.x}%;top:${s.y}%">
            <div class="seat-bet"></div>
            <div class="seat-cards"></div>
            <div class="seat-plate">
              <div class="seat-name">${s.name}${s.is_hero ? ' <span class="you-badge">you</span>' : ""}${s.is_button ? ' <span class="btn-chip">D</span>' : ""}</div>
              <div class="seat-stack"></div>
            </div>
          </div>`).join("")}
      </div>
      <div class="replay-controls">
        <button type="button" class="btn-ghost" data-act="first" title="First (Home)">&#124;&laquo;</button>
        <button type="button" class="btn-ghost" data-act="prev" title="Back (←)">&laquo; Back</button>
        <button type="button" data-act="play" title="Play / pause (space)">&#9654; Play</button>
        <button type="button" class="btn-ghost" data-act="next" title="Forward (→)">Next &raquo;</button>
        <button type="button" class="btn-ghost" data-act="last" title="Last (End)">&raquo;&#124;</button>
        ${this.data.big_blind ? `
        <div class="unit-toggle replay-unit">
          <a href="#" class="unit-opt active" data-unit="chips">chips</a>
          <a href="#" class="unit-opt" data-unit="bb">BB</a>
        </div>` : ""}
        <span class="replay-status"></span>
        <span class="replay-legend" style="display:none"></span>
      </div>
      <div class="replay-scrub"><input type="range" min="0" max="${this.data.frames.length - 1}" value="0"></div>
    `;

    this.boardEl = this.root.querySelector(".board-cards");
    this.potEl = this.root.querySelector(".pot-display");
    this.seatEls = Array.from(this.root.querySelectorAll(".seat"));
    this.statusEl = this.root.querySelector(".replay-status");
    this.legendEl = this.root.querySelector(".replay-legend");
    this.scrub = this.root.querySelector(".replay-scrub input");
    this.playBtn = this.root.querySelector('[data-act="play"]');

    this.root.querySelectorAll("[data-act]").forEach((b) => {
      b.addEventListener("click", () => this.act(b.dataset.act));
    });
    this.root.querySelectorAll("[data-unit]").forEach((a) => {
      a.addEventListener("click", (e) => {
        e.preventDefault();
        this.unit = a.dataset.unit;
        this.root.querySelectorAll("[data-unit]").forEach((o) => o.classList.toggle("active", o === a));
        this.render();
      });
    });
    this.scrub.addEventListener("input", () => {
      this.pause();
      this.i = parseInt(this.scrub.value, 10);
      this.render();
    });

    // Only steal the arrow keys when the replayer is actually on screen and
    // the user isn't typing into some other input on the page.
    document.addEventListener("keydown", (e) => {
      const tag = (e.target.tagName || "").toLowerCase();
      if (tag === "input" || tag === "textarea" || tag === "select") return;
      const map = { ArrowLeft: "prev", ArrowRight: "next", Home: "first", End: "last" };
      if (e.key === " ") { e.preventDefault(); this.act("play"); return; }
      if (map[e.key]) { e.preventDefault(); this.act(map[e.key]); }
    });
  }

  act(what) {
    if (what === "play") { this.playing ? this.pause() : this.play(); return; }
    this.pause();
    if (what === "prev") this.i = Math.max(0, this.i - 1);
    if (what === "next") this.i = Math.min(this.data.frames.length - 1, this.i + 1);
    if (what === "first") this.i = 0;
    if (what === "last") this.i = this.data.frames.length - 1;
    this.render();
  }

  play() {
    if (this.i >= this.data.frames.length - 1) this.i = 0;
    this.playing = true;
    this.playBtn.innerHTML = "&#10073;&#10073; Pause";
    this.timer = setInterval(() => {
      if (this.i >= this.data.frames.length - 1) { this.pause(); return; }
      this.i++;
      this.render();
    }, 900);
  }

  pause() {
    this.playing = false;
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
    this.playBtn.innerHTML = "&#9654; Play";
  }

  render() {
    const f = this.data.frames[this.i];
    const hl = f.highlight || null;

    // Board cards belong to whichever player's hand is currently ringed, so
    // they follow the same highlight.
    this.boardEl.innerHTML = f.board.map((c) => cardHtml(c, usedClasses(c, hl))).join("");
    this.legendEl.style.display = hl && (hl.hi || hl.lo) ? "" : "none";
    if (hl) {
      this.legendEl.innerHTML =
        (hl.hi ? '<span><i class="ring-hi"></i>best high hand</span>' : "") +
        (hl.lo ? '<span><i class="ring-lo"></i>best low hand</span>' : "");
    }
    this.potEl.innerHTML = `<span class="pot-label">Pot</span> <span class="pot-amount">${this.fmt(f.pot)}</span>`;
    this.statusEl.textContent = `${this.i + 1} / ${this.data.frames.length} · ${f.street} · ${f.label}`;
    this.scrub.value = this.i;

    f.seats.forEach((s, idx) => {
      const el = this.seatEls[idx];
      const isHero = this.data.seats[idx].is_hero;
      const size = isHero ? "hero" : "sm";  // your own cards read bigger
      el.classList.toggle("folded", s.folded);
      el.classList.toggle("acting", s.acting);
      el.classList.toggle("winner", s.won > 0);
      el.querySelector(".seat-stack").textContent = this.fmt(s.stack);
      const bet = el.querySelector(".seat-bet");
      bet.innerHTML = s.bet > 0 ? `<span class="chip-dot"></span>${this.fmt(s.bet)}` : "";
      bet.style.visibility = s.bet > 0 ? "visible" : "hidden";
      // Lay the seat out in deal order so positions line up: any leading
      // face-down cards, then what we can see, then any trailing ones.
      const before = s.hidden_before || 0;
      const items = [];
      for (let k = 0; k < before; k++) items.push(null);
      s.cards.forEach((c) => items.push(c));
      for (let k = 0; k < s.hidden - before; k++) items.push(null);

      const seatHl = hl && hl.seat === idx ? hl : null;
      el.querySelector(".seat-cards").innerHTML = items.map((c, pos) => {
        // Stud deals cards 3-6 face up; 1, 2 and 7 stay down. Nudging the
        // up-cards proud of the row makes that split readable at a glance.
        const cls = [size];
        if (this.data.is_stud && pos >= 2 && pos <= 5) cls.push("up-card");
        if (c) cls.push(usedClasses(c, seatHl));
        return c ? cardHtml(c, cls.join(" ")) : backHtml(1, cls.join(" "));
      }).join("");
    });
  }
}
```

## 5. CSS

These are the colour variables the block below is written against. Reuse
your own palette instead if the host project already has one — nothing
else needs to change, since every selector below reads through these
custom properties rather than hard-coded colours.

```css
:root {
  --bg:        #101216;
  --panel:     #171a20;
  --panel-2:   #1d212a;
  --border:    #272c36;
  --text:      #e8eaee;
  --text-dim:  #888f9c;
  --gold:      #c2a15e;
  --gold-dim:  #8a7442;
  --win:       #5fae6e;
  --radius:    6px;
  --sans: "Segoe UI Variable Text", "Segoe UI", ui-sans-serif, -apple-system,
          BlinkMacSystemFont, Inter, Roboto, "Helvetica Neue", Arial, sans-serif;
  --num: var(--sans);
}
```

The widget block itself (`.btn-ghost` and `button`/`.btn` are generic
button styles the rest of a host app would likely already have — include
them too if not):

```css
button, .btn {
  background: var(--gold-dim);
  color: #0f1115;
  border: 1px solid transparent;
  padding: 7px 14px;
  border-radius: var(--radius);
  font-family: var(--sans);
  font-weight: 600;
  cursor: pointer;
  font-size: 13px;
  transition: background 0.12s, border-color 0.12s, color 0.12s;
}
button:hover, .btn:hover { background: var(--gold); text-decoration: none; }
.btn-ghost {
  background: transparent;
  border: 1px solid var(--border);
  color: var(--text-dim);
  font-weight: 500;
}
.btn-ghost:hover { border-color: var(--border); color: var(--text); background: var(--panel-2); }

.unit-toggle {
  display: flex;
  gap: 2px;
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 3px;
  margin-bottom: 6px;
}
.unit-opt {
  padding: 5px 12px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-dim);
  border-radius: 4px;
}
.unit-opt:hover { color: var(--text); text-decoration: none; }
.unit-opt.active { background: var(--gold-dim); color: #0f1115; }
.unit-opt.active:hover { color: #0f1115; }

/* ---------------------------------------------------------------- replayer */
.replay-wrap {
  background: var(--panel);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 16px 18px;
  margin-bottom: 18px;
}
.replay-table {
  position: relative;
  width: 100%;
  max-width: 860px;
  margin: 0 auto 14px;
  aspect-ratio: 16 / 9;
  min-height: 360px;
}
.felt {
  position: absolute;
  left: 12%;
  top: 16%;
  width: 76%;
  height: 68%;
  border-radius: 50%;
  background: radial-gradient(ellipse at 50% 40%, #1f5c46 0%, #16412f 70%, #102e22 100%);
  border: 7px solid #3b2a1c;
  box-shadow: inset 0 0 40px rgba(0, 0, 0, 0.45), 0 6px 22px rgba(0, 0, 0, 0.5);
}
.table-centre {
  position: absolute;
  left: 50%;
  top: 46%;
  transform: translate(-50%, -50%);
  text-align: center;
  width: 60%;
}
.board-cards { display: flex; gap: 4px; justify-content: center; min-height: 50px; }
.pot-display {
  margin-top: 8px;
  font-family: var(--num);
  font-variant-numeric: tabular-nums;
  font-size: 13px;
  color: var(--text);
}
.pot-label { color: rgba(231, 228, 220, 0.6); text-transform: uppercase; font-size: 10px; letter-spacing: 0.08em; }
.pot-amount { font-weight: 700; }

.seat {
  position: absolute;
  transform: translate(-50%, -50%);
  width: 128px;
  text-align: center;
  transition: opacity 0.15s;
}
.seat.folded { opacity: 0.35; }
.seat-cards {
  display: flex;
  gap: 3px;
  justify-content: center;
  align-items: flex-end;
  min-height: 34px;
  padding-top: 9px;  /* room for raised stud up-cards + highlight rings */
  margin-bottom: 3px;
}
.seat-plate {
  background: var(--panel-2);
  border: 1px solid var(--border);
  border-radius: 5px;
  padding: 4px 6px;
}
.seat.acting .seat-plate { border-color: var(--gold); box-shadow: 0 0 0 2px rgba(201, 162, 74, 0.25); }
.seat.winner .seat-plate { border-color: var(--win); box-shadow: 0 0 0 2px rgba(95, 174, 110, 0.25); }
.seat-name {
  font-size: 11px;
  font-weight: 600;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.seat-stack { font-family: var(--num); font-variant-numeric: tabular-nums; font-size: 12px; color: var(--gold); }
.btn-chip {
  display: inline-block;
  width: 14px;
  height: 14px;
  line-height: 13px;
  border-radius: 50%;
  background: var(--text);
  color: var(--bg);
  font-size: 9px;
  font-weight: 700;
  vertical-align: middle;
}
.you-badge {
  display: inline-block;
  padding: 0 5px;
  border-radius: 3px;
  background: var(--gold-dim);
  color: #0f1115;
  font-size: 9px;
  font-weight: 700;
  letter-spacing: 0.05em;
  text-transform: uppercase;
  vertical-align: middle;
}
.seat-bet {
  font-family: var(--num);
  font-variant-numeric: tabular-nums;
  font-size: 11px;
  color: var(--text);
  margin-bottom: 2px;
  white-space: nowrap;
}
.chip-dot {
  display: inline-block;
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--gold);
  border: 1.5px solid #8a6f2c;
  margin-right: 4px;
  vertical-align: -1px;
}

/* playing cards - 4-colour deck, easier to read in Omaha/stud */
.pc {
  display: inline-flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  /* Never squeeze to fit the seat - a stud player can hold seven cards,
     far wider than the seat plate. They keep full size and the row just
     overflows, centred, rather than getting progressively smaller. */
  flex-shrink: 0;
  width: 34px;
  height: 48px;
  background: #f4f1e8;
  border-radius: 3px;
  font-family: var(--sans);
  line-height: 1;
  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.4);
}
.pc b { font-size: 17px; font-weight: 700; }
.pc i { font-size: 15px; font-style: normal; margin-top: 1px; }
.pc.sm { width: 24px; height: 34px; }
.pc.sm b { font-size: 12px; }
.pc.sm i { font-size: 10px; }
/* your own cards sit bigger than everyone else's - they're the ones you're
   actually reading while stepping through the replay */
.pc.hero { width: 40px; height: 56px; }
.pc.hero b { font-size: 20px; }
.pc.hero i { font-size: 17px; }
.pc-back {
  width: 24px;
  height: 34px;
  background: repeating-linear-gradient(45deg, #7d2b2b, #7d2b2b 3px, #5e1f1f 3px, #5e1f1f 6px);
  border: 1px solid #4a1919;
}
.pc-back.hero { width: 40px; height: 56px; }

/* Stud's face-up cards (3rd-6th street) sit proud of the face-down ones,
   so which cards the table could actually see is obvious at a glance. */
.pc.up-card { transform: translateY(-7px); }

/* Cards making up a player's best hand at showdown. Split-pot games can ring
   a card both ways at once, so the low ring sits outside the high one. */
.pc.used-hi { box-shadow: 0 0 0 2px var(--gold), 0 0 9px rgba(201, 162, 74, 0.65); }
.pc.used-lo { box-shadow: 0 0 0 2px #4a90d9, 0 0 9px rgba(74, 144, 217, 0.65); }
.pc.used-hi.used-lo {
  box-shadow: 0 0 0 2px var(--gold), 0 0 0 4px #4a90d9, 0 0 9px rgba(74, 144, 217, 0.6);
}

.replay-legend { display: flex; gap: 12px; font-size: 11px; color: var(--text-dim); }
.replay-legend span { display: flex; align-items: center; gap: 5px; }
.replay-legend i { width: 9px; height: 9px; border-radius: 2px; display: inline-block; }
.replay-legend i.ring-hi { background: var(--gold); }
.replay-legend i.ring-lo { background: #4a90d9; }
.suit-s { color: #14161b; }
.suit-h { color: #c0392b; }
.suit-d { color: #2170b8; }
.suit-c { color: #1e7a45; }

.replay-controls {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.replay-controls button { padding: 5px 12px; font-size: 12px; }
.replay-unit { margin-bottom: 0; margin-left: 4px; }
.replay-unit .unit-opt { padding: 3px 10px; font-size: 11px; }
.replay-status {
  font-family: var(--num);
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  color: var(--text-dim);
  margin-left: 6px;
}
.replay-scrub { margin-top: 12px; }
.replay-scrub input {
  width: 100%;
  height: 14px;
  margin: 0;
  cursor: pointer;
  background: none;
  -webkit-appearance: none;
  appearance: none;
}
.replay-scrub input::-webkit-slider-runnable-track {
  height: 4px;
  background: var(--border);
  border-radius: 2px;
}
.replay-scrub input::-moz-range-track {
  height: 4px;
  background: var(--border);
  border-radius: 2px;
}
.replay-scrub input::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 13px;
  height: 13px;
  margin-top: -4.5px;
  border-radius: 50%;
  background: var(--gold);
  border: 2px solid var(--bg);
  cursor: pointer;
}
.replay-scrub input::-moz-range-thumb {
  width: 13px;
  height: 13px;
  border-radius: 50%;
  background: var(--gold);
  border: 2px solid var(--bg);
  cursor: pointer;
}

@media (max-width: 720px) {
  .seat { width: 96px; }
  .replay-table { min-height: 320px; }
}
```

## 6. Design decisions worth keeping if you adapt this

- **Frame-based, not simulated.** The backend does all the "what does the
  table look like after this action" work once, up front. The frontend
  only ever indexes into a precomputed array — no stepping logic to get
  wrong, no drift between forward/backward playback.
- **Cards never shrink to fit.** A row that would overflow its container
  (a stud player's seventh card, say) just overflows, centred, rather than
  scaling every card down. Fixed sizes read better than small ones.
- **Hero's cards are bigger than everyone else's** (`.pc.hero` vs
  `.pc.sm`) — you're always most interested in the cards that are actually
  yours to read.
- **`hidden_before` for deal-order layout.** Rather than hard-coding "cards
  1–2 hidden, 3–6 shown, 7 hidden" in the frontend, that split is data the
  backend computes per game type, so the same renderer works for any game
  with a mixed face-up/face-down deal.
- **A single `fmt()` chokepoint for units.** Every number on screen — pot,
  stacks, bets — funnels through one function. Adding a unit toggle later
  only meant changing that one function, not every call site.
