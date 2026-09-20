"""Render the whole app to plain HTML files that open straight from disk.

The dashboard normally needs a server because the page is built per request
from SQLite. But every page it can produce is fully determined by the
database plus a handful of filter values, so they can all be rendered ahead
of time and written out as files. Opening index.html over file:// then gets
the same pages, with no port and nothing running.

Two things genuinely can't survive the trip, and are switched off rather
than left to fail silently:

  * Writes (tagging, notes, settings, "rescan now"). A page loaded from
    disk has nowhere to put them.
  * Anything using fetch(). Browsers refuse file:// -> file:// requests, so
    the scan-status poller and the click-a-graph-point row loader are
    replaced with static equivalents.

Search and tag filtering move into the browser: they hide table rows rather
than re-querying, so the stats and the graph above the table keep showing
the whole (game-filtered) set. Filtering by game does regenerate those,
because those pages are pre-rendered.
"""
import html
import os
import re
import shutil
import sys

os.environ.setdefault("MGT_NO_BROWSER", "1")

import db

OUT_DIR = os.environ.get("MGT_EXPORT_DIR") or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "static-export"
)

# Routes that only make sense with a server behind them. Links to these are
# stripped rather than followed.
DEAD_PREFIXES = ("/api/", "/settings", "/setup")


def _slug(value):
    return re.sub(r"[^A-Za-z0-9]+", "-", str(value)).strip("-").lower() or "any"


def dashboard_file(mode, unit):
    """Only mode and unit need a page of their own. Filtering by game is
    already done in the browser (the game boxes recompute the banner and the
    graph from the embedded per-hand points), so pre-rendering a page per
    game would just be 10x the bytes for behaviour the page already has."""
    return f"dashboard-{mode}-{unit}.html"


def hand_file(hand_id):
    return f"hands/{_slug(hand_id)}.html"


def _query_of(url):
    return url.split("?", 1)[1] if "?" in url else ""


def _parse_query(qs):
    out = {}
    for part in qs.split("&"):
        if not part:
            continue
        k, _, v = part.partition("=")
        from urllib.parse import unquote_plus
        out[unquote_plus(k)] = unquote_plus(v)
    return out


class Exporter:
    def __init__(self, out_dir=OUT_DIR, include_hands=True):
        self.out_dir = out_dir
        self.include_hands = include_hands
        import app as app_module

        self.app_module = app_module
        self.client = app_module.app.test_client()
        self.default_mode = None
        self.pages = {}   # url path+query -> output filename
        self.written = 0
        self.skipped = 0

    # ---------------------------------------------------------------- plan

    def plan(self):
        """Work out every page to render before rendering any of them, so
        links can be rewritten in a single pass - a page can link forwards to
        one that hasn't been generated yet."""
        cash = db.overall_stats(is_tournament=0)["hands"] or 0
        tourney = db.overall_stats(is_tournament=1)["hands"] or 0
        self.default_mode = "tournament" if tourney > cash else "cash"

        modes = [m for m, n in (("cash", cash), ("tournament", tourney)) if n]
        if not modes:
            modes = [self.default_mode]

        for mode in modes:
            for unit in ("raw", "bb"):
                # show_all=1 because the row cap exists to keep a server
                # response small; a file on disk has no such round trip, and
                # a truncated list would silently hide hands that the in-page
                # search is expected to find.
                q = f"mode={mode}&unit={unit}&show_all=1"
                self.pages[f"/?{q}"] = dashboard_file(mode, unit)

        if self.include_hands:
            for h in db.list_hands(limit=None):
                self.pages[f"/hand/{h['hand_id']}"] = hand_file(h["hand_id"])
        return self

    # ------------------------------------------------------------- rewrite

    def _target_for(self, url):
        """Map an in-app URL to the exported file that stands in for it."""
        path = url.split("?", 1)[0]
        qs = _parse_query(_query_of(url))

        if path.startswith("/static/"):
            return url.lstrip("/")
        if path.startswith(DEAD_PREFIXES):
            return None
        if path.startswith("/hand/"):
            rest = path[len("/hand/"):]
            # Only a bare /hand/<id> is a page. /hand/<id>/tag and /untag are
            # POST endpoints, and must not be rewritten to the hand's own
            # page - "Add tag" would then look like it silently did nothing.
            if "/" in rest.rstrip("/"):
                return None
            f = hand_file(rest.strip("/"))
            return f if f in self.pages.values() else None
        if path == "/reports":
            return None
        if path == "/":
            mode = qs.get("mode") or self.default_mode
            unit = qs.get("unit") or ("bb" if mode == "tournament" else "raw")
            # A game_type filter in the URL has no page of its own; it lands
            # on the unfiltered page, where the game boxes do the same job.
            f = dashboard_file(mode, unit)
            return f if f in self.pages.values() else None
        return None

    _LINK_RE = re.compile(r'\b(href|src|action)=(["\'])(/[^"\']*)\2')

    def _rewrite_links(self, text, page_file):
        here = os.path.dirname(page_file)

        def sub(m):
            attr, quote, url = m.group(1), m.group(2), m.group(3)
            target = self._target_for(html.unescape(url))
            if target is None:
                # Kept as an anchor so layout doesn't shift; the shim styles
                # and disables these.
                return f'{attr}={quote}#{quote} data-offline="1"'
            rel = os.path.relpath(target, here) if here else target
            return f"{attr}={quote}{rel}{quote}"

        text = self._LINK_RE.sub(sub, text)

        # Row clicks navigate from a JS string rather than an href, so they
        # need the same treatment.
        def sub_js(m):
            target = self._target_for(m.group(2))
            if target is None:
                return "void 0"
            rel = os.path.relpath(target, here) if here else target
            return f"window.location={m.group(1)}{rel}{m.group(1)}"

        return re.sub(r"window\.location=(['\"])(/[^'\"]*)\1", sub_js, text)

    def _inject_shim(self, text, page_file):
        depth = page_file.count("/")
        root = "../" * depth
        shim = STATIC_SHIM.replace("__ROOT__", root)
        # Must land after graph.js/widgets.js have defined the functions it
        # overrides, but before base.html's inline script (end of <body>)
        # calls pollScanStatus.
        if "</head>" in text:
            return text.replace("</head>", shim + "\n</head>", 1)
        return shim + text

    def _strip_external(self, text):
        """The app is offline-only by design; an export that phones out to a
        CDN for a donate widget would quietly break that promise."""
        return re.sub(
            r'<script src="https://storage\.ko-fi\.com.*?</script>\s*<script>.*?</script>',
            "", text, flags=re.S)

    # -------------------------------------------------------------- render

    def render_page(self, url, page_file):
        resp = self.client.get(url)
        if resp.status_code != 200:
            print(f"  ! {url} -> HTTP {resp.status_code}, skipped")
            self.skipped += 1
            return
        text = resp.data.decode("utf-8")
        text = self._strip_external(text)
        text = self._rewrite_links(text, page_file)
        text = self._inject_shim(text, page_file)

        dest = os.path.join(self.out_dir, page_file)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # Rewriting a byte-identical file every time would give every hand
        # page a new mtime on each run and make incremental exports
        # pointless.
        new = text.encode("utf-8")
        if os.path.exists(dest):
            with open(dest, "rb") as fh:
                if fh.read() == new:
                    self.skipped += 1
                    return
        with open(dest, "wb") as fh:
            fh.write(new)
        self.written += 1

    def run(self, progress=True):
        os.makedirs(self.out_dir, exist_ok=True)
        src_static = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
        shutil.copytree(src_static, os.path.join(self.out_dir, "static"), dirs_exist_ok=True)

        items = list(self.pages.items())
        for i, (url, page_file) in enumerate(items, 1):
            self.render_page(url, page_file)
            if progress and (i % 200 == 0 or i == len(items)):
                print(f"  {i}/{len(items)} pages")

        # index.html is what gets double-clicked, so the default view is
        # written a second time under that name rather than linked to it -
        # a symlink wouldn't survive being copied to another machine.
        landing = dashboard_file(self.default_mode,
                                 "bb" if self.default_mode == "tournament" else "raw")
        shutil.copyfile(os.path.join(self.out_dir, landing),
                        os.path.join(self.out_dir, "index.html"))
        return self


STATIC_SHIM = """
<style>[data-offline] { opacity: .4; pointer-events: none; }</style>
<script>
/* This page was exported to disk. Anything that needed the server is
   replaced here rather than left to throw. */
window.MGT_STATIC = true;

/* No server to poll - the import banner can never apply. */
window.pollScanStatus = function () {};
window.rescanNow = function () {
  alert("This is an exported copy. Run the app to import new hands, then export again.");
};
window.toggleQuickTag = function (handId, tag, isActive, event) {
  if (event) event.stopPropagation();
  alert("Tags are read-only in the exported copy. Tag hands in the app, then export again.");
};

/* Clicking a graph point normally fetches a table row to pin. fetch() is
   blocked on file://, so go to the hand's own page instead. */
window.selectHandFromGraph = function (handId) {
  window.location.href = "__ROOT__hands/" + String(handId).replace(/[^A-Za-z0-9]+/g, "-").toLowerCase() + ".html";
};

document.addEventListener("DOMContentLoaded", function () {
  /* The hand page's tag editor posts to the server. Disable it outright and
     say why, rather than leaving a button that appears to do nothing. */
  document.querySelectorAll('form[data-offline]').forEach(function (f) {
    f.querySelectorAll("input, button, select, textarea").forEach(function (el) {
      el.disabled = true;
    });
    var msg = document.createElement("div");
    msg.style.cssText = "font-size:12px;opacity:.7;margin-top:6px;";
    msg.textContent = "Read-only export - tag this hand in the app, then export again.";
    f.appendChild(msg);
  });

  var form = document.querySelector("form[data-autofilter]");
  if (!form) return;

  /* Search and tag have no pre-rendered page per value, so they filter the
     rows already in the table. The stats and graph above deliberately keep
     showing the whole set - recomputing them here would mean a second,
     drifting copy of the aggregation the app already does in SQL. */
  var search = form.querySelector("input[name=search]");
  var tagSel = form.querySelector("select[name=tag]");
  var tbody = document.getElementById("hands-tbody");

  function apply() {
    if (!tbody) return;
    var q = (search && search.value || "").trim().toLowerCase();
    var tag = tagSel && tagSel.value || "";
    var shown = 0;
    tbody.querySelectorAll("tr").forEach(function (tr) {
      var text = tr.textContent.toLowerCase();
      var tags = (tr.getAttribute("data-tags") || "").split(",");
      var ok = (!q || text.indexOf(q) !== -1) && (!tag || tags.indexOf(tag) !== -1);
      tr.style.display = ok ? "" : "none";
      if (ok) shown++;
    });
    var note = document.getElementById("mgt-filter-note");
    if (note) {
      note.textContent = (q || tag)
        ? shown + " of " + tbody.querySelectorAll("tr").length + " hands shown (stats above cover all of them)"
        : "";
    }
  }

  var note = document.createElement("div");
  note.id = "mgt-filter-note";
  note.style.cssText = "font-size:12px;opacity:.7;margin:4px 0;";
  form.parentNode.insertBefore(note, form.nextSibling);

  /* The app's autofilter submits the form on every change, which on file://
     would navigate to a query string that isn't a file. */
  form.addEventListener("submit", function (e) { e.preventDefault(); apply(); });
  if (search) {
    search.addEventListener("input", apply);
    search.addEventListener("keydown", function (e) {
      if (e.key === "Enter") { e.preventDefault(); apply(); }
    });
  }
  if (tagSel) tagSel.addEventListener("change", apply);
  apply();
});
</script>
"""


def main():
    include_hands = "--no-hands" not in sys.argv
    out = OUT_DIR
    if "--out" in sys.argv:
        out = sys.argv[sys.argv.index("--out") + 1]

    print(f"Exporting to {out}")
    ex = Exporter(out_dir=out, include_hands=include_hands).plan()
    print(f"  {len(ex.pages)} pages planned"
          f"{'' if include_hands else ' (hand pages skipped)'}")
    ex.run()

    total = sum(
        os.path.getsize(os.path.join(dirpath, f))
        for dirpath, _, files in os.walk(out) for f in files
    )
    print(f"\n  {ex.written} written, {ex.skipped} unchanged")
    print(f"  {total / 1e6:.1f} MB total")
    print(f"\n  Open: file://{os.path.abspath(os.path.join(out, 'index.html'))}")


if __name__ == "__main__":
    main()
