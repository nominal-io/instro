# instro: User Documentation

Prose / conceptual documentation for [`instro`](https://instro.nominal.io), a Python library for scripting and automating test equipment.

This site is built on [Mintlify](https://mintlify.com). It sits alongside `docs/reference/` (mkdocs), which auto-generates the API reference from docstrings. This Mintlify site holds the prose, examples, and getting-started content.

## Local development

Install the [Mintlify CLI](https://www.npmjs.com/package/mint):

```bash
npm i -g mint
```

Run the dev server from this directory (where `docs.json` lives):

```bash
cd docs/guides
mint dev
```

The preview is served at `http://localhost:3000`.

Check links before opening a PR:

```bash
mint broken-links
```

## Contributing

See [CONTRIBUTING.md](./CONTRIBUTING.md). For questions or feedback about the library itself, reach out via the [Nominal Support Portal](https://portal.usepylon.com/nominal).

## Resources

- [instro SDK reference](https://nominal-io.github.io/instro/)
- [Runnable examples](https://instro.nominal.io/instrumentation/examples)
- [Nominal](https://nominal.io)
