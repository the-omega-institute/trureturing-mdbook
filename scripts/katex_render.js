#!/usr/bin/env node
// Render a JSON array of {tex, display} from stdin to a JSON array of HTML strings.
// KaTeX runs in Node's V8, whose stack is not the 256 KiB QuickJS budget that made
// mdbook-katex give up on deeply nested formulas. A formula KaTeX cannot render (a
// paper-local macro in a verbatim quote, or a character KaTeX has no font metrics
// for) is reported as a failure and its slot left null for the caller's fallback.
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

const items = JSON.parse(fs.readFileSync(0, "utf8"));
const rendered = [];
const failures = [];
for (const [index, item] of items.entries()) {
  try {
    rendered.push(katex.renderToString(item.tex, {
      displayMode: Boolean(item.display),
      throwOnError: true,
      trust: false,
      output: "html",
    }));
  } catch (error) {
    rendered.push(null);
    failures.push({ index, message: error && error.message ? error.message : String(error) });
  }
}
process.stdout.write(JSON.stringify({ version: katex.version, html: rendered, failures }));
