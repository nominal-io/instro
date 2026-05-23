# Nominal Instrumentation docs — agent instructions

## About this project

- Open-source documentation for [Nominal Instrumentation](https://nominal-io.github.io/instrumentation/), Nominal's Python library for test equipment automation.
- Built on [Mintlify](https://mintlify.com); pages are MDX files with YAML frontmatter.
- Configuration lives in `docs.json`.
- Run `mint dev` to preview locally; run `mint broken-links` before opening a PR.
- Reusable text snippets live in `snippets/` (e.g. `snippets/glossary/channel.mdx`).

## Terminology

- Refer to the library as **Nominal Instrumentation** (or `instro`, the package name).
- The instrument HALs are **`InstroPSU`**, **`InstroELoad`**, **`InstroDMM`**, **`InstroDAQ`**, **`I2CInterface`** — keep the casing.
- "Channel" = a named signal for a series of measurements or computed values; the inline glossary tooltip is in `snippets/glossary/channel.mdx`.

## Style preferences

- Use active voice and second person ("you").
- Keep sentences concise — one idea per sentence.
- Use sentence case for headings.
- Bold for UI elements: Click **Settings**.
- Code formatting for file names, commands, paths, identifiers, and code references.

## Content boundaries

- This repo documents Nominal Instrumentation only. Nominal Core, Nominal Connect, and the dashboard are documented elsewhere — link out, don't re-document.
