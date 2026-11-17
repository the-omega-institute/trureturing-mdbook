const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const path = require("node:path");
const { test } = require("node:test");
const vm = require("node:vm");
const katex = require("katex");
const scriptPath = path.join(__dirname, "../scripts/katex_page.js");
const { MACROS, katexOptions, renderPage } = require(scriptPath);

// A source element as render_katex.py writes it, after the browser decoded its text.
function element(tex, { display = false, lenient = false, kind = "katex-source" } = {}) {
  const attributes = { "data-display": display ? "true" : null, "data-lenient": lenient ? "true" : null };
  return { kind, textContent: tex, html: null, getAttribute: name => attributes[name] ?? null };
}

function page(...elements) {
  return { querySelectorAll(selector) {
    assert.equal(selector, "span.katex-source");
    return elements.filter(item => item.kind === "katex-source");
  } };
}

// katex.render writes into the element; record what renderToString would produce.
const recorder = { render(tex, target, options) { target.html = katex.renderToString(tex, options); } };

test("renders each source element in display or inline mode with the package macros", () => {
  const inline = element("\\Tr(\\rho) + \\norm{v}");
  const display = element("\\sum_{i=1}^n i", { display: true });
  const fallback = element("\\frac{x}{", { kind: "katex-fallback" });
  assert.equal(renderPage(page(inline, display, fallback), recorder), 2);
  assert.match(inline.html, /^<span class="katex">/);
  assert.match(inline.html, /Tr/);
  assert.match(display.html, /^<span class="katex-display">/);
  assert.equal(fallback.html, null);
});

test("lenient elements show an undefined macro in red; strict ones keep their source", () => {
  const lenient = element("\\qc(x)", { lenient: true });
  const strict = element("\\qc(x)");
  assert.equal(renderPage(page(lenient, strict), recorder), 1);
  assert.match(lenient.html, /#cc0000/);
  assert.equal(strict.html, null);
  assert.equal(strict.textContent, "\\qc(x)");
});

test("each formula gets a fresh macro table", () => {
  const defines = element("\\gdef\\foo{x}\\foo");
  const uses = element("\\foo");
  assert.equal(renderPage(page(defines, uses), recorder), 1);
  assert.equal(uses.html, null);
  assert.equal(MACROS["\\foo"], undefined);
  assert.notEqual(katexOptions(false, false).macros, katexOptions(false, false).macros);
});

test("options match the build-time check", () => {
  assert.deepEqual(katexOptions(true, true), {
    displayMode: true, throwOnError: false, trust: false, strict: "ignore", output: "html",
    macros: MACROS,
  });
  assert.equal(katexOptions(false, false).throwOnError, true);
});

test("in a browser the script renders the page once, and does nothing without katex", () => {
  const source = readFileSync(scriptPath, "utf8");
  const rendered = element("x^2");
  const window = { katex: recorder, document: page(rendered) };
  vm.runInNewContext(source, { window });
  assert.match(rendered.html, /^<span class="katex">/);

  const untouched = element("x^2");
  vm.runInNewContext(source, { window: { document: page(untouched) } });
  assert.equal(untouched.html, null);
});
