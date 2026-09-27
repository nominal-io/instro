"""Show only the current section of the site (Guides, Examples, SDK) in the left sidebar.

The root toctree holds every section's captioned groups; this trims the rendered
sidebar to the groups whose pages live in the current page's section, like the
tabs on the former Mintlify site. A section is a URL prefix; the guides are
everything else.
"""

import posixpath
import re

from sphinx.application import Sphinx

SECTIONS = ("sdk/", "examples/")
_BLOCK = re.compile(r'(?=<p class="caption")')
_HREF = re.compile(r'href="([^"#]*)')


def _section(path: str) -> str:
    return next((s for s in SECTIONS if path.startswith(s)), "")


def _on_page(app: Sphinx, pagename: str, templatename: str, context: dict, doctree) -> None:
    toctree = context.get("toctree")
    if toctree is None:
        return
    if app.builder.name == "dirhtml":
        page_dir = "" if pagename == "index" else pagename.removesuffix("/index") + "/"
    else:
        page_dir = posixpath.dirname(pagename) + "/"
    current = _section(pagename)

    def section_toctree(**kwargs) -> str:
        blocks = _BLOCK.split(toctree(**kwargs))
        kept = []
        for block in blocks:
            href = _HREF.search(block)
            if not href:
                continue
            target = posixpath.normpath(posixpath.join(page_dir, href.group(1) or "."))
            if _section(target + "/") == current:
                kept.append(block)
        return "".join(kept)

    context["toctree"] = section_toctree


def setup(app: Sphinx) -> dict[str, bool]:
    app.connect("html-page-context", _on_page)
    return {"parallel_read_safe": True}
