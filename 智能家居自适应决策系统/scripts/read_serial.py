# -*- coding: utf-8 -*-
"""读取开发板串口输出，统计 PIR 触发情况。"""
import sys
import time
import re

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial
import serial.tools.list_ports as lp

# 优先选择 CH343 芯片的端口（VID 1A86 = UART 口）
target = None
for p in lp.comports():
    if p.vid == 0x1A86:
        target = p.device
        break
if target is None:
    ports = [p.device for p in lp.comports()]
    if not ports:
        print("未检测到串口，板子可能没插好")
        sys.exit(1)
    target = ports[0]

print("端口:", target)
print("读取 25 秒（请在这期间用手在 PIR 前挥动）")
print("=" * 64)

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
pir1_vals = []
pir2_vals = []
t0 = time.time()
while time.time() - t0 < 25:
    d = ser.read(512)
    if d:
        buf += d
ser.close()

txt = buf.decode("utf-8", errors="replace")
lines = [x for x in txt.splitlines() if x.strip()]
print("共 %d 行：" % len(lines))
print("=" * 64)
for x in lines:
    print(x)
    m = re.search(r"PIR1=(\d)\s+PIR2=(\d)", x)
    if m:
        pir1_vals.append(int(m.group(1)))
        pir2_vals.append(int(m.group(2)))

print()
print("=" * 64)
print("统计：")
if pir1_vals:
    n1 = sum(pir1_vals)
    n2 = sum(pir2_vals)
    tag = "有触发 OK" if n1 > 0 else "未触发"
    print("  PIR1: 采样 %d 次, 高电平 %d 次  -> %s" % (len(pir1_vals), n1, tag))
    print("  PIR2: 采样 %d 次, 高电平 %d 次" % (len(pir2_vals), n2))
else:
    print("  没有解析到 PIR 数据行")
