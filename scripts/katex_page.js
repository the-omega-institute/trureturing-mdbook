// Renders a page's formulas in the browser, published as katex/katex-page.js.
//
// scripts/render_katex.py leaves each formula as its TeX source in
// <span class="katex-source">, after KaTeX under Node (scripts/katex_render.js) has
// already rendered it once with the options below: a formula KaTeX cannot render becomes
// a <code class="katex-fallback"> and never reaches this script. Node requires this file
// for those options, so the build-time check and the browser cannot drift apart, and both
// run the KaTeX release pinned in package-lock.json.
//
// Rendering is one synchronous pass in a deferred script, before DOMContentLoaded: the
// browser lays the page out once and scrolls to a #fragment after the math has its final
// size. Without JavaScript, or if katex.min.js fails to load, the TeX source stays visible.
"use strict";

(function (root) {
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

  // `lenient` is for a formula whose only defect is a macro defined in the quoted paper:
  // KaTeX renders the rest and shows that command's name in red.
  function katexOptions(display, lenient) {
    return {
      displayMode: display,
      throwOnError: !lenient,
      trust: false,
      // LaTeX-compatibility notices change nothing in the output.
      strict: "ignore",
      output: "html",
      // A fresh copy: \gdef in one formula must not leak into the next.
      macros: Object.assign({}, MACROS),
    };
  }

  // Renders every source element in document order and returns how many rendered. One
  // that throws (the build already rendered it, so this is not expected) keeps its source.
  function renderPage(document, katex) {
    let rendered = 0;
    for (const element of document.querySelectorAll("span.katex-source")) {
      const display = element.getAttribute("data-display") === "true";
      const lenient = element.getAttribute("data-lenient") === "true";
      try {
        katex.render(element.textContent, element, katexOptions(display, lenient));
        rendered += 1;
      } catch (error) {
        // Leave the TeX source in place.
      }
    }
    return rendered;
  }

  if (typeof module === "object" && module.exports) {
    module.exports = { MACROS, katexOptions, renderPage };
  } else if (root.katex && root.document) {
    renderPage(root.document, root.katex);
  }
})(typeof window === "undefined" ? globalThis : window);
