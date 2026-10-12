# -*- coding: utf-8 -*-
"""验证采集脚本的"按自然日分割文件"逻辑（模拟跨零点）。"""
import sys
import shutil
import tempfile
from datetime import datetime as real_dt
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import collect_data  # noqa: E402

# 用可控的假时间替换 collect_data 里的 datetime
FAKE = {"now": real_dt(2026, 10, 12, 23, 59, 40)}


class FakeDateTime:
    @staticmethod
    def now():
        return FAKE["now"]

    @staticmethod
    def strptime(*a, **k):
        return real_dt.strptime(*a, **k)


collect_data.datetime = FakeDateTime

tmp = Path(tempfile.mkdtemp(prefix="collect_test_"))
print("临时目录:", tmp)

rec = collect_data.Recorder(tmp, note="跨日测试")

# 第 1 天：写 2 行
for i in range(2):
    FAKE["now"] = real_dt(2026, 10, 12, 23, 59, 40 + i * 10)
    rec.write('{"ts":"uptime:1","temp":22.0,"hum":50.0,"lux":30.0,'
              '"pir1":0,"pir2":0,"radar":0,"sht_ok":1,"up":1}')

# 跨零点
FAKE["now"] = real_dt(2026, 10, 13, 0, 0, 10)
rec.write('{"ts":"uptime:2","temp":22.1,"hum":50.1,"lux":31.0,'
          '"pir1":1,"pir2":0,"radar":0,"sht_ok":1,"up":2}')

# 第 2 天：再写 1 行
FAKE["now"] = real_dt(2026, 10, 13, 0, 0, 20)
rec.write('{"ts":"uptime:3","temp":22.2,"hum":50.2,"lux":32.0,'
          '"pir1":0,"pir2":0,"radar":0,"sht_ok":1,"up":3}')

rec.close()

print()
print("=== 生成的文件 ===")
for f in sorted(tmp.iterdir()):
    lines = f.read_text(encoding="utf-8").strip().splitlines()
    print(f"  {f.name}: {len(lines)} 行")
    for l in lines[:3]:
        print("      " + l[:78])

print()
print("=== 判定 ===")
files = {f.name for f in tmp.iterdir()}
ok1 = "2026-10-12.csv" in files
ok2 = "2026-10-13.csv" in files
ok3 = "2026-10-12_events.csv" in files
ok4 = "2026-10-13_events.csv" in files

n12 = len((tmp / "2026-10-12.csv").read_text(encoding="utf-8").strip().splitlines()) - 1
n13 = len((tmp / "2026-10-13.csv").read_text(encoding="utf-8").strip().splitlines()) - 1

print(f"  生成 12 日文件: {'OK' if ok1 else 'FAIL'}")
print(f"  生成 13 日文件: {'OK' if ok2 else 'FAIL'}")
print(f"  生成事件文件:   {'OK' if (ok3 and ok4) else 'FAIL'}")
print(f"  12 日数据行数: {n12} (期望 2)  {'OK' if n12 == 2 else 'FAIL'}")
print(f"  13 日数据行数: {n13} (期望 2)  {'OK' if n13 == 2 else 'FAIL'}")

allok = ok1 and ok2 and ok3 and ok4 and n12 == 2 and n13 == 2
print()
print("  总判定:", "全部通过" if allok else "存在失败项")

shutil.rmtree(tmp, ignore_errors=True)
sys.exit(0 if allok else 1)
