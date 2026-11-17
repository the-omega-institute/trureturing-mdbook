#!/usr/bin/env node
// Check a JSON array of {tex, display} from stdin against the pinned KaTeX package. The
// first output line is {"version"}; then one JSON line per formula, in order: {"tex"} with
// the TeX the browser is to render (and "lenient" when an undefined macro is shown in red),
// or {"tex": null, "failure"}. The browser renders that TeX with the same options
// (scripts/katex_page.js), so a formula that renders here renders there.
// KaTeX runs in Node's V8, whose stack is not the 256 KiB QuickJS budget that made
// mdbook-katex give up on deeply nested formulas. A formula KaTeX cannot render even
// with the package macros, undone Markdown escaping, or (for a paper-local macro) its
// lenient mode is reported as a failure and its slot left null for the caller's fallback.
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
const { katexOptions } = require("./katex_page.js");

function render(tex, display, lenient) {
  return katex.renderToString(tex, katexOptions(display, lenient));
}

// Returns [tex, lenient] for the TeX that renders, or throws the error of the formula
// as written.
function checkItem(item) {
  const display = Boolean(item.display);
  const candidates = [item.tex];
  // Markdown-escaped TeX writes `\\mathcal` and `\\\\` for `\mathcal` and `\\`;
  // math spans are not unescaped, so try the formula with the escaping undone.
  if (/\\\\[A-Za-z\\]/.test(item.tex)) candidates.push(item.tex.replace(/\\\\/g, "\\"));
  let firstError = null;
  for (const tex of candidates) {
    try {
      render(tex, display, false);
      return [tex, false];
    } catch (error) {
      firstError = firstError || error;
    }
  }
  // A macro defined only in the quoted paper (`\qc`, `\scrT`): KaTeX renders the rest
  // of the formula and shows that command's name in red.
  if (/Undefined control sequence/.test(String(firstError && firstError.message))) {
    for (const tex of candidates) {
      const html = render(tex, display, true);
      if (!html.startsWith('<span class="katex-error"')) return [tex, true];
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
    const [tex, lenient] = checkItem(item);
    line = lenient ? { tex, lenient: true } : { tex };
  } catch (error) {
    line = { tex: null, failure: error && error.message ? error.message : String(error) };
  }
  process.stdout.write(JSON.stringify(line) + "\n");
}
