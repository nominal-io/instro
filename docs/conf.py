"""Sphinx config for the instro docs: the guides (this folder) and the API reference (sdk/)."""

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE / "_ext"))

# Vendor workspace packages aren't installed in the dev env; put their sources on
# the path so autodoc can import them. instro's drivers packages
# use pkgutil.extend_path, so these merge into instro.daq.drivers / instro.i2c.drivers.
for pkg in ("instro-daq-ni", "instro-daq-labjack", "instro-daq-mcc", "instro-i2c-aardvark"):
    sys.path.insert(0, str(HERE.parent / "packages" / pkg))

project = "instro"
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
    "driver_cards",
    "examples",
    "sections",
]

templates_path = ["_templates"]
exclude_patterns = [
    "_build",
    "guides",  # the Mintlify site, live until the domain moves (DOCS_MIGRATION.md)
    # contributor docs, at the top level and in any folder
    *[
        f"{prefix}{name}"
        for prefix in ("", "**/")
        for name in ("README.md", "AGENTS.md", "CLAUDE.md", "CONTRIBUTING.md")
    ],
    "DOCS_MIGRATION.md",
    "NEW_DOCS_CHANGES.md",
]

# Single backticks in docstrings (`Measurement`) link to the named object when it
# resolves and render as code otherwise.
default_role = "py:obj"

# -- MyST ---------------------------------------------------------------------
myst_enable_extensions = ["colon_fence", "deflist", "attrs_inline", "attrs_block", "fieldlist"]
myst_heading_anchors = 6

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

# -- HTML: Shibuya, styled like the former Mintlify site -------------------------
html_theme = "shibuya"
html_title = "instro"
# the docs are published under /python/ (landing page at the root; `just build-site`)
html_baseurl = "https://nominal-io.github.io/instro/python/"
html_static_path = ["_static"]
html_css_files = [
    "https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap",
    "custom.css",
]
html_js_files = ["external-links.js"]
html_favicon = "_static/favicon.png"
html_copy_source = False

html_context = {
    "source_type": "github",
    "source_user": "nominal-io",
    "source_repo": "instro",
    "source_version": "main",
    "source_docs_path": "/docs/",
}

# Right sidebar: on-page contents and edit link, no GitHub repo-stats box.
html_sidebars = {"**": ["sidebars/localtoc.html", "sidebars/edit-this-page.html"]}

html_theme_options = {
    "accent_color": "gray",
    "light_logo": "_static/logo/instro-logo-solid-black.svg",
    "dark_logo": "_static/logo/instro-logo-solid-white.svg",
    "github_url": "https://github.com/nominal-io/instro",
    "discord_url": "https://discord.gg/nN4RzhQkr",
    "linkedin_url": "https://linkedin.com/company/nominal-io",
    "x_url": "https://x.com/nominal_io",
    # section tabs; the sidebar follows them (_ext/sections.py)
    "nav_links": [
        {"title": "Guides", "url": "index"},
        {"title": "Examples", "url": "examples/index"},
        {"title": "SDK", "url": "sdk/index"},
        {"title": "GitHub", "url": "https://github.com/nominal-io/instro"},
    ],
    # right of the search box; "Get a demo" follows them (_templates/partials/nav-socials.html)
    "nav_socials": [
        "github",
        "x",
        "discord",
        {"name": "Forum", "url": "https://community.instro.nominal.io", "icon": "lucide:messages-square"},
        "linkedin",
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


def setup(app):
    app.connect("autodoc-process-docstring", _drop_basemodel_init_doc)
    app.connect("env-updated", _alias_reexports)
