# instro docs: agent instructions

## About this project

- Open-source documentation for [`instro`](https://instro.nominal.io), a Python library for test equipment automation.
- Built on [Mintlify](https://mintlify.com). Pages are MDX files with YAML frontmatter.
- Configuration lives in `docs.json`.
- Run `mint dev` to preview locally. Run `mint broken-links` before opening a PR.
- Reusable text snippets live in `snippets/` (e.g. `snippets/glossary/channel.mdx`).

## Terminology

- Refer to the library as **`instro`** (the package name). Reserve *Nominal* for the platform it integrates with (Nominal Core, Nominal Connect, the Nominal publishers).
- The instrument HALs are **`InstroPSU`**, **`InstroELoad`**, **`InstroDMM`**, **`InstroDAQ`**, **`I2CInterface`**: keep the casing.
- A *channel* is a named signal for a series of measurements or computed values. The inline glossary tooltip is in `snippets/glossary/channel.mdx`.

## Style preferences

Follow Nominal's writing style guide. The rules that bite most often here:

- Active voice. Prefer the imperative over repeated *you* (*Configure the check*, not *You can configure the check*).
- No em-dashes or en-dashes. Use a period, colon, semicolon, comma, or parentheses.
- Refer to Nominal in the third person, never *we* or *our platform*. Reserve *we* for cases where Nominal is the actor (*Nominal recommends*).
- Lowercase data primitives (*channel*, *source*). Capitalize product names (*Nominal*, *Nominal Connect*, *Nominal Core*). Instrument HAL classes keep their code casing (`InstroPSU`).
- Name the mechanism before the benefit.
- Keep sentences concise: one idea per sentence.
- Use sentence case for headings.
- Bold for UI elements: Click **Settings**.
- Code formatting for file names, commands, paths, identifiers, and code references.

## Content boundaries

- This repo documents `instro` only. Nominal Core, Nominal Connect, and the dashboard are documented elsewhere: link out, don't re-document.
