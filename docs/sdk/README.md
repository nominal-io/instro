# instro SDK reference

Source for the API reference at https://nominal-io.github.io/instro/, built with [Sphinx](https://www.sphinx-doc.org). The prose guides live separately in [`docs/guides/`](../guides/).

## Running locally

Needs Python 3.12+ (the `docs` dependency group installs automatically on first run). From the repo root:

```bash
just serve-docs    # live preview on http://127.0.0.1:8000, rebuilds on page or docstring edits
just build-docs    # full build into docs/sdk/_build/dirhtml; warnings fail it, as in CI
just check-sdk-links   # after a build: fail on guides links to missing SDK pages
```

To view a finished build, serve it rather than opening the files, since pages link to folders (`/instruments/psu/`):

```bash
python -m http.server 8000 -d docs/sdk/_build/dirhtml
```

## How it works

- Pages are Markdown (MyST). They mostly lay out the site; the API content comes from docstrings.
- Each category page (`instruments/psu.md`, …) lists classes in `autosummary` tables. Sphinx generates a page per class, and a page per method for behavioural classes, into gitignored `generated/` folders. The templates in `_templates/autosummary/` control those pages.
- Docstrings are Google style, parsed as reStructuredText: ` ``code`` ` for literals, and `` `Name` `` to link to another object.
- Vendor packages under `packages/` are imported from source, with their hardware SDKs mocked in `conf.py`, so no drivers need installing.
- The sidebar comes from the `toctree` blocks in `index.md`. Styling is in `_static/custom.css`.
- PRs run a strict build and the link check, and upload the built site as an `sdk-docs` artifact. Merges to `main` deploy to GitHub Pages.

Adding a driver or category, and the page conventions, are covered in [`AGENTS.md`](./AGENTS.md).
