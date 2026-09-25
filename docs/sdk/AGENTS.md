# instro SDK docs: agent instructions

Scope: `docs/sdk/`, the Sphinx API reference site published at https://nominal-io.github.io/instro/. Docstring content rules: root [`AGENTS.md`](../../AGENTS.md#doc-string-style). Guides site: [`docs/guides/AGENTS.md`](../guides/AGENTS.md).

## About this site

- Sphinx + [Shibuya](https://shibuya.lepture.com) theme, styled toward the Mintlify guides site. Pages are MyST Markdown; API content comes from docstrings through `sphinx.ext.autodoc` + `sphinx.ext.autosummary`, parsed as Google style by `sphinx.ext.napoleon`.
- Toolchain is the `docs` dependency group (Python >=3.12). `just build-docs` does a clean build into `_build/dirhtml` with warnings as errors, which is what PR CI and the deploy run. `just serve-docs` live-previews on http://127.0.0.1:8000 and rebuilds on docstring changes. `just check-sdk-links` (after a build) fails on guides/README links to SDK pages or anchors that don't exist.
- Built with the `dirhtml` builder, so URLs are folder-style (`/instruments/psu/`). Guides pages link here by absolute URL; don't rename pages or classes without updating those links (`just check-sdk-links` catches it).
- Navigation is the hidden `toctree` blocks at the bottom of `index.md`, one per sidebar section, not folder structure. A page in no toctree is unreachable and warns.
- Sidebar or toctree changes only show on unchanged pages after a clean build (`-E`, which `just build-docs` does).

## How API pages are generated

- Category and library pages list objects in `autosummary` tables inside `{eval-rst}` blocks. Autodoc emits reST, so API directives must be in `{eval-rst}`, never MyST fences; autosummary also finds its entries by scanning source text for `.. autosummary::`.
- Each table entry gets a generated page under `<page dir>/generated/` (gitignored, rebuilt every build). `_templates/autosummary/class.rst` picks the layout: pydantic models, dataclasses and enums get one page with fields inline; other classes get Attributes/Methods tables with a page per member.
- `autosummary_context["extra_methods"]` in `conf.py` opts private methods back into a class page (`Instrument._package_command`, `_package_measurement`).
- Re-exported names (`instro.dmm.config.TimingConfig`) resolve to the documented original via a hook in `conf.py`; document each object once, where it's defined.
- Vendor packages (`packages/instro-daq-*`, `instro-i2c-aardvark`) load from source via `sys.path` in `conf.py`, with their native SDKs in `autodoc_mock_imports`. A new vendor package needs both.
- Docstrings are reST: ``` ``code`` ``` for literals; single backticks (`` `Measurement` ``) link to the named object via `default_role = "py:obj"`, or `` :class:`~instro.lib.types.Measurement` `` for an explicit path.

## Site structure

| Path | Contents |
|---|---|
| `conf.py` | Extensions, autodoc/napoleon/pydantic options, mocks, theme options, and the re-export and pydantic-docstring hooks. |
| `index.md` | Landing page and the sidebar toctrees. |
| `instruments/` | `index.md` (category table + README "Supported devices" table via MyST `{include}`), then one page per category. |
| `library/` | One page per library module (`instrument`, `types`, `config`, `exceptions`, `publishers`, `discover`, `transports`). |
| `protocols/` | `modbus.md`, `ethernetip.md` (mostly hand-written), `index.md`. |
| `changelog.md` | `{include}` of the generated `CHANGELOG.md`. Never hand-edit. |
| `_templates/autosummary/` | Generated-page templates (`class.rst`, `method.rst`, `function.rst`, `exception.rst`, `base.rst`). |
| `_ext/pysummary.py` | `{pysummary}` role: an object's docstring summary, for hand-laid tables. |
| `_static/` | `custom.css` (Mintlify styling), `external-links.js` (header links open in a new tab). Logos and favicon come from `docs/guides/`. |
| `check_links.py` | The `just check-sdk-links` checker. |

## Category page layout

Every `instruments/<category>.md` follows the same order:

1. `# Title` and a one-line description.
2. `## Instrument and Abstract Driver`: autosummary of `Instro<Category>` and `<Category>DriverBase`.
3. `## Vendor Drivers`: a hand-written Vendor / Model / Description table. Model links to the class (`` {py:class}`9115 <instro.psu.drivers.bk_9115.BK9115>` ``); Description is `` {pysummary}`<same path>` `` so it stays sourced from the docstring.
4. `## Simulated Driver`, when the category has one: autosummary of the simulated driver.
5. `## Types & Configuration` and any category-specific sections (DAQ's `## Scaling`).
6. A `---` rule, then the Exceptions notice.

## Adding a vendor driver

Two edits in `instruments/<category>.md`, both under `## Vendor Drivers`:

1. A row in the Vendor / Model / Description table.
2. The class's dotted path in the hidden `.. only:: autosummary_stubs` block below it. The table is hand-written, so autosummary can't see the class; this never-rendered block is what generates its page and sidebar entry.

Link to the new class page from the guides driver page as `https://nominal-io.github.io/instro/instruments/generated/<dotted.path>/`, then run `just build-docs && just check-sdk-links`.

## Adding a category

Create `instruments/<category>.md` in the layout above and add it to the Instruments toctree in `index.md`, then add its row to `instruments/index.md`.
