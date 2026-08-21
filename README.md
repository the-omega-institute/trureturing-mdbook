# trureturing-mdbook

This repository projects the `Blueprint/` Markdown content of
[the-omega-institute/trureturing](https://github.com/the-omega-institute/trureturing) into an
mdBook site and publishes it through GitHub Pages. The site is a derived artifact for browsing
and search — it is not the source of mathematical truth. That is always the upstream repository
and its Git history.

A daily workflow captures a single commit SHA from the upstream `dev` branch, reads only the
regular `Blueprint/**/*.md` blobs from that tree, and derives the navigation, the home page, a
first-parent changelog covering the last 30 dates with changes, and a provenance record. It then
builds with pinned mdBook, mdbook-katex and Pagefind Extended, and deploys only after the source
set, page mapping, math output, relative links and artifact size all pass their gates.

Nothing derived is committed. The upstream checkout, the projected source tree, `SUMMARY.md`, the
changelog, the search index and the built book are all recomputed on every run and never enter the
Git index.

## Local build

Requires Python 3, Git, mdBook 0.5.4, mdbook-katex 0.10.0 and Pagefind Extended 1.5.2:

```sh
SITE_SRC="$(mktemp -d)"
python3 scripts/build-site.py /path/to/trureturing "$SITE_SRC"
MDBOOK_BOOK__SRC="$SITE_SRC" mdbook build --dest-dir book
pagefind_extended --site book --force-language zh
python3 scripts/verify-site.py /path/to/trureturing "$SITE_SRC" book
```

Pagefind's segmentation language is pinned to `zh` while the mdBook page language stays `en`; the
two are independent knobs. Almost all Blueprint content is English, but a handful of upstream pages
contain Chinese terms, and `zh` segmentation is what makes those terms findable. Measured on the
real corpus, forcing `zh` costs nothing in English recall: `Knaster`, `Hausdorff` and `entropy`
return the same number of results under both settings, while Chinese terms such as `未入账` return
results only under `zh`.

For manual search verification, serve the build with
`python3 -m http.server --directory book 8000`, open the home page, and query one Chinese term that
actually appears upstream (for example `未入账`) and one English term (for example `Knaster`). Both
must return at least one result.

Unit tests need only Python 3 and Git:

```sh
python3 -m unittest discover -s tests -v
```

## License boundary

The MIT License in [LICENSE](LICENSE) covers **only the generator, configuration and workflows
written in this repository**. The Blueprint content shown on the built pages is fetched from
upstream at build time. Upstream currently declares no content license, so this repository grants
no rights to that content and does not sublicense it. KaTeX, Pagefind and their generated assets
remain under their own licenses and notices. See [NOTICE.md](NOTICE.md) for the full statement.
