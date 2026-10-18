# -*- coding: utf-8 -*-
"""零依赖的 Markdown -> HTML 转换器（用于在浏览器里阅读本项目的文档）。

为什么需要
----------
本机没有安装任何 Markdown 阅读器，也没有 pandoc / python-markdown /
Office / WPS，且 .md 扩展名没有文件关联，双击不会打开任何程序。
因此提供一个只依赖标准库的转换器，把文档转成自包含的 HTML，
用浏览器（Edge）阅读。

支持范围（覆盖本项目文档实际用到的语法）
----------------------------------------
  标题 # ~ #####、粗体 **、行内代码 `、围栏代码块 ```、
  表格（含对齐行）、无序/有序列表、引用 >、分隔线 ---、链接 [t](u)

不支持的（本项目文档未使用）：图片、嵌套列表、脚注、HTML 块。

用法
----
    python scripts/md2html.py                 # 转换 docs/ 下指定的几份
    python scripts/md2html.py <某个.md>       # 转换单个文件
    python scripts/md2html.py --all           # 转换 docs/ 下全部 .md
"""

import glob
import html
import io
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
DOCS = os.path.join(PROJ, "docs")
OUTDIR = os.path.join(PROJ, "docs", "_html")

CSS = """
:root { --fg:#1f2328; --muted:#57606a; --bd:#d0d7de; --bg:#ffffff;
        --code:#f6f8fa; --accent:#0969da; --warn:#fff8c5; }
* { box-sizing: border-box; }
body { margin:0; background:#f6f8fa; color:var(--fg);
       font: 16px/1.75 -apple-system, "Segoe UI", "Microsoft YaHei",
             "PingFang SC", sans-serif; }
.page { max-width: 1000px; margin: 0 auto; padding: 40px 28px 80px;
        background: var(--bg); }
h1,h2,h3,h4,h5 { line-height:1.35; margin: 1.6em 0 .6em; font-weight:600; }
h1 { font-size: 2em; border-bottom:2px solid var(--bd); padding-bottom:.3em; }
h2 { font-size: 1.5em; border-bottom:1px solid var(--bd); padding-bottom:.25em; }
h3 { font-size: 1.22em; }
h4 { font-size: 1.06em; color:var(--muted); }
p { margin: .75em 0; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
code { background: var(--code); padding: .15em .4em; border-radius: 5px;
       font: .9em/1.5 "Cascadia Mono", Consolas, monospace; }
pre { background: var(--code); padding: 14px 16px; border-radius: 8px;
      overflow: auto; border: 1px solid var(--bd); }
pre code { background: none; padding: 0; font-size: .88em; }
table { border-collapse: collapse; margin: 1.1em 0; width: 100%;
        font-size: .94em; display: block; overflow-x: auto; }
th, td { border: 1px solid var(--bd); padding: 7px 11px; text-align: left;
         vertical-align: top; }
th { background: #eef2f6; font-weight: 600; white-space: nowrap; }
tr:nth-child(even) td { background: #fbfcfd; }
blockquote { margin: 1em 0; padding: .6em 1em; border-left: 4px solid var(--accent);
             background: #f0f7ff; color: #1f2328; }
blockquote p { margin: .3em 0; }
ul, ol { margin: .7em 0; padding-left: 1.8em; }
li { margin: .25em 0; }
hr { border: 0; border-top: 1px solid var(--bd); margin: 2em 0; }
.toc { background:#f6f8fa; border:1px solid var(--bd); border-radius:8px;
       padding: 14px 20px; margin: 1.5em 0 2em; font-size: .94em; }
.toc div { margin: .2em 0; }
.toc .l2 { padding-left: 1.2em; color: var(--muted); }
.toc .l3 { padding-left: 2.4em; color: var(--muted); font-size: .95em; }
.meta { color: var(--muted); font-size: .86em; margin-bottom: 1.5em; }
"""


def inline(s):
    """处理行内语法：代码、粗体、斜体、链接。"""
    # 先保护行内代码
    codes = []

    def keep(m):
        codes.append(m.group(1))
        return f"\x00{len(codes)-1}\x00"

    s = re.sub(r"`([^`]+)`", keep, s)
    s = html.escape(s)
    s = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", s)
    s = re.sub(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])", r"<em>\1</em>", s)
    # 链接：支持相对路径（转成 .html）
    def link(m):
        text, url = m.group(1), m.group(2)
        if re.match(r"^https?://", url):
            return f'<a href="{url}" target="_blank">{text}</a>'
        u = url.split("#")[0]
        if u.endswith(".md"):
            u = u[:-3] + ".html"
        return f'<a href="{u}">{text}</a>'
    s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", link, s)
    # 还原行内代码
    def back(m):
        i = int(m.group(1))
        return f"<code>{html.escape(codes[i])}</code>"
    s = re.sub(r"\x00(\d+)\x00", back, s)
    return s


def convert(md_text):
    lines = md_text.splitlines()
    out = []
    i = 0
    n = len(lines)
    in_code = False
    code_buf = []
    in_list = None          # 'ul' / 'ol'
    in_quote = False
    table_buf = []

    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    def close_quote():
        nonlocal in_quote
        if in_quote:
            out.append("</blockquote>")
            in_quote = False

    def flush_table():
        nonlocal table_buf
        if not table_buf:
            return
        rows = table_buf
        table_buf = []
        # 去掉对齐行（第二行形如 |---|---|）
        if len(rows) >= 2 and re.match(r"^[\s:|-]+$", rows[1]):
            align = rows[1]
            body = [rows[0]] + rows[2:]
        else:
            align = ""
            body = rows

        def cells(r):
            r = r.strip()
            if r.startswith("|"):
                r = r[1:]
            if r.endswith("|"):
                r = r[:-1]
            return [c.strip() for c in r.split("|")]

        out.append("<table>")
        if body:
            out.append("<thead><tr>")
            for c in cells(body[0]):
                out.append(f"<th>{inline(c)}</th>")
            out.append("</tr></thead>")
        out.append("<tbody>")
        for r in body[1:]:
            out.append("<tr>")
            for c in cells(r):
                out.append(f"<td>{inline(c)}</td>")
            out.append("</tr>")
        out.append("</tbody></table>")

    while i < n:
        line = lines[i]

        # 围栏代码块
        if line.strip().startswith("```"):
            if in_code:
                out.append("<pre><code>"
                           + html.escape("\n".join(code_buf))
                           + "</code></pre>")
                code_buf = []
                in_code = False
            else:
                close_list()
                close_quote()
                flush_table()
                in_code = True
            i += 1
            continue
        if in_code:
            code_buf.append(line)
            i += 1
            continue

        # 表格
        if line.strip().startswith("|") and line.strip().endswith("|"):
            close_list()
            close_quote()
            table_buf.append(line.strip())
            i += 1
            continue
        else:
            flush_table()

        # 空行
        if not line.strip():
            close_list()
            close_quote()
            i += 1
            continue

        # 分隔线
        if re.match(r"^\s*(---+|\*\*\*+|___+)\s*$", line):
            close_list()
            close_quote()
            out.append("<hr>")
            i += 1
            continue

        # 标题
        m = re.match(r"^(#{1,5})\s+(.*)$", line)
        if m:
            close_list()
            close_quote()
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2).strip())}</h{lvl}>")
            i += 1
            continue

        # 引用
        if line.lstrip().startswith(">"):
            close_list()
            if not in_quote:
                out.append("<blockquote>")
                in_quote = True
            out.append(f"<p>{inline(line.lstrip()[1:].strip())}</p>")
            i += 1
            continue
        else:
            close_quote()

        # 列表
        m = re.match(r"^(\s*)([-*+])\s+(.*)$", line)
        if m:
            if in_list != "ul":
                close_list()
                out.append("<ul>")
                in_list = "ul"
            out.append(f"<li>{inline(m.group(3))}</li>")
            i += 1
            continue
        m = re.match(r"^(\s*)(\d+)[.)]\s+(.*)$", line)
        if m:
            if in_list != "ol":
                close_list()
                out.append("<ol>")
                in_list = "ol"
            out.append(f"<li>{inline(m.group(3))}</li>")
            i += 1
            continue
        close_list()

        # 普通段落
        out.append(f"<p>{inline(line.strip())}</p>")
        i += 1

    if in_code and code_buf:
        out.append("<pre><code>" + html.escape("\n".join(code_buf))
                   + "</code></pre>")
    close_list()
    close_quote()
    flush_table()
    return "\n".join(out)


def build_toc(md_text):
    """从标题生成目录。"""
    items = []
    for line in md_text.splitlines():
        m = re.match(r"^(#{1,3})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            items.append((lvl, m.group(2).strip()))
    if len(items) < 3:
        return ""
    parts = ['<div class="toc"><strong>目录</strong>']
    for lvl, text in items:
        parts.append(f'<div class="l{lvl}">{inline(text)}</div>')
    parts.append("</div>")
    return "\n".join(parts)


def convert_file(src):
    md = io.open(src, encoding="utf-8").read()
    title = os.path.splitext(os.path.basename(src))[0]
    m = re.search(r"^#\s+(.*)$", md, re.M)
    if m:
        title = m.group(1).strip()
    body = convert(md)
    toc = build_toc(md)
    page = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{CSS}</style>
</head>
<body>
<div class="page">
<div class="meta">源文件：{html.escape(os.path.basename(src))}
　·　由 scripts/md2html.py 生成</div>
{toc}
{body}
</div>
</body>
</html>"""
    os.makedirs(OUTDIR, exist_ok=True)
    dst = os.path.join(OUTDIR, os.path.splitext(os.path.basename(src))[0]
                       + ".html")
    io.open(dst, "w", encoding="utf-8").write(page)
    return dst


DEFAULT = ["项目核心结论.md", "结题引用守则.md", "实验结果报告.md",
           "项目状态总结.md", "gen6体检报告.md", "模型代次变更记录.md"]


def main():
    args = sys.argv[1:]
    if not args:
        files = [os.path.join(DOCS, f) for f in DEFAULT]
    elif args[0] == "--all":
        files = sorted(glob.glob(os.path.join(DOCS, "*.md")))
    else:
        files = args

    ok = 0
    for f in files:
        if not os.path.exists(f):
            print(f"  ⚠️ 不存在: {f}")
            continue
        dst = convert_file(f)
        size = os.path.getsize(dst) / 1024
        print(f"  ✅ {os.path.basename(f)} -> "
              f"{os.path.relpath(dst, PROJ)} ({size:.1f} KB)")
        ok += 1
    print(f"\n  共转换 {ok} 个文件到 docs/_html/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
