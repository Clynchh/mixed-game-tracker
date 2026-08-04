/* Hand replayer. The server precomputes a full snapshot of the table for
   every step of the hand, so this just renders frame N - stepping back and
   forward can't drift out of sync with the real hand. */

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
    // the user isn't typing into the tag/note inputs.
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
