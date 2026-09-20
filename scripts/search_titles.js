// Inlined by the generated homepage; never supplied to the Pagefind indexer.
function startSearchTitles(identity) {
  let started = false;
  window.addEventListener("DOMContentLoaded", async function () {
    if (started) return;
    started = true;
    let processResult;
    let timer;
    try {
      const asset = new URL(document.getElementById("pagefind-ui").src);
      if (!/^https?:$/.test(asset.protocol) || !asset.pathname.endsWith("/pagefind/pagefind-ui.js")) {
        throw new Error("Unexpected Pagefind UI asset");
      }
      const root = new URL("../", asset);
      const controller = new AbortController();
      const deadline = new Promise(resolve => {
        timer = setTimeout(() => {
          resolve(null);
          controller.abort();
        }, 2000);
      });
      const request = fetch(new URL(`search-titles-${identity}.json`, root), { signal: controller.signal })
        .then(response => {
          if (!response.ok) throw new Error("Title map unavailable");
          return response.json();
        });
      const map = await Promise.race([request, deadline]);
      const record = value => value !== null && typeof value === "object" && !Array.isArray(value);
      const filePath = path => typeof path === "string" && path.endsWith(".html") &&
        !/[\\\u0000-\u001f\u007f]/.test(path) &&
        path.split("/").every(segment => segment && segment !== "." && segment !== "..");
      if (!record(map) || map.version !== 1 || map.identity !== identity || !record(map.titles) ||
          !Object.entries(map.titles).every(([path, title]) =>
            filePath(path) && typeof title === "string" && title.trim())) {
        throw new Error("Invalid title map");
      }
      // Pagefind 1.5.2's public raw_url is '/' + literal site-relative file
      // path, with only a terminal index.html removed. Do not URL-decode it:
      // %, # and ? can be literal filename characters. No processed-URL fallback.
      processResult = result => {
        try {
          const raw = result.raw_url;
          if (typeof raw !== "string" || !raw.startsWith("/") || raw.startsWith("//")) return result;
          let path = raw.slice(1);
          if (!path || path.endsWith("/")) path += "index.html";
          if (!filePath(path) || !Object.hasOwn(map.titles, path)) return result;
          return { ...result, meta: { ...result.meta, title: map.titles[path] } };
        } catch (_) {
          return result;
        }
      };
    } catch (_) {
      // All enhancement failures use the original labels. The deadline settles
      // even if fetch ignores abort; late responses cannot install a processor.
    } finally {
      clearTimeout(timer);
    }
    const options = { element: "#library-search", showSubResults: true };
    if (processResult) options.processResult = processResult;
    new PagefindUI(options);
  }, { once: true });
}
