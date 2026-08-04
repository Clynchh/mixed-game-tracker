/* Dual-handle range slider: two overlapping native <input type=range>
   elements (one for min, one for max) styled to look like one control, with
   a filled track between the two handles. No library - it's two form
   fields that submit normally, JS just keeps them from crossing and
   updates the visible fill/labels. */

function initRangeSlider(container) {
  const minInput = container.querySelector("input[type=range]:first-of-type");
  const maxInput = container.querySelector("input[type=range]:last-of-type");
  const fill = container.querySelector(".rs-fill");
  const minLabel = container.querySelector(".rs-min-label");
  const maxLabel = container.querySelector(".rs-max-label");

  function fmt(v) {
    const n = parseFloat(v);
    if (Math.abs(n) >= 1000) return Math.round(n).toString();
    if (Math.abs(n) >= 10) return n.toFixed(1);
    return n.toFixed(2);
  }

  function update() {
    const lo = parseFloat(minInput.min);
    const hi = parseFloat(minInput.max);
    let minV = parseFloat(minInput.value);
    let maxV = parseFloat(maxInput.value);
    if (minV > maxV) {
      if (document.activeElement === minInput) maxInput.value = minV;
      else minInput.value = maxV;
      minV = parseFloat(minInput.value);
      maxV = parseFloat(maxInput.value);
    }
    const range = hi - lo || 1;
    fill.style.left = `${((minV - lo) / range) * 100}%`;
    fill.style.width = `${((maxV - minV) / range) * 100}%`;
    minLabel.textContent = fmt(minV);
    maxLabel.textContent = fmt(maxV);
  }

  minInput.addEventListener("input", update);
  maxInput.addEventListener("input", update);
  update();
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".range-slider").forEach(initRangeSlider);
});
