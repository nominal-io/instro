"""``driver-cards`` directive: a card grid of the driver pages in a folder.

Each driver page carries its card in front matter::

    ---
    card: B&K Precision 9115
    image: BK9115.png
    ---

``:::{driver-cards} psu`` then renders one card per such page in ``psu/``, sorted by
card title, so adding a driver is just adding its page and image.
"""

import re
from pathlib import Path

import yaml
from docutils import nodes
from docutils.statemachine import StringList
from sphinx.application import Sphinx
from sphinx.util.docutils import SphinxDirective

_FRONT_MATTER = re.compile(r"\A---\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


def _front_matter(path: Path) -> dict:
    """Parse the page's YAML front matter; {} when there is none.

    Raises:
        ValueError: On an opening fence with no closing one, or YAML that isn't a mapping.
    """
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---"):
        return {}
    match = _FRONT_MATTER.match(text)
    if match is None:
        raise ValueError("front matter is not closed by a '---' line")
    meta = yaml.safe_load(match.group(1)) or {}
    if not isinstance(meta, dict):
        raise ValueError("front matter is not a mapping")
    return meta


class DriverCards(SphinxDirective):
    required_arguments = 1

    def run(self) -> list[nodes.Node]:
        folder = self.arguments[0].strip("/")
        cards = []
        warnings: list[str] = []
        for page in sorted(Path(self.env.srcdir, folder).glob("*.md")):
            self.env.note_dependency(str(page))
            try:
                meta = _front_matter(page)
            except (ValueError, yaml.YAMLError) as exc:
                warnings.append(f"driver-cards: {page.relative_to(self.env.srcdir)}: {exc}")
                continue
            if "card" not in meta:
                continue
            if "image" not in meta:
                warnings.append(f"driver-cards: {page.relative_to(self.env.srcdir)}: 'card' needs an 'image'")
                continue
            cards.append((meta["card"], f"/{folder}/{page.stem}", f"/{folder}/{meta['image']}"))
        reporter = self.state.document.reporter
        problems = [reporter.warning(msg, line=self.lineno) for msg in warnings]
        if not cards:
            return [*problems, reporter.warning(f"driver-cards: no driver pages in {folder}/", line=self.lineno)]
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
        return [*problems, *container.children]


def setup(app: Sphinx) -> dict[str, bool]:
    app.add_directive("driver-cards", DriverCards)
    return {"parallel_read_safe": True}
