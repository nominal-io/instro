"""``{pysummary}`instro.x.Y``` role: the object's docstring summary, as autosummary shows it.

Lets hand-laid-out tables (extra columns autosummary can't produce) keep their
descriptions sourced from docstrings.
"""

import inspect
from types import SimpleNamespace

from docutils import nodes
from docutils.parsers.rst import languages as rst_languages
from docutils.parsers.rst import states
from sphinx.application import Sphinx
from sphinx.ext.autosummary import ImportExceptionGroup, extract_summary, import_by_name
from sphinx.util.docutils import SphinxRole


class PySummaryRole(SphinxRole):
    def run(self) -> tuple[list[nodes.Node], list[nodes.system_message]]:
        try:
            _, obj, _, _ = import_by_name(self.text)
        except (ImportError, ImportExceptionGroup):
            msg = self.inliner.reporter.warning(f"pysummary: cannot import {self.text!r}", line=self.lineno)
            return [nodes.problematic(self.rawtext, self.rawtext)], [msg]
        document = self.inliner.document
        doc = inspect.getdoc(obj) or ""
        summary = extract_summary(doc.splitlines(), document.settings)
        # Docstrings are reST, but the host page may be MyST, whose inliner parses
        # Markdown. Render with a reST inliner so ``literals``, default_role names,
        # and :role:`targets` come out as they do in autosummary's own tables.
        inliner = states.Inliner()
        inliner.init_customizations(document.settings)
        memo = SimpleNamespace(
            document=document,
            reporter=document.reporter,
            language=rst_languages.get_language(document.settings.language_code, document.reporter),
        )
        return inliner.parse(summary, self.lineno, memo, self.inliner.parent)


def setup(app: Sphinx) -> dict[str, bool]:
    app.add_role("pysummary", PySummaryRole())
    return {"parallel_read_safe": True}
