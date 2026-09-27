# instro docs

Source for the [`instro`](https://github.com/nominal-io/instro) documentation: guides, examples, and the API reference, built with [Sphinx](https://www.sphinx-doc.org) into one site.

## Running locally

Needs Python 3.12+ (the `docs` dependency group installs on first run). From the repo root:

```bash
just serve-docs   # live preview on http://127.0.0.1:8000, rebuilds on page, example, or docstring edits
just build-docs   # full build into docs/_build/dirhtml; warnings fail it, as in CI
just build-site   # the published site in docs/_build/site: landing page at the root, docs under /python/
```

To view a finished build, serve it rather than opening the files, since pages link to folders (`/psu/`):

```bash
python -m http.server 8000 -d docs/_build/dirhtml
```

## How it works

- **Guides** are Markdown (MyST) pages in this folder. Each driver has its own page and image; `driver-cards` builds each category's card grid from them.
- **Examples** are generated at build time from the scripts in `examples/` (and the `examples/` folders inside `instro-unstable` and `instro-contrib`), one page per script.
- **API reference** (`sdk/`) comes from docstrings via autodoc/autosummary: a page per class, and per method for behavioural classes. Vendor packages import from source with their hardware SDKs mocked, so no drivers need installing.
- **Navigation** is the `toctree` blocks in `index.md`. The header tabs (Guides, Examples, SDK) switch which part the sidebar shows.
- **Look**: the Shibuya theme, styled in `_static/custom.css` to match the former Mintlify site.
- **Landing page**: `_landing/index.html` is the published site's root; `just build-site` puts it there and the docs under `/python/`.
- **CI**: PRs run a strict build and upload the site as a `docs-site` artifact; merges to `main` deploy to GitHub Pages.

Conventions and common tasks are in [CONTRIBUTING.md](./CONTRIBUTING.md) and [AGENTS.md](./AGENTS.md).
