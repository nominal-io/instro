# New docs: what changed

`docs/` is now one Sphinx site (#584, stacked on #578): the guides at the root, generated examples, and the API reference under `/sdk/`. It replaces the Mintlify guides and the separate SDK site. Steps outside the repo (domain, DNS, Mintlify) are in [`DOCS_MIGRATION.md`](./DOCS_MIGRATION.md).

## What changed

- **Build root.** The shared config, extensions, templates and styling moved from `docs/sdk/` up to `docs/`. Logos and favicon are copies, because Mintlify still serves the originals. `docs/guides/` is excluded from the build and untouched apart from link fixes.
- **Guides.** 56 pages converted from MDX to MyST: cards to sphinx-design grids, callouts to admonitions, plus tabs, steps and tooltips. Links to API pages are cross-references that the build checks. Guide URLs are unchanged (`/psu/`, `/library/publishers/`).
- **Simplifications.**
  - Each driver is one page plus one image. The `driver-cards` directive builds each category's card grid from their metadata, replacing about 120 snippet, hidden-page and aggregator files.
  - Example pages are generated at build time, so the 63 committed example pages, their generator, the drift check and its CI job are gone.
  - Navigation lives in `index.md` instead of `docs.json`.
  - Guides and API link with plain Sphinx references, so `check_links.py` is retired.
- **Look.** Header tabs (Guides / Examples / SDK) switch which part the sidebar shows, as Mintlify's tabs did. The header keeps Forum, a "Get a demo" button and the social icons; Google Analytics keeps the same ID.
- **Build and CI.** `just build-docs` (strict) and `just serve-docs` (live preview) build the whole site. The PR job `build-docs` uploads a `docs-site` preview artifact; the deploy publishes the combined site.
- **Contributor docs.** Short `docs/AGENTS.md`, `CONTRIBUTING.md` and `README.md` replace the five per-site files. Root `AGENTS.md` and `CONTRIBUTING.md`, both `add-instrument-driver` skill copies, and the review prompt are updated.
- **Live-site safety.** The live Mintlify pages and the README linked to the old SDK paths; 119 of those links now point to `/sdk/`, and all resolve. The live guides stay intact after merge until DNS moves.

## Verified

- `just build-docs` (strict, zero warnings), `just check-python`, `just test-python`, `uv lock --check`.
- All 71 Mintlify pages exist in the new site, including all 26 driver pages.
- The PSU page compared visually against the live Mintlify site.

## Worth knowing

- **Sequencing.** #578 merges first. After this merges, the combined site is live at `nominal-io.github.io/instro/` while Mintlify keeps `instro.nominal.io` until the DNS switch.
- **Content freeze.** Guide edits go into the Sphinx pages. The Mintlify copy in `docs/guides/` is frozen; don't edit it.
- **Dropped with Mintlify.** The "Ask Assistant" box, the MCP server, and the per-group sidebar icons.
- **Preview.** `just serve-docs`, then open http://127.0.0.1:8000.
