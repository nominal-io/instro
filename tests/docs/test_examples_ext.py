import importlib.util
from pathlib import Path

import yaml

_spec = importlib.util.spec_from_file_location(
    "docs_examples_ext", Path(__file__).parents[2] / "docs" / "_ext" / "examples.py"
)
assert _spec is not None and _spec.loader is not None
examples = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(examples)


def _front(page: str) -> dict:
    return yaml.safe_load(page.removeprefix("---\n").partition("\n---\n")[0])


def test_notebook_page_keeps_existing_myst_settings():
    text = (
        "---\nkernelspec:\n  name: python3\nmyst:\n  substitutions:\n    key: value\n"
        "  html_meta:\n    keywords: instro\n---\n\n# Bench demo\n\nWatch a supply.\n"
    )

    title, page = examples._notebook_page(text, "bench")

    front = _front(page)
    assert title == "Bench demo"
    assert front["kernelspec"] == {"name": "python3"}
    assert front["myst"]["substitutions"] == {"key": "value"}
    assert front["myst"]["html_meta"] == {
        "keywords": "instro",
        "description": "instro notebook example: Bench demo. Watch a supply.",
    }
    assert page.endswith("\n# Bench demo\n\nWatch a supply.\n")


def test_notebook_page_keeps_its_own_description():
    text = '---\nmyst:\n  html_meta:\n    description: "Mine"\n---\n\n# Bench demo\n'

    assert _front(examples._notebook_page(text, "bench")[1])["myst"]["html_meta"]["description"] == "Mine"
