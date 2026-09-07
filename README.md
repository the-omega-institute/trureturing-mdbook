# trureturing-mdbook

This repository projects the `Blueprint/`, `Problems/` and `Library/` Markdown content of
[the-omega-institute/trureturing](https://github.com/the-omega-institute/trureturing) into an
mdBook site and publishes it through GitHub Pages. The site is a derived artifact for browsing
and search — it is not the source of mathematical truth. That is always the upstream repository
and its Git history.

A daily workflow captures a single commit SHA from the upstream `dev` branch, publishes only the
regular `Blueprint/**/*.md`, `Problems/*.md` and `Library/**/*.md` blobs from that tree,
and derives the navigation, the home page, an
external open problems page, a first-parent changelog covering the last 30 dates with changes,
and a provenance record. It then
builds with pinned mdBook, mdbook-katex and Pagefind Extended, and deploys only after the source
set, page mapping, math output, relative links and artifact size all pass their gates.

Nothing derived is committed. The upstream checkout, the projected source tree, `SUMMARY.md`, the
changelog, the search index and the built book are all recomputed on every run and never enter the
Git index.

## Local build

Requires Python 3.10 or later, Git, mdBook 0.5.4, mdbook-katex 0.10.0 and Pagefind Extended 1.5.2:

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

Unit tests need only Python 3.10 or later and Git:

```sh
python3 -m unittest discover -s tests -v
```

## External open problems

The generated root page `open-problems.md` lists every `Problems/<slug>.md` dossier,
its research triage, its Library citation and DOI, and any matching resolution marker
in published Blueprint Markdown. All three roots are published byte-for-byte and
included in the existing navigation and changelog. Dossier, Library note and theorem
links are repository-relative Markdown paths that mdBook rewrites to HTML. DOI links
remain at `doi.org`; the snapshot commit link remains at GitHub for provenance.
The global GitHub toolbar shortcut is disabled so it adds no external navigation.

All three input trees are read with `git ls-tree` and `git cat-file` at the same
captured SHA, including during verification. The verifier independently regenerates
the page from those Git objects, compares its bytes, requires its HTML mapping, and
checks its rendered links. CI runs the Python tests before building the site.

The dossier parser accepts the current closed key set: `slug`, `bibkey`, `doi`,
`triage`, `motivation_gids`. Front matter must use UTF-8 without BOM or CR, plain
scalar lines, and two-space block lists. Unsupported YAML syntax, missing or unknown
keys, duplicate keys, path/slug disagreement, and duplicate or out-of-order dossier
slugs fail the build. Each bibkey must select exactly one regular Library note with
the current closed Library key set, whose DOI exactly matches the dossier's DOI,
including case. Both DOI values must match `^10\.[0-9]{4,9}/\S+$`; journal DOIs and
arXiv DOIs are accepted. The retired `arxiv_id` key is rejected, including alongside `doi`.
Every Library field except `strata_touched` must be a nonempty scalar;
`strata_touched` must be a nonempty block list of scalars. Input documents reject
YAML-forbidden control characters, including NUL, even outside the front matter.
The reader implements a restricted text format, not general YAML: scalar values
must occupy one line without tabs, Unicode line breaks, embedded BOM, leading or
trailing whitespace, comments, or mapping separators (including a final colon).
Quoted values, tags, anchors, aliases, flow collections, block scalars, reserved
leading indicators, and nested lists or mappings are unsupported. Punctuation
inside block-context text such as `C#`, `a:b`, and `text [with brackets]` is retained.
Numbers, booleans, and null spellings are literal strings with no implicit typing.

Resolution parsing recognizes standalone `scribe-open-problem-resolution-v1` HTML
comments with exactly `problem_slug`, `declaration_gid` and `resolution_kind`
(`proved` or `refuted`). The two-key shape is rejected; there is no compatibility
reader. `declaration_gid` must be a canonical formal GID with a declaration selector,
such as `D5/S1/Words/Sumfree/GreedyThreeSumfreeTwoParameter.conjecture17`.
Any occurrence of the reserved marker prefix must have valid syntax, version, and
payload, even in a Markdown code example. Slugs must exist in the dossier set, be
globally unique among markers, and increase lexically within each Blueprint page;
ordering across different Blueprint pages is immaterial. Violations fail the build.

These comments are records, not validated typed claims: ordinary narrative can emit
identical bytes. The page does not consume a Describe report, establish repository
validity, or validate Lean proofs. The theorem GID is displayed verbatim and linked
using the marker's containing Blueprint path, with its source line. Upstream owns
the checks that a marker resolves uniquely to a currently frozen, theorem-like declaration.
No matching marker means this repository has no recorded resolution binding
in that Markdown snapshot; it says nothing about whether the problem is still open
in the world or was resolved externally.

"Frozen in repository" is the committer date (`YYYY-MM-DD`) of the first commit
adding the module's frozen state file, reachable from the captured snapshot. It is
neither a world-resolution date nor the date the binding was recorded. The module
path comes from the containing Blueprint page: `Blueprint/D5/X/Y.md` maps to
`Golden/Frozen/state/D5/X/Y.lean.json`. This follows the segment-preserving Lean,
Scribe and Markdown address invariant in upstream's
[repository specification, sections 2.3 and A2](https://github.com/the-omega-institute/trureturing/blob/5c1f71b3b34d4946698510e141193aa32d0b8a5d/docs/develop/spec/golden-ledger-repo-spec.md).
No path is inferred from the declaration GID. The lookup is:

```sh
git log --diff-filter=A --format=%cs --reverse --no-renames "$SHA" -- \
  ':(literal)Golden/Frozen/state/D5/X/Y.lean.json'
```

The first output line supplies the date, including if the file was later deleted
and re-added. Missing history, Git failures and malformed dates fail the build.
The workflow's non-shallow `--filter=blob:none --sparse` clone retains this history
even with only `Blueprint Problems Library` checked out; `Golden/` need not be
materialized. The same publication predicate selects source blobs, changelog paths
and the files whose bytes the verifier checks.

## License boundary

The MIT License in [LICENSE](LICENSE) covers **only the generator, configuration and workflows
written in this repository**. The upstream content shown on the built pages is fetched from
upstream at build time. Upstream currently declares no content license, so this repository grants
no rights to that content and does not sublicense it. KaTeX, Pagefind and their generated assets
remain under their own licenses and notices. See [NOTICE.md](NOTICE.md) for the full statement.
