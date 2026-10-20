"""把 Markdown 分析报告渲染为可直接在浏览器/答辩现场查看的 HTML。"""
from __future__ import annotations

import sys
from pathlib import Path

import markdown

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "reports" / "历史工单分析报告.md"
OUT = ROOT / "reports" / "历史工单分析报告.html"

CSS = """
:root { --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --brand:#2563eb; --bg:#f8fafc; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink); font-family:"PingFang SC","Microsoft YaHei","Hiragino Sans GB",sans-serif; line-height:1.85; }
.report-top { background:linear-gradient(125deg,#0b1f3a 0%,#123a6b 55%,#0e7490 100%); color:#fff; padding:44px 0 38px; }
.wrap { width:min(980px,92vw); margin:0 auto; }
.report-top .eyebrow { letter-spacing:.22em; font-size:12px; color:#7dd3fc; text-transform:uppercase; }
.report-top h1 { margin:10px 0 8px; font-size:34px; line-height:1.3; font-weight:700; color:#ffffff; }
.report-top p { margin:0; color:#c7d7ee; font-size:14px; }
.report-top .meta { display:flex; gap:16px; flex-wrap:wrap; margin-top:18px; color:#dbeafe; font-size:13px; }
.report-top .meta span { background:rgba(255,255,255,.12); border:1px solid rgba(255,255,255,.16); border-radius:999px; padding:5px 14px; }
.toolbar { display:flex; justify-content:space-between; align-items:center; margin:18px 0 0; }
.toolbar a, .toolbar button { border:1px solid var(--line); background:#fff; color:#334155; padding:8px 16px; border-radius:8px; font-size:13px; cursor:pointer; text-decoration:none; }
.toolbar a:hover, .toolbar button:hover { border-color:var(--brand); color:var(--brand); }
.paper { background:#fff; margin:22px auto 50px; padding:54px 64px; border:1px solid var(--line); border-radius:14px; box-shadow:0 18px 50px rgba(15,23,42,.08); }
h1 { font-size:30px; margin:0 0 18px; color:#0b1f3a; }
h2 { font-size:23px; margin:38px 0 14px; padding-bottom:9px; border-bottom:1px solid var(--line); color:#0b1f3a; }
h3 { font-size:18px; margin:28px 0 10px; color:#1e3a8a; }
h4 { font-size:16px; margin:22px 0 8px; }
p { margin:10px 0; color:#334155; }
strong { color:#0f172a; }
table { width:100%; border-collapse:collapse; margin:16px 0 22px; font-size:13.5px; }
th { background:#eff6ff; color:#1e40af; font-weight:600; text-align:left; }
th, td { border:1px solid #e2e8f0; padding:9px 11px; vertical-align:top; }
tr:nth-child(even) td { background:#f8fafc; }
img { max-width:100%; height:auto; display:block; margin:18px auto 8px; border:1px solid var(--line); border-radius:10px; }
blockquote { margin:16px 0; padding:12px 18px; background:#f0f9ff; border-left:4px solid #0ea5e9; color:#0c4a6e; }
code { background:#f1f5f9; padding:2px 6px; border-radius:4px; font-size:.9em; }
pre { background:#0f172a; color:#e2e8f0; padding:16px; border-radius:10px; overflow:auto; }
pre code { background:transparent; color:inherit; }
ul, ol { padding-left:24px; color:#334155; }
li { margin:5px 0; }
hr { border:0; border-top:1px solid var(--line); margin:30px 0; }
.foot { text-align:center; color:var(--muted); font-size:12px; padding-bottom:30px; }
@media (max-width:720px) { .paper { padding:28px 20px; } .report-top h1 { font-size:25px; } }
@media print {
  body { background:#fff; }
  .report-top { background:#0b1f3a !important; -webkit-print-color-adjust:exact; print-color-adjust:exact; }
  .toolbar { display:none; }
  .paper { border:0; box-shadow:none; margin:0; padding:20px 0; }
  h2 { page-break-after:avoid; }
  table, img { page-break-inside:avoid; }
}
"""


def main() -> None:
    if not SRC.exists():
        raise SystemExit(f"缺少报告文件：{SRC}")
    text = SRC.read_text(encoding="utf-8")
    body = markdown.markdown(
        text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
        output_format="html5",
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>历史工单分析报告 · 智修云</title>
<style>{CSS}</style>
</head>
<body>
<header class="report-top">
  <div class="wrap">
    <div class="eyebrow">ZHIXIU CLOUD · DATA ANALYSIS</div>
    <h1>小区物业历史报修工单数据分析报告</h1>
    <p>智修云 · 多智能体物业工单闭环系统 · 离线分析分册</p>
    <div class="meta"><span>3000 条历史工单</span><span>180 天</span><span>13 类报修</span><span>12 名维修工</span></div>
  </div>
</header>
<div class="wrap">
  <div class="toolbar">
    <a href="/">← 返回智修云看板</a>
    <button onclick="window.print()">打印 / 导出 PDF</button>
  </div>
  <main class="paper">{body}</main>
  <div class="foot">智修云 · 多智能体物业工单闭环系统　|　报告由 scripts/analyze.py 生成，数据可复现</div>
</div>
</body>
</html>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"已生成：{OUT}（{OUT.stat().st_size / 1024:.1f} KB）")


if __name__ == "__main__":
    main()
