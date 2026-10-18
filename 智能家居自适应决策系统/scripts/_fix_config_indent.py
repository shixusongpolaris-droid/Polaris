# -*- coding: utf-8 -*-
"""一次性修复：把误插到 SimConfig 类外面的 gen7 字段移回类内。

原因：`occupancy_prob` 是 SimConfig 的最后一个字段，我在它之后插入的
新字段落到了模块级（缩进 4 但已不在类体内），导致
"unexpected indent" 语法错误。
"""

import ast
import io
import os

P = (r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
     r"\src\env\config.py")

START = "    # ---------- 占用生成模式（gen7 新增）----------"
ANCHOR = "    # ---------- 占用基础概率表 ----------"


def main():
    t = io.open(P, encoding="utf-8").read()
    lines = t.splitlines()

    i0 = next(i for i, l in enumerate(lines) if l.startswith(START))
    # 块结束：sm_hazard_h30 的最后一行（以 "])" 结尾的那行）
    i1 = i0
    while i1 < len(lines) and "sm_hazard_h30" not in lines[i1]:
        i1 += 1
    # 再前进到该字段结束
    while i1 < len(lines) and not lines[i1].rstrip().endswith("])"):
        i1 += 1
    block = lines[i0:i1 + 1]
    print(f"  待移动字段块：第 {i0+1} ~ {i1+1} 行（{len(block)} 行）")

    rest = lines[:i0] + lines[i1 + 1:]
    # 删掉插入后多出来的连续空行
    while i0 < len(rest) and not rest[i0].strip():
        del rest[i0]

    j = next(i for i, l in enumerate(rest) if l.startswith(ANCHOR))
    new = rest[:j] + block + [""] + rest[j:]
    out = "\n".join(new) + "\n"
    try:
        ast.parse(out)
    except SyntaxError as e:
        print(f"  ❌ 仍语法错误: {e}")
        return 1
    io.open(P, "w", encoding="utf-8").write(out)
    print(f"  ✅ 已移到第 {j+1} 行之前（类内），语法通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
