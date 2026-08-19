/* Small dependency-free SVG line chart: drag to zoom (horizontal range
   select), click a point to select it. No external chart library - the app
   never makes a network request, so pulling one in would break that. */

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

// Axis ticks are already snapped to round numbers (see the "nice" step math
// in render()), so this only needs to strip trailing zeros - never force
// them the way a fixed decimal count would (50 as "50.0", 0 as "0.00").
function fmtAxisNum(v) {
  if (Math.abs(v) < 1e-9) return "0";
  return (Math.round(v * 100) / 100).toString();
}

function fmtSigned(v) {
  return (v >= 0 ? "+" : "") + v.toFixed(2);
}

function ordinal(n) {
  const suffixes = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (suffixes[(v - 20) % 10] || suffixes[v] || suffixes[0]);
}

const HTML_ESCAPES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
// Points on this chart carry fields parsed straight out of a hand-history
// text file (stakes, game type) - a hand history is just a .txt file
// someone could hand-craft and share, so tooltipHtml callbacks that build
// HTML strings from point data must escape it before it goes into
// innerHTML, the same as any other untrusted text.
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => HTML_ESCAPES[c]);
}

class LineChart {
  /**
   * container: DOM element to render into.
   * points: array of data objects, oldest first. Each must have a numeric
   *   value for every line's `key`.
   * lines: [{key, color, width}] - drawn in array order, so put the line
   *   that should sit on top last.
   * options: {onPointClick: fn(point, index), tooltipHtml: fn(point, index)}
   *   - both omittable for a non-clickable, tooltip-less chart (still
   *   zoomable either way).
   */
  constructor(container, points, lines, options) {
    this.container = container;
    this.points = points;
    this.lines = lines;
    this.options = options || {};
    this.width = 900;
    // Kept in step with .line-chart's CSS height (they scale together at
    // 2.5x the original 900x200 / 180px). The SVG uses
    // preserveAspectRatio="none", so raising only one of the two would
    // stretch the axis labels rather than just make the plot taller.
    this.height = this.options.height || 500;
    // Extra room on the left when ticks carry a unit suffix (e.g. " BI") -
    // otherwise it gets clipped against the edge of the SVG.
    this.padL = this.options.yAxisSuffix ? 66 : 48;
    this.padR = 12;
    this.padT = 10;
    this.padB = 22;
    this.range = [0, Math.max(0, points.length - 1)];
    this._buildDom();
    this.render();
  }

  _buildDom() {
    this.container.innerHTML = "";
    this.container.classList.add("chart-container");

    const resetBtn = document.createElement("button");
    resetBtn.type = "button";
    resetBtn.className = "btn-ghost chart-reset-zoom";
    resetBtn.textContent = "Reset zoom";
    resetBtn.style.display = "none";
    resetBtn.onclick = () => {
      this.range = [0, this.points.length - 1];
      this.render();
    };
    this.container.appendChild(resetBtn);
    this.resetBtn = resetBtn;

    const svg = svgEl("svg", {
      viewBox: `0 0 ${this.width} ${this.height}`,
      class: "line-chart",
      preserveAspectRatio: "none",
    });
    this.svg = svg;
    this.container.appendChild(svg);

    const tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.style.display = "none";
    this.container.appendChild(tooltip);
    this.tooltip = tooltip;

    svg.addEventListener("mousedown", (e) => this._onMouseDown(e));
    svg.addEventListener("mousemove", (e) => this._onHover(e));
    svg.addEventListener("mouseleave", () => this._hideTooltip());
    window.addEventListener("mousemove", (e) => this._onMouseMove(e));
    window.addEventListener("mouseup", (e) => this._onMouseUp(e));
  }

  _hideTooltip() {
    this.tooltip.style.display = "none";
    if (this._hoverGroup) {
      this._hoverGroup.remove();
      this._hoverGroup = null;
    }
  }

  _onHover(e) {
    if (this._dragging) return;
    const p = this._svgPoint(e.clientX, e.clientY);
    const idx = this._xToIndex(p.x);
    const rawPoint = this.points[idx];
    if (!rawPoint || !this._xOf || !this._yOf || !this._offsets) {
      this._hideTooltip();
      return;
    }
    // Same rebasing as render() - the hover dot and tooltip numbers have to
    // agree with where the line is actually drawn.
    const point = Object.assign({}, rawPoint);
    for (const line of this.lines) point[line.key] = rawPoint[line.key] - this._offsets[line.key];

    // Vertical guide line + a dot on each series at the hovered index, so
    // it's visually obvious which point the tooltip is describing.
    if (this._hoverGroup) this._hoverGroup.remove();
    const group = svgEl("g", {});
    const x = this._xOf(idx - this.range[0]);
    group.appendChild(svgEl("line", {
      x1: x.toFixed(1), y1: this.padT, x2: x.toFixed(1), y2: this.height - this.padB,
      stroke: "#8b90a0", "stroke-width": "1", "stroke-dasharray": "3,3",
    }));
    for (const line of this.lines) {
      group.appendChild(svgEl("circle", {
        cx: x.toFixed(1), cy: this._yOf(point[line.key]).toFixed(1), r: "3", fill: line.color,
      }));
    }
    this.svg.appendChild(group);
    this._hoverGroup = group;

    if (!this.options.tooltipHtml) return;
    this.tooltip.innerHTML = this.options.tooltipHtml(point, idx);
    this.tooltip.style.display = "block";
    const containerBox = this.container.getBoundingClientRect();
    let left = e.clientX - containerBox.left + 14;
    if (left + 190 > containerBox.width) left = e.clientX - containerBox.left - 204;
    this.tooltip.style.left = `${left}px`;
    this.tooltip.style.top = `${e.clientY - containerBox.top + 14}px`;
  }

  _svgPoint(clientX, clientY) {
    const ctm = this.svg.getScreenCTM();
    if (!ctm) return { x: 0, y: 0 };
    const pt = this.svg.createSVGPoint();
    pt.x = clientX;
    pt.y = clientY;
    const loc = pt.matrixTransform(ctm.inverse());
    return { x: loc.x, y: loc.y };
  }

  _xToIndex(svgX) {
    const [lo, hi] = this.range;
    const n = hi - lo;
    if (n <= 0) return lo;
    const innerW = this.width - this.padL - this.padR;
    const frac = (svgX - this.padL) / innerW;
    return Math.max(lo, Math.min(hi, Math.round(lo + frac * n)));
  }

  _onMouseDown(e) {
    if (e.button !== 0) return;
    this._hideTooltip();
    this._dragStart = this._svgPoint(e.clientX, e.clientY);
    // Cleared here, not just left over from whatever the last interaction
    // did - otherwise a click with zero jitter right after a drag-zoom
    // measures its distance against the previous drag's endpoint instead
    // of this click, and can get misread as a (usually no-op) zoom attempt.
    this._dragCurrent = null;
    this._dragging = true;
    this._selRect = svgEl("rect", {
      x: this._dragStart.x, y: this.padT, width: 0,
      height: this.height - this.padT - this.padB,
      class: "chart-select-box",
    });
    this.svg.appendChild(this._selRect);
  }

  _onMouseMove(e) {
    if (!this._dragging) return;
    const p = this._svgPoint(e.clientX, e.clientY);
    this._dragCurrent = p;
    const x0 = Math.min(this._dragStart.x, p.x);
    const w = Math.abs(p.x - this._dragStart.x);
    this._selRect.setAttribute("x", x0);
    this._selRect.setAttribute("width", w);
  }

  _onMouseUp() {
    if (!this._dragging) return;
    this._dragging = false;
    if (this._selRect) {
      this._selRect.remove();
      this._selRect = null;
    }
    const p = this._dragCurrent || this._dragStart;
    const dx = Math.abs(p.x - this._dragStart.x);

    if (dx < 6) {
      const idx = this._xToIndex(this._dragStart.x);
      if (this.options.onPointClick && this.points[idx]) {
        this.options.onPointClick(this.points[idx], idx);
      }
      return;
    }

    const i0 = this._xToIndex(Math.min(this._dragStart.x, p.x));
    const i1 = this._xToIndex(Math.max(this._dragStart.x, p.x));
    if (i1 - i0 < 1) return;
    this.range = [i0, i1];
    this.render();
  }

  render() {
    const svg = this.svg;
    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const [lo, hi] = this.range;
    this.resetBtn.style.display = (lo === 0 && hi === this.points.length - 1) ? "none" : "";
    const rawVisible = this.points.slice(lo, hi + 1);
    if (rawVisible.length === 0) return;

    // Every line is rebased to start at 0 at the left edge of whatever's
    // visible - dragging to zoom into a later stretch shows how that
    // stretch went on its own, not the all-time cumulative total carried in
    // from before the zoom. Applies even unzoomed: the leftmost point is
    // hand/tournament #1's own result, not exactly 0, so this pins it too.
    const offsets = {};
    for (const line of this.lines) offsets[line.key] = rawVisible[0][line.key];
    this._offsets = offsets;
    const visible = rawVisible.map((p) => {
      const rebased = Object.assign({}, p);
      for (const line of this.lines) rebased[line.key] = p[line.key] - offsets[line.key];
      return rebased;
    });

    // 0 is always exactly in the vertical middle - the axis is symmetric
    // (yMax = -yMin), sized to whichever line is furthest from 0 in the
    // visible range, rather than fit tightly to just the highs or just the
    // lows. That keeps 0 pinned to the same spot at every zoom level while
    // still using the full height for whatever's currently in view.
    let maxAbs = 0;
    for (const p of visible) {
      for (const line of this.lines) {
        const v = Math.abs(p[line.key]);
        if (v > maxAbs) maxAbs = v;
      }
    }
    if (maxAbs === 0) maxAbs = 1;

    // Just enough headroom that a line touching the extreme isn't flush
    // against the edge - the axis bound itself stays tight to the data
    // rather than rounding outward to the next "nice" tick step, which
    // used to waste up to half the chart's height as empty margin.
    const bound = maxAbs * 1.05;
    let yMin = -bound, yMax = bound;

    // Tick step still lands on nice round numbers (0/25/50/...), but ticks
    // only go up to whatever fits inside the (tight) bound rather than the
    // bound stretching out to meet them.
    const yTicks = 4;
    const roughStep = (2 * bound) / yTicks;
    const magnitude = Math.pow(10, Math.floor(Math.log10(roughStep)));
    const residual = roughStep / magnitude;
    const step = (residual > 5 ? 10 : residual > 2 ? 5 : residual > 1 ? 2 : 1) * magnitude;
    const tickBound = Math.floor(bound / step) * step;

    const innerW = this.width - this.padL - this.padR;
    const innerH = this.height - this.padT - this.padB;
    const n = visible.length;
    const xOf = (i) => (n === 1 ? this.padL : this.padL + (i / (n - 1)) * innerW);
    const yOf = (v) => this.padT + (1 - (v - yMin) / (yMax - yMin)) * innerH;

    // Y-axis gridlines + value labels, symmetric around 0 in steps of `step`,
    // capped at tickBound (a multiple of step) rather than the axis bound.
    const numTicks = tickBound > 0 ? Math.round((2 * tickBound) / step) : 0;
    for (let t = 0; t <= numTicks; t++) {
      const v = -tickBound + t * step;
      const y = yOf(v);
      svg.appendChild(svgEl("line", {
        x1: this.padL, y1: y.toFixed(1), x2: this.width - this.padR, y2: y.toFixed(1),
        stroke: "#2c313c", "stroke-width": "1", "stroke-dasharray": "2,4",
      }));
      const label = svgEl("text", {
        x: this.padL - 6, y: (y + 3).toFixed(1), "text-anchor": "end",
        class: "chart-axis-label", "font-size": "10",
      });
      label.textContent = fmtAxisNum(v) + (this.options.yAxisSuffix || "");
      svg.appendChild(label);
    }

    // Dedicated zero baseline, drawn separately from the regular ticks so
    // it's always exactly at 0 (not just whichever tick lands closest) and
    // clearly stands out - solid and brighter instead of dashed and dim.
    if (yMin <= 0 && yMax >= 0) {
      const zeroY = yOf(0);
      svg.appendChild(svgEl("line", {
        x1: this.padL, y1: zeroY.toFixed(1), x2: this.width - this.padR, y2: zeroY.toFixed(1),
        stroke: "#8b90a0", "stroke-width": "1.5",
      }));
      const zeroLabel = svgEl("text", {
        x: this.padL - 6, y: (zeroY + 3).toFixed(1), "text-anchor": "end",
        class: "chart-axis-label chart-zero-label", "font-size": "10",
      });
      zeroLabel.textContent = "0" + (this.options.yAxisSuffix || "");
      svg.appendChild(zeroLabel);
    }

    // X-axis position labels (a handful, evenly spaced across the visible range).
    const xTickCount = Math.min(6, n);
    for (let t = 0; t < xTickCount; t++) {
      const idx = Math.round((t / Math.max(1, xTickCount - 1)) * (n - 1));
      const label = svgEl("text", {
        x: xOf(idx).toFixed(1), y: this.height - 6, "text-anchor": "middle",
        class: "chart-axis-label", "font-size": "10",
      });
      label.textContent = String(lo + idx + 1);
      svg.appendChild(label);
    }

    // Lines - no fill, drawn in array order so later entries sit on top.
    for (const line of this.lines) {
      const d = "M " + visible.map((p, i) => `${xOf(i).toFixed(1)},${yOf(p[line.key]).toFixed(1)}`).join(" L ");
      svg.appendChild(svgEl("path", {
        d, fill: "none", stroke: line.color, "stroke-width": line.width || 2,
      }));
    }

    // A line can opt in to small tick marks flagging specific points along
    // it (e.g. the all-in EV line marking which hands actually went all
    // in) - drawn last so they sit on top of every line, not just their own.
    for (const line of this.lines) {
      if (!line.markKey) continue;
      visible.forEach((p, i) => {
        if (!p[line.markKey]) return;
        const x = xOf(i);
        const y = yOf(p[line.key]);
        svg.appendChild(svgEl("line", {
          x1: x.toFixed(1), y1: (y - 4).toFixed(1), x2: x.toFixed(1), y2: (y + 4).toFixed(1),
          stroke: line.color, "stroke-width": "1.5",
        }));
      });
    }

    // Reused by hover to place the guide line/dots without recomputing scale.
    this._xOf = (i) => xOf(i);
    this._yOf = yOf;
    this._hoverGroup = null;
  }
}

// Clicking a second point before the first one's request has come back
// must never let the first (now-stale) response land after the second -
// otherwise the row pinned at the end of a quick double-click can be
// whichever request happened to resolve last, not whichever was clicked
// last. Each call gets its own ticket; a response only gets applied if no
// newer click has happened since it went out.
let _selectHandRequestId = 0;

function selectHandFromGraph(handId, unit) {
  const requestId = ++_selectHandRequestId;
  fetch(`/api/hand/${handId}/row?unit=${encodeURIComponent(unit)}`)
    .then((r) => (r.ok ? r.text() : null))
    .then((html) => {
      if (!html || requestId !== _selectHandRequestId) return;
      const tbody = document.getElementById("hands-tbody");
      if (!tbody) return;
      const existingPinned = tbody.querySelector(".pinned-row");
      if (existingPinned) existingPinned.remove();
      const existingSame = document.getElementById(`hand-row-${handId}`);
      if (existingSame) existingSame.remove();
      tbody.insertAdjacentHTML("afterbegin", html);
      const newRow = document.getElementById(`hand-row-${handId}`);
      if (newRow) newRow.scrollIntoView({ behavior: "smooth", block: "center" });
    });
}

/** Polls /api/scan-status while an import is running (kicked off by setup,
 *  a folder change, "scan now", or "re-read every hand"), so the page can
 *  show a spinner/progress bar instead of looking stuck for however long a
 *  big folder takes to read. Calls onTick(status) on every poll, including
 *  the final one where status.active is false. */
function pollScanStatus(onTick, intervalMs) {
  const tick = () => {
    fetch("/api/scan-status")
      .then((r) => r.json())
      .then((status) => {
        onTick(status);
        if (status.active) setTimeout(tick, intervalMs || 600);
      })
      .catch(() => {});
  };
  tick();
}
