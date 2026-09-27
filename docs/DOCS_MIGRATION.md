# Docs migration: steps outside the repo

The guides (Mintlify, `instro.nominal.io`) and the API reference (GitHub Pages, `nominal-io.github.io/instro`) are now one Sphinx site built from `docs/` (#584). Once merged, it deploys to `https://nominal-io.github.io/instro/`, with the guides at the root and the API reference under `/sdk/`. Mintlify keeps serving `instro.nominal.io` from the frozen `docs/guides/` folder until the steps below move the domain.

These steps need org, repo-admin, DNS, or vendor access, and must happen in this order. Old URLs are not preserved, so there are no redirects to set up. Owners are TBD; fill them in before starting.

## Before cutover (any time)

- [ ] **Verify `nominal.io` for GitHub Pages** (org owner). Org settings → Pages → Add a domain, then add the TXT record GitHub gives you. This stops anyone else claiming `instro.nominal.io` on Pages.
- [ ] **Require the docs check on `main`** (repo admin): add `build-docs` to the branch protection rules.
- [ ] **Record the current DNS record for `instro.nominal.io`** (DNS owner), so it can be restored. Lower its TTL to about 5 minutes a day before cutover.
- [ ] **Google Analytics** (GA owner): the site keeps measurement ID `G-LR7QM29GGQ`. Check that the property's data stream doesn't filter out the GitHub Pages hostname.

## Cutover

1. [ ] **Confirm the merged site works** at `https://nominal-io.github.io/instro/`: a guide page, an example, and an API page under `/sdk/`.
2. [ ] **Set the custom domain** (repo admin): Settings → Pages → Custom domain → `instro.nominal.io`. The deploy is Actions-based, so no `CNAME` file is needed. GitHub then redirects `nominal-io.github.io/instro/*` to the custom domain.
3. [ ] **Point DNS at GitHub** (DNS owner): replace the `instro` record with `CNAME instro → nominal-io.github.io`. Leave `community.instro.nominal.io` (the forum) alone.
4. [ ] **Enforce HTTPS** (repo admin): once GitHub issues the certificate, tick "Enforce HTTPS" on the Pages settings.
5. [ ] **Verify** that the site loads on `https://instro.nominal.io`, search works, and page views appear in GA's real-time report.

**Rollback:** restore the recorded DNS record and remove the custom domain in Settings → Pages. Mintlify serves the old guides again as soon as DNS points back, so keep Mintlify running until the post-cutover steps are done.

## After cutover (wait about two weeks)

- [ ] **Retire Mintlify** (billing owner): remove the custom domain from the Mintlify project, uninstall the Mintlify GitHub app from the repo/org so it stops building on pushes, then delete the project and cancel the subscription.
- [ ] **Clean up the repo** (one PR): delete `docs/guides/` and its entry in `exclude_patterns` in `docs/conf.py`; set `html_baseurl` in `docs/conf.py` to `https://instro.nominal.io/`; point the README's SDK badge at `https://instro.nominal.io/sdk/`; delete this file.
- [ ] **Tell users** on Discord and the forum that the docs' AI assistant and MCP server (both Mintlify-hosted) are gone, and update any pinned links.
- [ ] **Search Console**, if used: verify `instro.nominal.io` and resubmit the sitemap.

PyPI project pages need nothing: their Documentation link (`https://instro.nominal.io`) stays valid and refreshes on the next release.
