# -*- coding: utf-8 -*-
"""诊断：检查批处理脚本的日志与测试产物（用 Python 避免中文路径问题）。"""
import io
import os
import sys
import glob

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"

print("=" * 60)
print("1. data\\logs 目录")
print("=" * 60)
logdir = os.path.join(PROJ, "data", "logs")
if os.path.isdir(logdir):
    files = os.listdir(logdir)
    if files:
        for f in files:
            p = os.path.join(logdir, f)
            print(f"  {f}  ({os.path.getsize(p)} B)")
    else:
        print("  （空目录）")
else:
    print("  目录不存在")

print()
print("=" * 60)
print("2. 所有 collect_*.log 内容")
print("=" * 60)
logs = glob.glob(os.path.join(PROJ, "data", "logs", "collect_*.log"))
if not logs:
    print("  未找到日志文件")
for p in logs:
    print(f"\n--- {os.path.basename(p)} ---")
    try:
        txt = io.open(p, encoding="utf-8", errors="replace").read()
        for line in txt.splitlines():
            print("  " + line)
    except Exception as e:
        print(f"  读取失败: {e}")

print()
print("=" * 60)
print("3. data\\raw 全部文件")
print("=" * 60)
rawdir = os.path.join(PROJ, "data", "raw")
for f in sorted(os.listdir(rawdir)):
    p = os.path.join(rawdir, f)
    print(f"  {f:<36} {os.path.getsize(p):>9,} B")

print()
print("=" * 60)
print("4. daily_* 测试数据内容")
print("=" * 60)
for p in sorted(glob.glob(os.path.join(rawdir, "daily_*.csv"))):
    if p.endswith("_events.csv"):
        continue
    import csv
    rows = list(csv.DictReader(io.open(p, encoding="utf-8")))
    print(f"\n--- {os.path.basename(p)}: {len(rows)} 行 ---")
    for r in rows[:3]:
        print("  " + str({k: r[k] for k in list(r)[:8]}))
