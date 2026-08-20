# trureturing-mdbook

本仓库把上游 [the-omega-institute/trureturing](https://github.com/the-omega-institute/trureturing) 的 `Blueprint/` Markdown 内容按固定规则投影为 mdBook，并通过 GitHub Pages 发布。站点是便于浏览和检索的派生产物，不是数学真源；真源始终是上游仓库及其 Git 历史。

每日 workflow 捕获上游 `dev` 分支当时的单一提交 SHA，只从该树读取 `Blueprint/**/*.md` 普通 blob，生成目录、首页、最近 30 个变更日的 first-parent 更新日志和 provenance。随后使用钉版的 mdBook、mdbook-katex 与 Pagefind Extended 构建，并通过源文件集合、页面映射、数学输出、相对链接和体积验证门后才允许部署。

## 本地构建

需要 Python 3、Git、mdBook 0.5.4、mdbook-katex 0.10.0 和 Pagefind Extended 1.5.2：

```sh
SITE_SRC="$(mktemp -d)"
python3 scripts/build-site.py /path/to/trureturing "$SITE_SRC"
MDBOOK_BOOK__SRC="$SITE_SRC" mdbook build --dest-dir book
pagefind_extended --site book --force-language zh
python3 scripts/verify-site.py /path/to/trureturing "$SITE_SRC" book
```

Pagefind 的分词语言固定为 `zh`，与保持为 `en` 的 mdBook 页面语言互不依赖。人工搜索验收可在本地启动 `python3 -m http.server --directory book 8000`，打开首页后分别查询一个上游实际中文词（例如“未入账”）和英文词（例如“Knaster”），两者都必须至少返回一条结果。

单元测试只依赖 Python 3 与 Git：

```sh
python3 -m unittest discover -s tests -v
```

## 许可边界

[LICENSE](LICENSE) 中的 MIT License **仅覆盖本仓库自写的生成器、配置与 workflow**。构建时展示的 Blueprint 内容取自上游；上游当前未声明任何内容许可证，本仓库不对该内容授予任何权利，也不进行再许可。KaTeX、Pagefind 及其生成资产适用各自的许可与 notice。完整说明见 [NOTICE.md](NOTICE.md)。
