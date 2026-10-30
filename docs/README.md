# instro docs

Source for the [`instro`](https://github.com/nominal-io/instro) documentation: guides, examples, and the API reference, built with [Sphinx](https://www.sphinx-doc.org) into one site.

## Running locally

Needs Python 3.12+ (the `docs` dependency group installs on first run). From the repo root:

```bash
just serve-docs   # live preview on http://127.0.0.1:8000, rebuilds on page, example, or docstring edits
just build-docs   # full build into docs/_build/dirhtml; warnings fail it, as in CI
```

To view a finished build, serve it rather than opening the files, since pages link to folders (`/psu/`):

```bash
python -m http.server 8000 -d docs/_build/dirhtml
```

## How it works

- **Guides** are Markdown (MyST) pages in this folder. Each driver has its own page and image; `driver-cards` builds each category's card grid from them.
- **Examples** are generated at build time from the scripts in `examples/` (and the `examples/` folders inside `instro-unstable` and `instro-contrib`), one page per script: the docstring's first line is the title, the rest the description.
- **API reference** (`sdk/`) comes from docstrings via autodoc/autosummary: one page per class, with its members on it. Vendor packages import from source with their hardware SDKs mocked, so no drivers need installing.
- **Navigation** is the `toctree` blocks in `index.md`. The header tabs (Guides, Examples, SDK) switch which part the sidebar shows.
- **Look**: the Shibuya theme with Nominal's shared styling, [`nominal-sphinx-theme`](https://github.com/nominal-io/nominal-sphinx-theme). `_static/custom.css` holds only instro's own styles (driver cards and images, logo size).
- **Old URLs**: `_extra/404.html` (copied to the site root) redirects pages from before #607 (under `/python/`) and the old per-member API pages (under `generated/`).
- **Search engines**: `sitemap.xml` (sphinx-sitemap) and `_extra/robots.txt`. `_add_seo` in `conf.py` sets each page's `<title>` and meta description:
  - Guide pages: `seo_title:` and `myst: html_meta: description:` in front matter. Category pages target searches like "python control power supply"; without them, the page title and first paragraph are used.
  - Driver pages: generated from `card:` and the folder's entry in `_DRIVER_CATEGORIES`, e.g. "B&K Precision 9115 Python Driver (BK9115)". A new category needs an entry.
  - API pages: the object and module ("InstroPSU (instro.psu) - instro Python API") and the docstring summary.
- **CI**: PRs run a strict build and upload the site as a `docs-site` artifact; merges to `main` deploy to GitHub Pages.

Conventions and common tasks are in [CONTRIBUTING.md](./CONTRIBUTING.md) and [AGENTS.md](./AGENTS.md).
