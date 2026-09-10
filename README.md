# trureturing-mdbook

This repository projects the `Blueprint/`, `Problems/` and `Library/` Markdown content of
[the-omega-institute/trureturing](https://github.com/the-omega-institute/trureturing) into an
mdBook site and publishes it through GitHub Pages. The site is a derived artifact for browsing
and search — it is not the source of mathematical truth. That is always the upstream repository
and its Git history.

A workflow checks the upstream `dev` branch on a nominal 15-minute schedule
(`7,22,37,52 * * * *` UTC). When the upstream push notifier is installed and its dedicated
credential is configured (see activation below), each upstream `dev` push also requests this
repository's existing Pages workflow on `main`. Scheduled checks remain recovery for missed or
failed notifications. Cron alone has exhibited multi-hour delays; neither trigger guarantees
hard real-time publication. GitHub may delay or skip scheduled runs, and queueing, build and
deployment time add to publication latency. A lightweight scheduled probe captures one commit
SHA and compares it with the public deployment's `provenance.json` before installing tools or
fetching history.
Only an identical upstream SHA **and** generator revision skip the build. Missing, malformed or
unreachable provenance attempts a build; failed builds or deployments leave the previous public
provenance in place, so later checks retry. Main pushes and all workflow dispatches always rebuild.

The build fetches exactly that captured commit with full history, publishes only the
regular `Blueprint/**/*.md`, `Problems/*.md` and `Library/**/*.md` blobs from that tree,
and derives the navigation, the home page, an
external open problems page, a first-parent changelog covering the last 30 dates with changes,
and a provenance record. It then
builds with pinned mdBook, mdbook-katex and Pagefind Extended, and deploys only after the source
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
group; production runs are serialized without cancelling an active publication. GitHub can
replace pending runs and does not guarantee their order. Every run captures current upstream
`dev` when its probe executes; notifications do not pass a source SHA. Thus rapid successive or
delayed notifications can coalesce into a current snapshot without replaying old event snapshots.
The captured SHA remains fixed through projection, verification and deployment. Repeated dispatches
may rebuild the same snapshot. Failed notification jobs can be rerun after fixing their cause;
later pushes and scheduled checks also retry publication. A dispatch accepted by GitHub is not
evidence of a successful deployment.

## Upstream push notification activation

The sender is the independent `notify-mdbook` job in upstream
`.github/workflows/ci.yml`, restricted to pushes to that repository's `dev` branch. It has no
checkout or dependency on the admission jobs. It only sends
`POST /repos/the-omega-institute/trureturing-mdbook/actions/workflows/pages.yml/dispatches`
with `{"ref":"main"}`. This repository owns the full publication pipeline and needs no new secret.

Installing the workflow does **not** complete activation. The credential owner must create a
dedicated fine-grained personal access token with resource owner `the-omega-institute`, repository
access limited to **only** `trureturing-mdbook`, and repository **Actions: write** (plus automatic
Metadata: read). No Contents, Pages, Administration or organization permissions are needed. Complete
any required organization approval before use, choose an expiry, and arrange renewal with the owner.
Store that newly issued value as the upstream repository Actions secret **`MDBOOK_DISPATCH_TOKEN`**
in `the-omega-institute/trureturing`, through GitHub's secret entry UI. Do not reuse an interactive
CLI credential or put token values in files, commands, PRs or logs. Upstream's built-in
`GITHUB_TOKEN` cannot dispatch a workflow in another repository. A missing, expired or rejected
credential makes the notifier fail; there is no successful empty-token skip. The job uses
`permissions: {}` for upstream's built-in token; dispatch uses only the dedicated secret.

After the sender is reviewed and merged through upstream's required checks and the secret is
configured, observe a **real upstream `dev` push**. Record its SHA and availability time, the
upstream `notify-mdbook` job's acceptance time, the resulting `workflow_dispatch` run on website
`main`, its captured SHA and deployment completion time. Check the canonical
[`provenance.json`](https://the-omega-institute.github.io/trureturing-mdbook/provenance.json) and
[problem page](https://the-omega-institute.github.io/trureturing-mdbook/open-problems.html) against
that captured source, including the triggering result. A newer captured descendant can include
several pushes. Measure result availability to visible publication, including runner queueing;
manual dispatch, lint and an accepted API request alone do not establish this integration.

## Local build

Requires Python 3.10 or later, Git, mdBook 0.5.4, mdbook-katex 0.10.0 and Pagefind Extended 1.5.2:

```sh
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

The generated root page `open-problems.md` lists every `Problems/<slug>.md` dossier,
its Library citation (DOI or stable HTTPS URL), and any matching resolution marker
in published Blueprint Markdown. All three roots are published byte-for-byte and
included in the existing navigation and changelog. Dossier, Library note and theorem
links are repository-relative Markdown paths that mdBook rewrites to HTML. Source links
use `doi.org` for a DOI or the recorded URL for sources such as OEIS; the snapshot commit
link remains at GitHub for provenance.
The global GitHub toolbar shortcut is disabled so it adds no external navigation.

All three input trees are read with `git ls-tree` and `git cat-file` at the same
captured SHA, including during verification. The verifier independently regenerates
the page from those Git objects, compares its bytes, requires its HTML mapping, and
checks its rendered links. CI runs the Python tests before building the site.

The dossier parser accepts the current closed key set: `slug`, `bibkey`, `doi`,
`triage`, `motivation_gids`, plus optional `url`. Front matter must use UTF-8 without BOM or CR,
single-line scalars (plain, double-quoted or single-quoted), and two-space block lists.
Unsupported YAML syntax, missing or unknown
keys, duplicate keys, path/slug disagreement, and duplicate or out-of-order dossier
slugs fail the build. Each bibkey must select exactly one regular Library note with
the current closed Library key set (also allowing optional `url`), whose citation exactly
matches the dossier's decoded DOI/URL pair, including case. The `doi` key is required but
nullable (bare, `null` or `~`); exactly one non-null DOI or URL is required for each dossier
and its selected note. Non-null DOIs must match `^10\.[0-9]{4,9}/\S+$`; journal DOIs and
arXiv DOIs are accepted. URLs must already be canonical absolute HTTPS URLs with a host and
without credentials. The retired `arxiv_id` key is rejected, including alongside `doi`.
Every Library field except nullable `doi` and `strata_touched` must be a nonempty scalar;
`strata_touched` may be an empty list (bare or `[]`) or a block list of scalars.
Dossier `motivation_gids` must still be a nonempty list of unique formal GIDs. Input documents reject
YAML-forbidden control characters, including NUL, even outside the front matter.
The reader implements a restricted text format, not general YAML: scalar values
must occupy one line without tabs, Unicode line breaks, embedded BOM, leading or
trailing whitespace. Plain scalars cannot contain comments or mapping separators
(including a final colon). Quoted scalars can contain such punctuation; double-quoted
values decode JSON string escapes, and single-quoted contents remain literal, matching the
producer's subset parser. Malformed quotes and decoded noncanonical text fail the build.
Tags, anchors, aliases, nonempty flow collections, block scalars, reserved
leading plain indicators, and nested lists or mappings are unsupported. Punctuation
inside block-context text such as `C#`, `a:b`, and `text [with brackets]` is retained.
Numbers and booleans remain text; unquoted `null` and `~` represent null.
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
upstream at build time. Upstream currently declares no content license, so this repository grants
no rights to that content and does not sublicense it. KaTeX, Pagefind and their generated assets
remain under their own licenses and notices. See [NOTICE.md](NOTICE.md) for the full statement.
