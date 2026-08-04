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

function backHtml(n) {
  let out = "";
  for (let i = 0; i < n; i++) out += '<span class="pc pc-back"></span>';
  return out;
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
    this._build();
    this.render();
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
              <div class="seat-name">${s.is_hero ? "★ " : ""}${s.name}${s.is_button ? ' <span class="btn-chip">D</span>' : ""}</div>
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
        <span class="replay-status"></span>
      </div>
      <div class="replay-scrub"><input type="range" min="0" max="${this.data.frames.length - 1}" value="0"></div>
    `;

    this.boardEl = this.root.querySelector(".board-cards");
    this.potEl = this.root.querySelector(".pot-display");
    this.seatEls = Array.from(this.root.querySelectorAll(".seat"));
    this.statusEl = this.root.querySelector(".replay-status");
    this.scrub = this.root.querySelector(".replay-scrub input");
    this.playBtn = this.root.querySelector('[data-act="play"]');

    this.root.querySelectorAll("[data-act]").forEach((b) => {
      b.addEventListener("click", () => this.act(b.dataset.act));
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
    this.boardEl.innerHTML = f.board.map((c) => cardHtml(c)).join("");
    this.potEl.innerHTML = `<span class="pot-label">Pot</span> <span class="pot-amount">${fmtChips(f.pot)}</span>`;
    this.statusEl.textContent = `${this.i + 1} / ${this.data.frames.length} · ${f.street} · ${f.label}`;
    this.scrub.value = this.i;

    f.seats.forEach((s, idx) => {
      const el = this.seatEls[idx];
      el.classList.toggle("folded", s.folded);
      el.classList.toggle("acting", s.acting);
      el.classList.toggle("winner", s.won > 0);
      el.querySelector(".seat-stack").textContent = fmtChips(s.stack);
      const bet = el.querySelector(".seat-bet");
      bet.innerHTML = s.bet > 0 ? `<span class="chip-dot"></span>${fmtChips(s.bet)}` : "";
      bet.style.visibility = s.bet > 0 ? "visible" : "hidden";
      const before = s.hidden_before || 0;
      el.querySelector(".seat-cards").innerHTML =
        backHtml(before) + s.cards.map((c) => cardHtml(c, "sm")).join("") + backHtml(s.hidden - before);
    });
  }
}
