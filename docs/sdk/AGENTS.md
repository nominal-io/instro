# instro SDK docs: agent instructions

Scope: `docs/sdk/`, the mkdocs/mkdocstrings API reference site. Docstring content rules: root [`AGENTS.md`](../../AGENTS.md#doc-strings). Guides site: [`docs/guides/AGENTS.md`](../guides/AGENTS.md).

## About this site

- mkdocs (Material theme) + mkdocstrings. `docs_dir: src`. Preview: `cd docs/sdk; uv run --with mkdocs mkdocs serve`. Build/CI check: `just build-docs`.
- Page content is mostly generated from docstrings via `::: dotted.path` directives — `.md` files provide structure, not prose.
- Nav is `mkdocs.yml`'s `nav:` list, not folder structure. A `src/` page missing from it triggers an `omitted_files: warn` and isn't reachable.

## Site structure

| Path | Contents |
|---|---|
| `mkdocs.yml` | Nav, theme, `pymdownx.snippets` transclusion paths, mkdocstrings handler options. |
| `src/index.md` | Hand-written landing page. |
| `src/reference/` | One page per library module (`instrument.md`, `types.md`, `exceptions.md`, `publishers.md`, `discover.md`, `transports.md`) — each just a `:::` directive. |
| `src/instruments/` | One page per category, plus `index.md` (category table + transcluded README table). |
| `src/protocols/` | `modbus.md`, `ethernetip.md`, `index.md` — mostly hand-written, unlike instrument pages. |
| `src/changelog.md` | Passthrough of generated `CHANGELOG.md`. Never hand-edit. |
