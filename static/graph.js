/* Small dependency-free SVG line chart: drag to zoom (horizontal range
   select), click a point to select it. No external chart library - the app
   never makes a network request, so pulling one in would break that. */

const SVG_NS = "http://www.w3.org/2000/svg";

function svgEl(tag, attrs) {
  const e = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}

function fmtAxisNum(v) {
  if (Math.abs(v) >= 100) return Math.round(v).toString();
  if (Math.abs(v) >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

class LineChart {
  /**
   * container: DOM element to render into.
   * points: array of data objects, oldest first. Each must have a numeric
   *   value for every line's `key`.
   * lines: [{key, color, width}] - drawn in array order, so put the line
   *   that should sit on top last.
   * options: {onPointClick: fn(point, index)} - omit for a non-clickable
   *   chart (still zoomable).
   */
  constructor(container, points, lines, options) {
    this.container = container;
    this.points = points;
    this.lines = lines;
    this.options = options || {};
    this.width = 900;
    this.height = this.options.height || 200;
    this.padL = 48;
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

    svg.addEventListener("mousedown", (e) => this._onMouseDown(e));
    window.addEventListener("mousemove", (e) => this._onMouseMove(e));
    window.addEventListener("mouseup", (e) => this._onMouseUp(e));
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
    this._dragStart = this._svgPoint(e.clientX, e.clientY);
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
    const visible = this.points.slice(lo, hi + 1);
    if (visible.length === 0) return;

    let yMin = 0, yMax = 0;
    for (const p of visible) {
      for (const line of this.lines) {
        const v = p[line.key];
        if (v < yMin) yMin = v;
        if (v > yMax) yMax = v;
      }
    }
    if (yMin === yMax) { yMin -= 1; yMax += 1; }
    const yPad = (yMax - yMin) * 0.08;
    yMin -= yPad;
    yMax += yPad;

    const innerW = this.width - this.padL - this.padR;
    const innerH = this.height - this.padT - this.padB;
    const n = visible.length;
    const xOf = (i) => (n === 1 ? this.padL : this.padL + (i / (n - 1)) * innerW);
    const yOf = (v) => this.padT + (1 - (v - yMin) / (yMax - yMin)) * innerH;

    // Y-axis gridlines + value labels (evenly spaced ticks).
    const yTicks = 4;
    for (let t = 0; t <= yTicks; t++) {
      const v = yMin + (t / yTicks) * (yMax - yMin);
      const y = yOf(v);
      svg.appendChild(svgEl("line", {
        x1: this.padL, y1: y.toFixed(1), x2: this.width - this.padR, y2: y.toFixed(1),
        stroke: "#2c313c", "stroke-width": "1", "stroke-dasharray": "2,4",
      }));
      const label = svgEl("text", {
        x: this.padL - 6, y: (y + 3).toFixed(1), "text-anchor": "end",
        class: "chart-axis-label", "font-size": "10",
      });
      label.textContent = fmtAxisNum(v);
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
      zeroLabel.textContent = "0";
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
  }
}

function selectHandFromGraph(handId, unit) {
  fetch(`/api/hand/${handId}/row?unit=${encodeURIComponent(unit)}`)
    .then((r) => (r.ok ? r.text() : null))
    .then((html) => {
      if (!html) return;
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
