# trureturing-mdbook

This repository projects the `Blueprint/`, `Problems/` and `Library/` Markdown content of
[the-omega-institute/trureturing](https://github.com/the-omega-institute/trureturing) into an
mdBook site and publishes it through GitHub Pages. The site is a derived artifact for browsing
and search — it is not the source of mathematical truth. That is always the upstream repository
and its Git history.

A workflow checks the upstream `dev` branch on a nominal 15-minute schedule
(`7,22,37,52 * * * *` UTC). GitHub may delay or skip scheduled runs, and build and deployment
time adds to publication latency. A lightweight probe captures one commit SHA and compares it
with the public deployment's `provenance.json` before installing tools or fetching history.
Only an identical upstream SHA **and** generator revision skip the build. Missing, malformed or
unreachable provenance attempts a build; failed builds or deployments leave the previous public
provenance in place, so later checks retry. Main pushes and manual runs always rebuild.

The build fetches exactly that captured commit with full history, publishes only the
regular `Blueprint/**/*.md`, `Problems/*.md` and `Library/**/*.md` blobs from that tree,
and derives the navigation (a folded menu without section numbers, where a directory
whose only content is one subdirectory shares the entry of that subdirectory, so
`Blueprint / D5` is one entry), the home page, an
external open problems page, a first-parent changelog covering the last 30 dates with changes,
and a provenance record. It then
builds with pinned mdBook, KaTeX under Node and Pagefind Extended, and deploys only after the source
set, page mapping, math output, relative links and artifact size all pass their gates.

Nothing derived is committed. The upstream checkout, the projected source tree, `SUMMARY.md`, the
changelog, the search index and the built book are all recomputed on every run and never enter the
Git index.

Provenance includes `upstream_sha`, `file_count`, `tool_version`, `built_at` and
`generator_revision`. The generator revision is a SHA-256 digest of the named build inputs in
`scripts/site_freshness.py` (configuration, workflow and Python scripts), so even local edits are
identified accurately. One UTC build timestamp is shared by provenance, the home page and the
problem page. It describes when that snapshot build began; it is not a last-checked timestamp.
Checks that skip an identical deployment leave the snapshot timestamp intact.

For recovery or branch validation, dispatch **Build and deploy Pages** on the desired ref.
A branch run builds, verifies and uploads the Pages artifact for inspection. Only a successful
build with an uploaded artifact on `refs/heads/main` can deploy. Each ref has its own concurrency
group; production runs are serialized. No upstream dispatcher or additional secret is required.

## Local build

Requires Python 3.10 or later, Git, Node 18 or later, mdBook 0.5.4 and Pagefind Extended 1.5.2:

```sh
npm ci --ignore-scripts --no-audit --no-fund
SITE_SRC="$(mktemp -d)"
python3 scripts/build-site.py /path/to/trureturing "$SITE_SRC"
MDBOOK_BOOK__SRC="$SITE_SRC" mdbook build --dest-dir book
python3 scripts/render_source_links.py /path/to/trureturing "$SITE_SRC" book
pagefind_extended --site book --force-language zh
python3 scripts/verify-site.py /path/to/trureturing "$SITE_SRC" book
```

To reproduce a captured snapshot even if upstream HEAD has moved, add
`--upstream-sha <full-commit-SHA>` to `build-site.py`. The checkout must contain that commit's
complete history, including frozen-state additions.

Formulas are rendered by `scripts/render_katex.py`, an mdBook preprocessor that finds every
`$…$` and `$$…$$` span with the same scanner the verifier counts with (`scripts/math_scan.py`)
and renders them all in one Node process with the KaTeX package pinned in `package-lock.json`.
It replaced mdbook-katex, whose embedded QuickJS runtime has a 256 KiB stack: a theorem page
with a few hundred nested `\left…\right` groups made it keep the source text, which the
release gate then refused. A formula KaTeX rejects fails the build and names the page.

Before rendering, the prose preprocessor escapes link-shaped mathematical notation such as
`mu_H[d](univ)` and standalone powered coefficient expressions such as
`[X^(m-1)](1-A)^m` or `[X^(m−1)](1−F)^m`, with ASCII or Unicode minus signs.
The latter requires a coefficient selector, an algebraic factor and an
immediately following power; URL and file-path characters do not qualify. The transformation
runs in memory, leaving copied upstream Markdown intact, and preserves code and math regions.
The release gate still rejects broken relative links.

After mdBook renders, `render_source_links.py` turns relative anchors to existing,
unpublished upstream files (for example `.lean` source) into GitHub blob permalinks at
the SHA in the source and book provenance. This includes mdBook's rebased links in
`print.html`. Only exact paths to regular files in that Git tree qualify; missing paths
are never inferred from alternate extensions. Published-page navigation and local resources
stay relative. Query strings and fragments are retained. Only rendered anchor attributes
change; copied upstream Markdown and all other HTML bytes stay intact. This step runs before
Pagefind and the unchanged release gate, which still rejects missing relative resources.

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

The generated root page `open-problems.md` lists every `Problems/<slug>.md` dossier — solved
ones newest freeze first and by slug within a day, unsolved ones by slug — with
any matching resolution marker in published Blueprint Markdown, a citation of the source
and the claim, both copied from the reading note's front matter. The citation has the
upstream acknowledgement shape — `authors (year). *title*. DOI: […](https://doi.org/…).
URL: <…>.` — with the DOI first and the URL second when the note holds both; the claim is
the note's one-line transcription of the source statement, not a checked quotation. Field
text is Markdown-escaped verbatim, so a `$…$` in a claim renders as text, not as math.
All three roots are published byte-for-byte and
included in the existing navigation and changelog. Dossier, Library note and theorem
links are repository-relative Markdown paths that mdBook rewrites to HTML. The DOI link
uses `doi.org` and the URL is the recorded one for sources such as OEIS; the snapshot commit
link remains at GitHub for provenance.
The global GitHub toolbar shortcut is disabled so it adds no external navigation.

All three input trees are read with `git ls-tree` and `git cat-file` at the same
captured SHA, including during verification. The verifier independently regenerates
the page from those Git objects, compares its bytes, requires its HTML mapping, and
checks its rendered links. CI runs the Python tests before building the site.

The dossier parser accepts the current closed key set: `slug`, `bibkey`, `doi`,
`triage`, `motivation_gids`, plus optional `url`. Front matter must use UTF-8 without BOM or CR,
single-line scalars, and two-space block lists. A scalar is read the way the upstream
`YamlSubsetParser` reads it, so that nothing upstream accepts fails here: the remainder of the
line is trimmed; `null`, `~` and a blank remainder are null; `"…"` is JSON, falling back to the
raw inner text when it is not valid JSON; `'…'` is the inner text verbatim; anything else,
including `: `, `#`, quotes and flow characters, is the whole text verbatim. Only block-scalar
markers (`|`, `>` and their chomping forms), values that are not one canonical non-empty line
after decoding, and forbidden characters fail. Unsupported YAML syntax, missing or unknown
keys, duplicate keys, path/slug disagreement, and duplicate or out-of-order dossier
slugs fail the build. Each bibkey must select exactly one regular Library note with
the current closed Library key set (also allowing optional `url`). The note holds the source
identity: every DOI or URL the dossier gives must equal the note's value of the same kind,
byte for byte, while the note may hold a locator the dossier omits. The `doi` key is required
but nullable (bare, `null` or `~`); each dossier and each note needs at least one of a non-null
DOI and a URL, and may carry both. Non-null DOIs must match `^10\.[0-9]{4,9}/\S+$`; journal
DOIs and arXiv DOIs are accepted. URLs must already be canonical absolute HTTPS URLs with a
host and without credentials. The retired `arxiv_id` key is rejected, including alongside `doi`.
Every Library field except nullable `doi` and `strata_touched` must be a nonempty scalar;
`strata_touched` may be an empty list (bare or `[]`) or a block list of scalars.
Dossier `motivation_gids` must still be a nonempty list of unique formal GIDs. Input documents reject
YAML-forbidden control characters, including NUL, even outside the front matter.
The reader implements the producer's line format, not general YAML: one `key: value`
per line, scalar values read as described above, and two-space block lists. Block
scalars, nested lists or mappings, and decoded values that are not one canonical
non-empty line are unsupported. Numbers and booleans remain text; unquoted `null` and
`~` represent null.
An H1 dossier title is optional in the producer contract. The page uses the dossier slug
when absent, matching navigation; empty or multiple H1 titles are rejected.

Resolution parsing recognizes standalone `scribe-open-problem-resolution-v1` HTML
comments with exactly `problem_slug`, `declaration_gid` and `resolution_kind`
(`proved` or `refuted`). The two-key shape is rejected; there is no compatibility
reader. `declaration_gid` must be a canonical formal GID with a declaration selector,
such as `D5/S1/Words/Sumfree/GreedyThreeSumfreeTwoParameter.conjecture17`.
Any occurrence of the reserved marker prefix must have valid syntax, version, and
payload, even in a Markdown code example. Slugs must exist in the dossier set, be
globally unique among markers. Markers may follow the producer's document/theorem order;
the display remains ordered by dossier slug within each section. Violations fail the build.

These comments are records, not validated typed claims: ordinary narrative can emit
identical bytes. The page does not consume a Describe report, establish repository
validity, or validate Lean proofs. The theorem link displays only the declaration name
(for example, `conjecture17`); its destination is unchanged, still
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
The workflow's non-shallow `fetch --filter=blob:none` and sparse checkout retain this history
even with only `Blueprint Problems Library` checked out; `Golden/` need not be
materialized. The same publication predicate selects source blobs, changelog paths
and the files whose bytes the verifier checks.

## License boundary

The MIT License in [LICENSE](LICENSE) covers **only the generator, configuration and workflows
written in this repository**. The upstream content shown on the built pages is fetched from
upstream at build time. Upstream currently provides an
[Apache-2.0 LICENSE](https://github.com/the-omega-institute/trureturing/blob/dev/LICENSE).
For a built snapshot, consult the license and any content-specific notices at the exact upstream
commit recorded in `provenance.json`. This repository grants no rights to that content and does
not sublicense it. KaTeX, Pagefind and their generated assets remain under their own licenses
and notices. See [NOTICE.md](NOTICE.md) for the full statement.
