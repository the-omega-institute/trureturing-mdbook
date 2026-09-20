const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const { test } = require("node:test");
const vm = require("node:vm");
const script = readFileSync(require("node:path").join(__dirname, "../scripts/search_titles.js"), "utf8");
const identity = "1".repeat(64);
const titleMap = titles => ({ version: 1, identity, titles });
const response = value => ({ ok: true, json: async () => value });
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };

function harness({ fetcher = async () => response(titleMap({ "index.html": "Home" })),
                   asset = "https://example.test/project/pagefind/pagefind-ui.js", setup } = {}) {
  const callbacks = [], requests = [], instances = [], timers = new Map();
  const context = {
    URL, AbortController, setTimeout(fn, ms) { assert.equal(ms, 2000); timers.set(1, fn); return 1; },
    clearTimeout(id) { timers.delete(id); },
    window: { addEventListener(event, fn, options) {
      assert.equal(event, "DOMContentLoaded"); assert.equal(options.once, true); callbacks.push(fn);
    } },
    document: { getElementById(id) { assert.equal(id, "pagefind-ui"); return asset && { src: asset }; } },
    fetch(url, options) { requests.push({ url: String(url), options }); return fetcher(url, options); },
    PagefindUI: function (options) { instances.push(options); },
  };
  if (setup) setup(context);
  vm.createContext(context);
  vm.runInContext(script + `\nstartSearchTitles("${identity}");`, context);
  return { context, requests, instances, timers, start: () => callbacks[0](),
    expire: () => { for (const callback of timers.values()) callback(); } };
}

test("map settles before UI; asset URL determines root; one fetch and one initialization", async () => {
  for (const prefix of ["/", "/project/", "/nested/a%20b/"]) {
    let deliver;
    const h = harness({ asset: `https://example.test${prefix}pagefind/pagefind-ui.js?v=1#asset`,
      fetcher: () => new Promise(resolve => { deliver = resolve; }) });
    const pending = h.start();
    await flush();
    assert.equal(h.instances.length, 0);
    assert.equal(h.requests[0].url, `https://example.test${prefix}search-titles-${identity}.json`);
    deliver(response(titleMap({ "index.html": "Home" })));
    await pending;
    await h.start();
    assert.equal(h.requests.length, 1);
    assert.equal(h.instances.length, 1);
    assert.equal(typeof h.instances[0].processResult, "function");
    assert.equal(h.timers.size, 0);
  }
});

test("literal raw_url paths, index shortening, no decoding or alias guesses", async () => {
  const titles = { "index.html": "Home", "x/index.html": "Directory", "x.html": "File",
    "nested/ordinary.html": "Nested", "目录/汉 字%#?.html": "中文",
    "literal%2Fsegment.html": "Percent slash", "literal/segment.html": "Directory slash",
    "literal%252Fsegment.html": "Double percent", "Erdős.html": "Accent", "erdős.html": "Lowercase",
    "nested/a%23%3F.html": "Encoded literal", "a?b.html": "Question", "a#b.html": "Hash" };
  const h = harness({ fetcher: async () => response(titleMap(titles)) });
  await h.start();
  const process = h.instances[0].processResult;
  for (const [path, title] of Object.entries(titles)) {
    const result = { raw_url: "/" + path, url: "https://example.test/project/processed?highlight=x#h", meta: { title: "Stock" } };
    assert.equal(process(result).meta.title, title);
  }
  assert.equal(process({ raw_url: "/", meta: {} }).meta.title, "Home");
  assert.equal(process({ raw_url: "/x/", meta: {} }).meta.title, "Directory");
  for (const raw_url of [undefined, null, 42, "", "x.html", "/x", "/unknown/", "/X.html", "/Erdős.html",
    "/x.html?query=1", "/x.html#heading", "https://external.test/x.html", "//external.test/x.html",
    "/../x.html", "/./x.html", "/nested//ordinary.html", "/nested\\ordinary.html", "/x\n.html",
    "/%78.html", "/project/x.html", "/constructor", "/__proto__"]) {
    const original = { raw_url, url: "/x.html", meta: { title: "Stock" } };
    assert.equal(process(original), original, String(raw_url));
  }
});

test("synchronous copied result and meta preserve every other value, including subheadings", async () => {
  const title = '<img src=x onerror="alert(1)"> &lt;b&gt; 未入账 - Book';
  const h = harness({ fetcher: async () => response(titleMap({ "x.html": title })) });
  await h.start();
  const process = h.instances[0].processResult;
  const original = Object.freeze({ raw_url: "/x.html", url: "/project/x.html?q=a#b", excerpt: "<mark>x</mark>",
    sub_results: [{ title: "Subheading", url: "/x.html#sub" }], locations: [1, 2], score: 0.123,
    weighted_locations: [{ weight: 2 }], content: "body", meta: Object.freeze({ title: "Stock", image: "image.png", extra: "value" }) });
  const actual = process(original);
  assert.notEqual(actual, original);
  assert.notEqual(actual.meta, original.meta);
  assert.equal(actual.meta.title, title);
  assert.equal(actual.then, undefined);
  assert.deepEqual(Object.keys(actual), Object.keys(original));
  for (const key of Object.keys(original).filter(key => key !== "meta")) assert.equal(actual[key], original[key]);
  for (const key of Object.keys(original.meta).filter(key => key !== "title")) assert.equal(actual.meta[key], original.meta[key]);
  assert.equal(original.meta.title, "Stock");
  const hostile = { get raw_url() { throw Error("bad result"); } };
  assert.equal(process(hostile), hostile);
  assert.equal(process(null), null);
});

test("HTTP, JSON, schema, identity and enhancement setup failures each initialize stock exactly once", async () => {
  const failures = [
    { fetcher: async () => ({ ok: false }) },
    { fetcher: async () => { throw Error("network"); } },
    { fetcher: async () => ({ ok: true, json: async () => { throw SyntaxError("JSON"); } }) },
    ...[null, [], {}, { version: 2, identity, titles: {} }, { ...titleMap({}), identity: "stale" },
      titleMap([]), titleMap({ "x.html": " " }), titleMap({ "x.html": 4 }), titleMap({ "/x.html": "x" }),
      titleMap({ "../x.html": "x" })].map(value => ({ fetcher: async () => response(value) })),
    { asset: null }, { asset: "https://example.test/not-pagefind.js" },
    { setup: ctx => { ctx.AbortController = function () { throw Error("unavailable"); }; } },
    { fetcher: () => { throw Error("sync fetch setup"); } },
  ];
  for (const failure of failures) {
    const h = harness(failure);
    await h.start(); await h.start();
    assert.equal(h.instances.length, 1);
    assert.deepEqual(Object.keys(h.instances[0]).sort(), ["element", "showSubResults"]);
    assert.equal(h.timers.size, 0);
  }
});

test("two-second deadline includes body loading; ignored abort and late success/rejection cannot reinitialize", async () => {
  for (const body of [false, true]) {
    for (const rejectLate of [false, true]) {
      let deliver, reject;
      const slow = new Promise((resolve, fail) => { deliver = resolve; reject = fail; });
      const h = harness({ fetcher: () => body ? Promise.resolve({ ok: true, json: () => slow }) : slow });
      const pending = h.start();
      await flush();
      assert.equal(h.instances.length, 0);
      h.expire(); await pending;
      assert.equal(h.requests[0].options.signal.aborted, true);
      assert.equal(h.instances.length, 1);
      assert.equal(h.instances[0].processResult, undefined);
      if (rejectLate) reject(Error("late"));
      else deliver(body ? titleMap({ "index.html": "Late" }) : response(titleMap({ "index.html": "Late" })));
      await flush(); await h.start();
      assert.equal(h.instances.length, 1);
      assert.equal(h.requests.length, 1);
      assert.equal(h.instances[0].processResult, undefined);
    }
  }
});
