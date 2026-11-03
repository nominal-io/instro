// Header links (Guides, Forum, social icons) leave the docs, so open them in a new tab.
document.addEventListener("DOMContentLoaded", () => {
  for (const a of document.querySelectorAll('.sy-head a[href^="http"]')) {
    a.target = "_blank";
    a.rel = "noopener";
  }
});


// The "#" after each heading also copies a link to that section, so it can be pasted straight into
// a message. "Copied" shows beside it for a moment (nominal.css). The link still goes to the section.
document.addEventListener("DOMContentLoaded", () => {
  if (!navigator.clipboard) return;
  for (const a of document.querySelectorAll("a.headerlink")) a.title = "Copy link to this section";
});
document.addEventListener("click", (event) => {
  const a = event.target.closest("a.headerlink");
  if (!a || !navigator.clipboard) return;
  navigator.clipboard.writeText(a.href).then(() => {
    a.classList.add("nominal-copied");
    setTimeout(() => a.classList.remove("nominal-copied"), 1500);
  });
});


// With nominal_sidebar_open set, each sidebar group's caption is a button (rendered by the theme,
// with each group open or folded to start); a click opens or closes the group under it. Each button
// gets its own listener: Shibuya stops clicks inside the sidebar from reaching the document.
document.addEventListener("DOMContentLoaded", () => {
  for (const button of document.querySelectorAll("button.nominal-fold")) {
    button.addEventListener("click", () => {
      const open = button.getAttribute("aria-expanded") !== "true";
      button.setAttribute("aria-expanded", String(open));
      button.parentElement.nextElementSibling.hidden = !open;
    });
  }
});


// "On this page" highlights the entry for the heading the reader is at: the last heading at or above
// the top of the viewport (below the sticky header), or the last one at the bottom of the page.
// Shibuya marks entries `.active` by its own rules; this marks `.nominal-current` instead, which
// nominal.css styles. A clicked entry stays current until the reader scrolls by hand, because the
// page may be too short to bring its heading to the top.
document.addEventListener("DOMContentLoaded", () => {
  const toc = document.querySelector(".sy-rside .localtoc");
  if (!toc) return;
  const entries = [];
  for (const link of toc.querySelectorAll("a[href^='#']")) {
    const target = document.getElementById(decodeURIComponent(link.hash.slice(1)));
    if (target) entries.push({ link, target });
  }
  if (!entries.length) return;

  // Shibuya's spy also scrolls the list to its own pick; shadowing its scroll call leaves that to
  // setCurrent below.
  for (const item of toc.querySelectorAll("li")) item.scrollIntoViewIfNeeded = null;

  let current = null;
  let pinned = null; // the entry the reader chose, until they scroll by hand

  const setCurrent = (entry) => {
    if (entry === current) return;
    current?.link.classList.remove("nominal-current");
    current?.link.removeAttribute("aria-current");
    current = entry;
    entry.link.classList.add("nominal-current");
    entry.link.setAttribute("aria-current", "true");
    const box = toc.closest(".sy-rside-inner");
    if (!box) return;
    const at = entry.link.getBoundingClientRect();
    const view = box.getBoundingClientRect();
    if (at.top < view.top) box.scrollTop -= view.top - at.top;
    else if (at.bottom > view.bottom) box.scrollTop += at.bottom - view.bottom;
  };

  const update = () => {
    if (pinned) return setCurrent(pinned);
    const atBottom = window.innerHeight + Math.round(window.scrollY) >= document.documentElement.scrollHeight;
    if (atBottom) return setCurrent(entries[entries.length - 1]);
    // An anchor jump puts the target at its scroll-margin-top, give or take a pixel.
    let found = entries[0];
    for (const entry of entries) {
      if (!entry.target.getClientRects().length) continue; // hidden, e.g. in a closed <details>
      const margin = parseFloat(getComputedStyle(entry.target).scrollMarginTop) || 0;
      if (entry.target.getBoundingClientRect().top > margin + 4) break;
      found = entry;
    }
    setCurrent(found);
  };

  const pin = (entry) => {
    pinned = entry;
    update();
  };
  const unpin = () => {
    pinned = null;
  };

  toc.addEventListener("click", (event) => {
    const entry = entries.find((e) => e.link === event.target.closest("a"));
    if (entry) pin(entry);
  });
  window.addEventListener("hashchange", () => {
    const entry = entries.find((e) => e.link.hash === location.hash);
    if (entry) pin(entry);
    else {
      unpin();
      update();
    }
  });
  for (const type of ["wheel", "touchmove", "keydown", "pointerdown"]) window.addEventListener(type, unpin, { passive: true });
  window.addEventListener("scroll", update, { passive: true });
  window.addEventListener("resize", update);

  const landed = entries.find((e) => e.link.hash === location.hash);
  if (landed) pin(landed);
  else update();
});
