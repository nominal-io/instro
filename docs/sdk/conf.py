"""Sphinx config for the instro SDK reference, published at https://nominal-io.github.io/instro/."""

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "_ext"))

# Vendor workspace packages aren't installed in the dev env; put their sources on
# the path so autodoc can import them. instro's drivers packages
# use pkgutil.extend_path, so these merge into instro.daq.drivers / instro.i2c.drivers.
for pkg in ("instro-daq-ni", "instro-daq-labjack", "instro-daq-mcc", "instro-i2c-aardvark"):
    sys.path.insert(0, str(HERE.parent.parent / "packages" / pkg))

project = "instro SDK"
copyright = "Nominal, Inc."

extensions = [
    "myst_parser",
    "sphinx.ext.autodoc",
    "sphinx.ext.autosummary",
    "sphinx.ext.napoleon",
    "sphinx.ext.intersphinx",
    "sphinx.ext.viewcode",
    "sphinxcontrib.autodoc_pydantic",
    "sphinx_design",
    "sphinx_copybutton",
    "pysummary",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "AGENTS.md", "CLAUDE.md"]

# Single backticks in docstrings (`Measurement`) link to the named object when it
# resolves and render as code otherwise.
default_role = "py:obj"

# -- MyST ---------------------------------------------------------------------
myst_enable_extensions = ["colon_fence", "deflist", "attrs_inline", "fieldlist"]
myst_heading_anchors = 4

# -- autodoc -------------------------------------------------------------------
autodoc_default_options = {
    "members": True,
    "undoc-members": True,  # show_if_no_docstring
    # stop at these bases so pydantic/enum/exception/builtin internals stay out
    "inherited-members": "BaseModel,object,BaseException,Enum,str,int,float",
    "member-order": "bysource",
    "show-inheritance": True,
}
autoclass_content = "both"  # merge_init_into_class
autodoc_typehints = "signature"
autodoc_preserve_defaults = True
python_maximum_signature_line_length = 72  # separate_signature + line_length
python_use_unqualified_type_names = True  # show_root_full_path: false
toc_object_entries_show_parents = "hide"

# Native vendor SDKs (Windows DLLs / hardware drivers). mcculw itself is installed
# for its pure-Python enums, which appear in annotations; only its DLL-backed
# modules are mocked.
autodoc_mock_imports = [
    "nidaqmx",
    "labjack",
    "mcculw.ul",
    "mcculw.device_info",
    "pyaardvark",
    "pythoncom",
    "win32com",
]

# Class pages list members in summary tables; each member gets its own page
# (the scikit-rf layout). Stubs are written to <page dir>/generated/.
autosummary_generate = True
autosummary_generate_overwrite = True
autosummary_context = {
    # private methods documented alongside the public ones (supported extension points)
    "extra_methods": {
        "instro.lib.instrument.Instrument": ["_package_command", "_package_measurement"],
    },
}

napoleon_google_docstring = True
napoleon_numpy_docstring = False
napoleon_use_rtype = False
napoleon_use_ivar = True  # Attributes: sections as a field list, not duplicate targets

autodoc_pydantic_model_show_json = False
autodoc_pydantic_model_show_config_summary = False
autodoc_pydantic_model_show_validator_summary = False
autodoc_pydantic_model_show_validator_members = False
autodoc_pydantic_model_show_field_summary = False
autodoc_pydantic_field_list_validators = False
autodoc_pydantic_model_member_order = "bysource"

intersphinx_mapping = {"python": ("https://docs.python.org/3", None)}

# -- HTML: Shibuya, styled toward the Mintlify guides site --------------------
html_theme = "shibuya"
html_title = "instro SDK"
html_baseurl = "https://nominal-io.github.io/instro/"
html_static_path = ["_static", "../guides/logo"]
html_css_files = [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    "custom.css",
]
html_js_files = ["external-links.js"]
html_favicon = "../guides/favicon.png"
html_copy_source = False

html_context = {
    "source_type": "github",
    "source_user": "nominal-io",
    "source_repo": "instro",
    "source_version": "main",
    "source_docs_path": "/docs/sdk/",
}

# Right sidebar: on-page contents and edit link, no GitHub repo-stats box.
html_sidebars = {"**": ["sidebars/localtoc.html", "sidebars/edit-this-page.html"]}

html_theme_options = {
    "accent_color": "gray",
    "light_logo": "_static/instro-logo-solid-black.svg",
    "dark_logo": "_static/instro-logo-solid-white.svg",
    "github_url": "https://github.com/nominal-io/instro",
    "discord_url": "https://discord.gg/nN4RzhQkr",
    "linkedin_url": "https://linkedin.com/company/nominal-io",
    "x_url": "https://x.com/nominal_io",
    "nav_links": [
        {"title": "Guides", "url": "https://instro.nominal.io"},
        {"title": "Forum", "url": "https://community.instro.nominal.io"},
    ],
    "toctree_titles_only": True,
    # left nav lists pages only; generated class/member pages are reached from their tables
    "toctree_maxdepth": 1,
}
add_module_names = False


def _drop_basemodel_init_doc(app, what, name, obj, options, lines):
    """Drop pydantic's generic ``__init__`` docstring from model pages.

    autoclass_content="both" appends __init__'s docstring; for pydantic models
    that don't define one, that's BaseModel's generic "Create a new model…" text.
    """
    from pydantic import BaseModel
    from sphinx.util.docstrings import prepare_docstring

    if what == "pydantic_model" and "__init__" not in vars(obj):
        base = [ln.strip() for ln in prepare_docstring(BaseModel.__init__.__doc__) if ln.strip()]
        if [ln.strip() for ln in lines if ln.strip()] == base:
            lines.clear()


def _alias_reexports(app, env):
    """Resolve re-exported names to the documented original.

    Points instro.dmm.config.TimingConfig at instro.lib.config.TimingConfig, for
    example. Without this, a type annotation in the re-exporting module falls back
    to a fuzzy match and can pick an unrelated class of the same name
    (instro.modbus.types.TimingConfig).
    """
    py = env.get_domain("py")
    for modname, mod in list(sys.modules.items()):
        if not modname.startswith("instro") or mod is None:
            continue
        for attr, obj in list(vars(mod).items()):
            origin = getattr(obj, "__module__", None)
            qualname = getattr(obj, "__qualname__", None)
            if attr.startswith("_") or not origin or not qualname or origin == modname:
                continue
            target, alias = f"{origin}.{qualname}", f"{modname}.{attr}"
            if target in py.objects and alias not in py.objects:
                py.objects[alias] = py.objects[target]._replace(aliased=True)


# Library pages lived under /reference/ on the old mkdocs site; keep those URLs working.
LEGACY_REDIRECTS = {
    f"reference/{page}/": f"library/{page}/"
    for page in ("instrument", "types", "exceptions", "publishers", "discover", "transports")
}


def _write_legacy_redirects(app, exception):
    """Write a redirect page at each moved URL (dirhtml builds only, which serve those URLs)."""
    if exception is not None or app.builder.name != "dirhtml":
        return
    for old, new in LEGACY_REDIRECTS.items():
        target = "../" * old.count("/") + new
        page = Path(app.outdir) / old / "index.html"
        page.parent.mkdir(parents=True, exist_ok=True)
        page.write_text(
            '<!DOCTYPE html>\n<html><head><meta charset="utf-8"><title>Moved</title>\n'
            f'<link rel="canonical" href="{html_baseurl}{new}">\n'
            f'<meta http-equiv="refresh" content="0; url={target}"></head>\n'
            f'<body><p>This page moved to <a href="{target}">{html_baseurl}{new}</a>.</p></body></html>\n',
            encoding="utf-8",
        )


def setup(app):
    app.connect("autodoc-process-docstring", _drop_basemodel_init_doc)
    app.connect("env-updated", _alias_reexports)
    app.connect("build-finished", _write_legacy_redirects)
