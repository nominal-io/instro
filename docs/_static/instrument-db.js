/* Search, filter, and regroup the instrument list on /instruments/. The page works without this: the full
   lists are in the HTML. Entries come from the JSON the build embeds (#idb-data). */
(function () {
  "use strict";

  var root = document.querySelector("[data-idb]");
  var dataEl = document.getElementById("idb-data");
  if (!root || !dataEl) return;

  var entries = JSON.parse(dataEl.textContent);
  var input = root.querySelector(".idb-search");
  var categorySelect = root.querySelector(".idb-category");
  var status = root.querySelector(".idb-status");
  var results = root.querySelector(".idb-results");
  var resultsBody = results.querySelector("tbody");
  var browses = root.querySelectorAll(".idb-browse");
  var items = root.querySelectorAll(".idb-item");
  var driverChips = root.querySelectorAll("[data-driver]");
  var viewChips = root.querySelectorAll("[data-view]");

  var state = { q: "", drivers: new Set(), category: "", view: "type" };

  // ---- index: lowercase tokens, plus the name with separators removed ("keithley2400" matches "keithley 2400")
  function tokens(text) {
    return text.toLowerCase().split(/[^a-z0-9]+/).filter(Boolean);
  }
  entries.forEach(function (e) {
    var fields = { m: e.m, mo: e.mo, cl: e.cl, ct: e.ct, t: e.t, n: e.n };
    e._tok = {};
    e._all = [];
    Object.keys(fields).forEach(function (k) {
      e._tok[k] = tokens(fields[k]);
      e._all = e._all.concat(e._tok[k]);
    });
    e._compact = (e.m + e.mo + e.cl).toLowerCase().replace(/[^a-z0-9]/g, "");
    e._summary = e.s.toLowerCase();
  });

  // Edit distance <= 1 (insert, delete, substitute, or swap): enough for typos without false hits.
  function nearly(a, b) {
    if (a === b) return true;
    if (Math.abs(a.length - b.length) > 1) return false;
    var i = 0;
    while (i < a.length && i < b.length && a[i] === b[i]) i++;
    if (a.length === b.length) {
      return a.slice(i + 1) === b.slice(i + 1) ||
        (a[i] === b[i + 1] && a[i + 1] === b[i] && a.slice(i + 2) === b.slice(i + 2));
    }
    return a.length > b.length ? a.slice(i + 1) === b.slice(i) : b.slice(i + 1) === a.slice(i);
  }

  // Score one query token against an entry; 0 means no match.
  function tokenScore(e, q) {
    var best = 0;
    for (var f in e._tok) {
      var weight = f === "mo" || f === "cl" ? 1.2 : f === "m" ? 1.1 : 1;
      for (var i = 0; i < e._tok[f].length; i++) {
        var t = e._tok[f][i];
        if (t === q) best = Math.max(best, 100 * weight);
        else if (t.indexOf(q) === 0) best = Math.max(best, 60 * weight);
        else if (q.length >= 3 && t.indexOf(q) > 0) best = Math.max(best, 25 * weight);
        else if (q.length >= 4 && nearly(q, t.slice(0, q.length + 1))) best = Math.max(best, 12 * weight);
      }
    }
    if (!best && q.length >= 2 && e._compact.indexOf(q) >= 0) best = 20;
    if (!best && q.length >= 4 && e._summary.indexOf(q) >= 0) best = 8;
    return best;
  }

  function score(e, qs) {
    var total = 0;
    for (var i = 0; i < qs.length; i++) {
      var s = tokenScore(e, qs[i]);
      if (!s) return 0; // every word must match something
      total += s;
    }
    // the whole query as one string, for "2400" vs "keithley2400"
    if (qs.length > 1 && e._compact.indexOf(qs.join("")) >= 0) total += 40;
    return total;
  }

  function passesFilters(e) {
    return (!state.drivers.size || state.drivers.has(e.t)) && (!state.category || e.c === state.category);
  }

  // ---- render
  function esc(s) {
    return s.replace(/[&<>"]/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c];
    });
  }
  function resultHtml(e) {
    return '<tr><td><a href="' + esc(e.u) + '">' + esc(e.n) + "</a></td><td>" + esc(e.ct) + "</td><td>" + esc(e.t) + "</td></tr>";
  }

  function render() {
    var qs = tokens(state.q);
    var searching = qs.length > 0;
    var shown = 0;

    if (searching) {
      var hits = [];
      entries.forEach(function (e, i) {
        if (!passesFilters(e)) return;
        var s = score(e, qs);
        if (s) hits.push({ e: e, s: s, i: i });
      });
      hits.sort(function (a, b) { return b.s - a.s || a.i - b.i; });
      resultsBody.innerHTML = hits.map(function (h) { return resultHtml(h.e); }).join("");
      shown = hits.length;
      status.textContent = shown ? shown + (shown === 1 ? " result" : " results") : 'No instruments match "' + state.q + '". Try a manufacturer or model number.';
    } else {
      var visible = 0;
      items.forEach(function (li) {
        var ok = passesFilters(entries[+li.getAttribute("data-i")]);
        li.hidden = !ok;
      });
      root.querySelectorAll(".idb-group").forEach(function (g) {
        var any = g.querySelector(".idb-item:not([hidden])");
        g.hidden = !any;
      });
      // each instrument sits in one type group and one manufacturer group, so count one view
      entries.forEach(function (e) { if (passesFilters(e)) visible++; });
      shown = visible;
      status.textContent = shown + (shown === 1 ? " instrument" : " instruments");
    }

    results.hidden = !searching;
    browses.forEach(function (b) {
      b.hidden = searching || b.getAttribute("data-group") !== state.view;
    });
  }

  // ---- state in the URL, so a search or filter can be shared
  function readUrl() {
    var p = new URLSearchParams(location.search);
    state.q = p.get("q") || "";
    state.category = p.get("type") || "";
    state.view = p.get("by") === "manufacturer" ? "manufacturer" : "type";
    state.drivers = new Set((p.get("driver") || "").split(",").filter(Boolean));
  }
  function writeUrl() {
    var p = new URLSearchParams();
    if (state.q) p.set("q", state.q);
    if (state.category) p.set("type", state.category);
    if (state.drivers.size) p.set("driver", Array.from(state.drivers).join(","));
    if (state.view === "manufacturer") p.set("by", "manufacturer");
    var qs = p.toString();
    try { history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash); } catch (err) { /* sandboxed */ }
  }
  function syncControls() {
    input.value = state.q;
    categorySelect.value = state.category;
    driverChips.forEach(function (c) { c.setAttribute("aria-pressed", String(state.drivers.has(c.getAttribute("data-driver")))); });
    viewChips.forEach(function (c) { c.setAttribute("aria-pressed", String(c.getAttribute("data-view") === state.view)); });
  }
  function update() { syncControls(); render(); writeUrl(); }

  input.addEventListener("input", function () { state.q = input.value; update(); });
  categorySelect.addEventListener("change", function () { state.category = categorySelect.value; update(); });
  driverChips.forEach(function (c) {
    c.addEventListener("click", function () {
      var d = c.getAttribute("data-driver");
      if (state.drivers.has(d)) state.drivers.delete(d); else state.drivers.add(d);
      update();
    });
  });
  viewChips.forEach(function (c) {
    c.addEventListener("click", function () { state.view = c.getAttribute("data-view"); update(); });
  });
  // "/" focuses the search, as on most docs sites
  document.addEventListener("keydown", function (ev) {
    var tag = (ev.target.tagName || "").toLowerCase();
    if (ev.key === "/" && tag !== "input" && tag !== "textarea" && tag !== "select" && !ev.metaKey && !ev.ctrlKey) {
      ev.preventDefault();
      input.focus();
    }
  });

  readUrl();
  update();
})();
