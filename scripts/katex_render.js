#!/usr/bin/env node
// Render a JSON array of {tex, display} from stdin. The first output line is
// {"version"}; then one JSON line per formula, in order: {"html"} with "lenient" when an
// undefined macro is shown in red, or {"html": null, "failure"}. Streaming keeps the
// book's ~400 MB of KaTeX HTML out of the V8 heap (one array of it ran out of memory).
// KaTeX runs in Node's V8, whose stack is not the 256 KiB QuickJS budget that made
// mdbook-katex give up on deeply nested formulas. A formula KaTeX cannot render even
// with the package macros below, undone Markdown escaping, or (for a paper-local
// macro) its lenient mode is reported as a failure and its slot left null for the
// caller's fallback.
"use strict";

const fs = require("fs");

let katex;
try {
  katex = require("katex");
} catch (error) {
  if (error && error.code === "MODULE_NOT_FOUND") {
    process.stderr.write("katex-render: the pinned katex package is not installed; run `npm ci` in the repository root\n");
    process.exit(2);
  }
  throw error;
}

// Commands from LaTeX packages that upstream quotes use (amsmath, dsfont, physics,
// mathtools-style delimiters, esvect, nicefrac) and that KaTeX does not define.
const MACROS = {
  "\\Tr": "\\operatorname{Tr}",
  "\\tr": "\\operatorname{tr}",
  "\\trace": "\\operatorname{Tr}",
  "\\diag": "\\operatorname{diag}",
  "\\Tilde": "\\tilde",
  "\\wt": "\\widetilde",
  "\\mathds": "\\mathbb",
  "\\mathpzc": "\\mathcal",
  "\\ketbra": "\\lvert #1\\rangle\\langle #2\\rvert",
  "\\dyad": "\\lvert #1\\rangle\\langle #1\\rvert",
  "\\floor": "\\left\\lfloor #1\\right\\rfloor",
  "\\ceil": "\\left\\lceil #1\\right\\rceil",
  "\\norm": "\\left\\lVert #1\\right\\rVert",
  "\\vv": "\\vec{#1}",
  "\\nicefrac": "{}^{#1}\\!/\\!{}_{#2}",
  "\\mbox": "\\text{#1}",
  "\\emph": "\\textit{#1}",
  "\\normalfont": "",
};

function render(tex, display, lenient) {
  // A fresh copy: \gdef in one formula must not leak into the next.
  return katex.renderToString(tex, {
    displayMode: display,
    throwOnError: !lenient,
    trust: false,
    // LaTeX-compatibility notices change nothing in the output; thousands flood the log.
    strict: "ignore",
    output: "html",
    macros: { ...MACROS },
  });
}

// Returns [html, lenient] or throws the error of the formula as written.
function renderItem(item) {
  const display = Boolean(item.display);
  const candidates = [item.tex];
  // Markdown-escaped TeX writes `\\mathcal` and `\\\\` for `\mathcal` and `\\`;
  // math spans are not unescaped, so try the formula with the escaping undone.
  if (/\\\\[A-Za-z\\]/.test(item.tex)) candidates.push(item.tex.replace(/\\\\/g, "\\"));
  let firstError = null;
  for (const tex of candidates) {
    try {
      return [render(tex, display, false), false];
    } catch (error) {
      firstError = firstError || error;
    }
  }
  // A macro defined only in the quoted paper (`\qc`, `\scrT`): KaTeX renders the rest
  // of the formula and shows that command's name in red.
  if (/Undefined control sequence/.test(String(firstError && firstError.message))) {
    for (const tex of candidates) {
      const html = render(tex, display, true);
      if (!html.startsWith('<span class="katex-error"')) return [html, true];
    }
  }
  throw firstError;
}

const items = JSON.parse(fs.readFileSync(0, "utf8"));
// Writes to a pipe are synchronous on Linux, so nothing accumulates here.
process.stdout.write(JSON.stringify({ version: katex.version }) + "\n");
for (const item of items) {
  let line;
  try {
    const [html, lenient] = renderItem(item);
    line = lenient ? { html, lenient: true } : { html };
  } catch (error) {
    line = { html: null, failure: error && error.message ? error.message : String(error) };
  }
  process.stdout.write(JSON.stringify(line) + "\n");
}
