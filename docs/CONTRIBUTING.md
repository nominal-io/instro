# Contribute to the instro docs

Thanks for helping. Agents: see [AGENTS.md](./AGENTS.md).

## Editing

- **On GitHub:** use **Edit this page** at the bottom of any page, make the change, and open a pull request.
- **Locally:** from the repo root run `just serve-docs` and open http://127.0.0.1:8000; it rebuilds as you edit. Run `just build-docs` before opening a PR: it fails on any warning, as CI does.

Pages are Markdown (MyST) in `docs/`. The API reference in `docs/sdk/` is generated from docstrings, so fix API text in the code. Don't edit `docs/guides/` (the retired site) or `docs/examples/` (generated from `examples/`).

## Common tasks

**Add a driver to an existing category**

1. Add `docs/<category>/<Driver>.md` and `<Driver>.png`, copying a sibling driver. The category page's card grid picks it up.
2. Add a row to the Vendor Drivers table in `docs/sdk/instruments/<category>.md`, and the class to the hidden list beneath it.
3. Run `just build-docs`.

**Add an example:** put a script with a one-line docstring in `examples/<category>/`. Its page is generated.

**Add an instrument category:** see [AGENTS.md](./AGENTS.md#add-an-instrument-category).

## Writing guidelines

- Call the library **`instro`**; reserve *Nominal* for the platform. Keep HAL class casing (`InstroPSU`).
- Be concise. Use active voice and the imperative: "Configure the check", not "You can configure the check".
- Sentence-case headings, **bold** for UI elements, `code` for files, commands, and identifiers.
- Link classes to the API reference with `` {py:class}`~instro.psu.InstroPSU` ``.
- Document `instro` only; link out for Nominal Core, Nominal Connect, and the dashboard.
