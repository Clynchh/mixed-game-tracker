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

/* Filter forms apply themselves as you change them, so there's no Filter
   button to press. Everything listens for "change" rather than "input":
   on a dropdown or checkbox that's immediate, on a slider it fires when you
   let go of the handle (not continuously through the drag), and on a text
   box when you press Enter or click away - so a page reload never yanks the
   cursor out from under you mid-word. */
function initAutoFilter(form) {
  form.querySelectorAll("select, input[type=checkbox], input[type=range]").forEach((el) => {
    el.addEventListener("change", () => form.submit());
  });
  form.querySelectorAll("input[type=text], input[type=number]").forEach((el) => {
    el.addEventListener("change", () => form.submit());
    // These forms have no submit button, so Enter needs handling explicitly.
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        form.submit();
      }
    });
  });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".range-slider").forEach(initRangeSlider);
  document.querySelectorAll("form[data-autofilter]").forEach(initAutoFilter);
});
