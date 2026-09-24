# instro SDK reference (Sphinx trial)

Trial port of `docs/sdk` (mkdocs) to Sphinx + Shibuya for #578. Not wired into CI.

```bash
# one-shot build
uv run --frozen --with-requirements docs/sdk_sphinx/requirements.txt \
  sphinx-build -j auto -b html docs/sdk_sphinx docs/sdk_sphinx/_build/html

# live preview on http://127.0.0.1:8000
uv run --frozen --with-requirements docs/sdk_sphinx/requirements.txt --with sphinx-autobuild \
  sphinx-autobuild docs/sdk_sphinx docs/sdk_sphinx/_build/html --watch instro
```
