"""``{pysummary}`instro.x.Y``` role: the object's docstring summary, as autosummary shows it.

Lets hand-laid-out tables (extra columns autosummary can't produce) keep their
descriptions sourced from docstrings.
"""

import inspect
import re

from docutils import nodes
from sphinx.application import Sphinx
from sphinx.ext.autosummary import extract_summary, import_by_name
from sphinx.util.docutils import SphinxRole

_LITERAL = re.compile(r"``(.+?)``")


class PySummaryRole(SphinxRole):
    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        try:
            _, obj, _, _ = import_by_name(self.text)
        except ImportError:
            msg = self.inliner.reporter.warning(f"pysummary: cannot import {self.text!r}", line=self.lineno)
            return [nodes.problematic(self.rawtext, self.rawtext)], [msg]
        doc = inspect.getdoc(obj) or ""
        summary = extract_summary(doc.splitlines(), self.inliner.document.settings)
        # Summaries are reST; render ``code`` spans, leave the rest as text.
        parts = _LITERAL.split(summary)
        return [nodes.literal(p, p) if i % 2 else nodes.Text(p) for i, p in enumerate(parts) if p], []


def setup(app: Sphinx) -> dict[str, bool]:
    app.add_role("pysummary", PySummaryRole())
    return {"parallel_read_safe": True}
