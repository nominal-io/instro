# instro docs: agent instructions

Scope: `docs/`, one Sphinx site holding the guides (this folder) and the API reference (`sdk/`), deployed from `main` to GitHub Pages. Docstring rules: root [`AGENTS.md`](../AGENTS.md#doc-string-style). `docs/guides/` is the retired Mintlify site, excluded from the build and frozen until the domain moves ([`DOCS_MIGRATION.md`](./DOCS_MIGRATION.md)); never edit it.

## Build

- `just build-docs`: clean build into `_build/dirhtml`, warnings are errors. PR CI (`docs-check.yml`) runs it and uploads the site as a `docs-site` artifact; `deploy-docs.yml` publishes on merge.
- `just serve-docs`: live preview on http://127.0.0.1:8000; rebuilds on page, example, and docstring edits.
- The deploy publishes `_build/dirhtml` as is, at the root of `instro.nominal.io`. The same build also goes to the Nominal developer docs hub at `/python/instro/`, so keep every link relative (the build does): the docs must work under any prefix.
- Toolchain: the `docs` dependency group (Python >=3.12).
- URLs are folder-style (`/psu/`), so a finished build needs a server to browse: `python -m http.server -d docs/_build/dirhtml`.
- Sidebar changes only reach unchanged pages on a clean build (`-E`, which `build-docs` does).

## Structure

| Path | Contents |
|---|---|
| `conf.py`, `_ext/`, `_templates/`, `_static/` | Shared config, extensions, templates, CSS/JS, logos. |
| `_extra/404.html` | Copied to the site root. Sends links from when the docs were under `/python/` to their new paths; otherwise a not-found page. |
| `index.md` | Home page, and every sidebar group: one hidden `toctree` per caption, for all three sections. |
| `quickstart.md`, `installation.md`, `cli.md`, `using-instro.md`, `instruments.md` | Overview pages. |
| `<category>.md` | One guide per category (`psu`, `dmm`, `scope`, `daq`, `awg`, `eload`, `flowcontroller`). |
| `<category>/<Driver>.md` + `<Driver>.png` | One page per vendor driver, with its card in front matter. Protocol drivers live in `library/protocols/<protocol>/`. |
| `library/` | Concepts and reference: publishers, config files, custom instruments (all "Driver Development" sections), contrib, protocols, transports. |
| `images/` | Shared images. |
| `examples/` | Generated at build time by `_ext/examples.py` (gitignored). Never edit. |
| `sdk/` | API reference: category pages, library modules, protocols, changelog. |

- **Sections:** the header tabs (Guides, Examples, SDK) are sections; `nominal_sphinx_theme` trims the sidebar to the current one by the tab's folder (`sdk/`, `examples/`, else guides), from `nav_links` in `conf.py`.
- **Theme:** styling comes from `nominal_sphinx_theme` (nominal-io/pub-docs, `theme/`); change it there. Keep `_static/custom.css` to instro-only styles.
- **Hidden pages:** pages outside the sidebar set `orphan: true` in front matter. Driver pages and protocol sub-pages are orphans.
- **Page head:** `# Title`, then an optional `{.lead}` paragraph (grey subtitle), with `myst: html_meta: description:` in front matter for search engines.

## Guides pages (MyST Markdown)

- Link pages as `/path.md` or `/path.md#anchor`; link API objects with `` {py:class}`~instro.psu.InstroPSU` `` (checked at build time), never by URL.
- Components: `:::{note}` / `tip` / `warning` admonitions; `::::{grid}` + `:::{grid-item-card}` cards (`{octicon}` icons); `::::{tab-set}` + `:::{tab-item}` tabs; `::::{container} steps` with `:::{container} step` numbered steps; `` {abbr}`channel (explanation)` `` tooltips. An outer directive needs more colons than the ones it contains.
- `:::{driver-cards} <folder>` renders the card grid of every driver page in `<folder>/`, sorted by card title.

## API pages (`sdk/`)

- Each `sdk/instruments/<category>.md`: `# Title` + description; `## Instrument and Abstract Driver` (autosummary of `Instro<Category>`, `<Category>DriverBase`); `## Vendor Drivers` (hand-written Vendor / Model / Description table, Description as `` {pysummary}`<path>` ``); `## Simulated Driver` if any; `## Types & Configuration`; `---` then the Exceptions notice.
- Autodoc emits reST, so API directives go in `{eval-rst}` blocks. Autosummary generates a page per table entry into gitignored `generated/` folders; `_templates/autosummary/class.rst` puts data types (pydantic, dataclass, enum) on one page and gives other classes a page per member.
- `conf.py`: vendor packages load from `packages/` source with native SDKs in `autodoc_mock_imports` (a new vendor package needs both); `autosummary_context["extra_methods"]` opts private methods in; a hook resolves re-exported names to their documented original, so document each object once, where defined.

## Tasks

### Add a vendor driver

1. `docs/<category>/<Driver>.md` and `<Driver>.png`. Copy a sibling: front matter `orphan: true`, `card: <Vendor Model>`, `image: <Driver>.png`; `# <Driver>`, a `{.lead}` line, the image, a `## Creating an Instro<Category> with <Driver>` example. The category's `driver-cards` grid picks it up.
2. In `sdk/instruments/<category>.md`, add a Vendor Drivers table row and the class path to the hidden `.. only:: autosummary_stubs` list beneath it (the table is hand-written, so this block is what generates the class page).
3. `just build-docs`.

### Add an instrument category

1. `docs/<category>.md` in the shape of the existing guides (intro, minimal `## Creating an Instro<Category>` example, `## Supported Vendors` with `driver-cards`, example link, `## Details`), plus its driver pages.
2. Add it to the Instruments toctree in `index.md`, and a heading plus `driver-cards` block to `instruments.md`.
3. Add a section to `library/custom-instruments.md`, and to `library/config-files.md` if it accepts `config=`.
4. Examples: add scripts under `examples/<category>/`; add a title and icon to `CATEGORIES` in `_ext/examples.py`.
5. API: `sdk/instruments/<category>.md` in the layout above, added to the SDK Instruments toctree in `index.md`.

## Terminology and style

- Call the library **`instro`**; reserve *Nominal* for the platform (Nominal Core, Nominal Connect, the Nominal publishers). Keep HAL casing: `InstroScope`, `InstroPSU`, `InstroDMM`, `InstroDAQ`, `InstroAWG`, `InstroELoad`, `InstroFlowController`, `I2CInterface`.
- A *channel* is a named signal for a series of measurements or computed values.
- Concise, one idea per sentence; active voice and the imperative ("Configure the check"). No em or en dashes. Nominal in the third person, never *we*. Sentence-case headings, **bold** UI elements, `code` for files, commands, and identifiers. Lowercase data primitives (*channel*, *source*).
- Document `instro` only; link out for Nominal Core, Nominal Connect, and the dashboard.
