"""``driver-cards`` directive: a card grid of the driver pages in a folder.

Each driver page carries its card in front matter::

    ---
    card: B&K Precision 9115
    image: BK9115.png
    ---

``:::{driver-cards} psu`` then renders one card per such page in ``psu/``, sorted by
card title, so adding a driver is just adding its page and image.
"""

from pathlib import Path

import yaml
from docutils import nodes
from docutils.statemachine import StringList
from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective


def _front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    return yaml.safe_load(text[4 : text.index("\n---\n", 4)]) or {}


class DriverCards(SphinxDirective):
    required_arguments = 1

    def run(self) -> list[nodes.Node]:
        folder = self.arguments[0].strip("/")
        cards = []
        for page in sorted(Path(self.env.srcdir, folder).glob("*.md")):
            self.env.note_dependency(str(page))
            meta = _front_matter(page)
            if "card" in meta:
                cards.append((meta["card"], f"/{folder}/{page.stem}", f"/{folder}/{meta['image']}"))
        if not cards:
            return [
                self.state.document.reporter.warning(f"driver-cards: no driver pages in {folder}/", line=self.lineno)
            ]
        lines = ["::::{grid} 1 2 2 2", ":gutter: 3", ""]
        for title, doc, image in sorted(cards):
            lines += [
                f":::{{grid-item-card}} {title}",
                f":link: {doc}",
                ":link-type: doc",
                f":img-bottom: {image}",
                ":class-card: driver-card",
                ":::",
                "",
            ]
        lines.append("::::")
        container = nodes.container()
        self.state.nested_parse(StringList(lines, source=self.get_source_info()[0]), self.content_offset, container)
        return container.children


def setup(app: Sphinx) -> dict[str, bool]:
    app.add_directive("driver-cards", DriverCards)
    return {"parallel_read_safe": True}
