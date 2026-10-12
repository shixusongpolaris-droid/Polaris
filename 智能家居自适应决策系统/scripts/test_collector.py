# -*- coding: utf-8 -*-
"""读取采集固件的 JSON 输出，验证数据格式。"""
import json
import re
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial
import serial.tools.list_ports as lp

target = None
for p in lp.comports():
    if p.vid == 0x1A86:
        target = p.device
        break
if target is None:
    ports = [p.device for p in lp.comports()]
    if not ports:
        print("未检测到串口")
        sys.exit(1)
    target = ports[0]

print("端口:", target)
print("读取 40 秒（采集固件每 10 秒输出一行 JSON）")
print("=" * 70)

ser = serial.Serial()
ser.port = target
ser.baudrate = 115200
ser.timeout = 1
ser.dtr = False
ser.rts = False
ser.open()
time.sleep(0.3)
ser.reset_input_buffer()

buf = b""
t0 = time.time()
while time.time() - t0 < 40:
    d = ser.read(512)
    if d:
        buf += d
ser.close()

txt = buf.decode("utf-8", errors="replace")
lines = [x for x in txt.splitlines() if x.strip()]
print("共 %d 行原始输出：" % len(lines))
print("-" * 70)
for x in lines:
    print(x)

# 解析 JSON 行
jsons = []
for x in lines:
    m = re.search(r"\{.*\}", x)
    if m:
        try:
            jsons.append(json.loads(m.group(0)))
        except Exception:
            pass

print()
print("=" * 70)
print("解析结果：")
if not jsons:
    print("  ❌ 没有解析到 JSON 行")
    sys.exit(1)

print("  ✅ 解析到 %d 条 JSON 记录" % len(jsons))
print()
print("  字段           样例值")
print("  " + "-" * 50)
keys = ["ts", "temp", "hum", "lux", "pir1", "pir2", "radar", "sht_ok", "up"]
for k in keys:
    vals = [str(j.get(k)) for j in jsons if k in j]
    if vals:
        print("  %-14s %s" % (k, vals[-1]))

print()
print("  完整最后一条记录：")
print("   ", json.dumps(jsons[-1], ensure_ascii=False))

# 质量检查
print()
print("=" * 70)
print("质量检查：")
last = jsons[-1]
checks = [
    ("温度有值", last.get("temp") is not None),
    ("湿度有值", last.get("hum") is not None),
    ("光照有值", last.get("lux") is not None),
    ("SHT31 状态正常", last.get("sht_ok") == 1),
    ("时间戳存在", bool(last.get("ts"))),
]
for name, ok in checks:
    print("  %s %s" % ("✅" if ok else "❌", name))
