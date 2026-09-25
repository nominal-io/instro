// Header links (Guides, Forum, social icons) leave the reference, so open them in a new tab.
document.addEventListener("DOMContentLoaded", () => {
  for (const a of document.querySelectorAll('.sy-head a[href^="http"]')) {
    a.target = "_blank";
    a.rel = "noopener";
  }
});
