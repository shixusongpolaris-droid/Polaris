# -*- coding: utf-8 -*-
"""从官方说明书/协议文档中提取安装与天线朝向要求。"""
import os
import re
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from pypdf import PdfReader

DIR = r"D:\Dev\Tools\HLK"
KEYWORDS = ["天线", "安装", "正面", "背面", "遮挡", "金属", "朝向",
            "壳体", "盖板", "间距", "挂", "探测面", "辐射", "透镜"]

for name in ["LD2410C_manual_V1.09.pdf", "LD2410C_protocol_V1.09.pdf"]:
    path = os.path.join(DIR, name)
    if not os.path.exists(path):
        print("缺失:", path)
        continue
    r = PdfReader(path)
    text = "\n".join((pg.extract_text() or "") for pg in r.pages)
    print("=" * 72)
    print(f"文件: {name}  ({len(r.pages)} 页, {len(text)} 字符)")
    print("=" * 72)
    for kw in KEYWORDS:
        hits = list(re.finditer(kw, text))
        if not hits:
            continue
        print(f"\n--- 关键词「{kw}」（共 {len(hits)} 处）---")
        for m in hits[:2]:
            i = m.start()
            seg = text[max(0, i - 130):i + 260].replace("\n", " ")
            seg = re.sub(r"\s+", " ", seg)
            print(f"  …{seg}…")
    print()
