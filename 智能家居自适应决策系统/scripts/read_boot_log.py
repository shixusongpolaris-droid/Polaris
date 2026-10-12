# -*- coding: utf-8 -*-
"""读取开发板完整开机日志：先复位再采集，确保捕获 setup() 的全部输出。"""
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
print("=" * 70)
print(">>> 请现在按一下开发板上的 RST 按钮 <<<")
print("    （我会持续读取 25 秒，按 RST 后就能看到完整开机日志）")
print("=" * 70)

ser = serial.Serial()
ser.port = target
ser.baudrate = 115200
ser.timeout = 1
ser.dtr = False
ser.rts = False
ser.open()
time.sleep(0.2)
ser.reset_input_buffer()

buf = b""
t0 = time.time()
while time.time() - t0 < 25:
    d = ser.read(512)
    if d:
        buf += d
ser.close()

txt = buf.decode("utf-8", errors="replace")
lines = [x for x in txt.splitlines() if x.strip()]
print("共 %d 行：" % len(lines))
print("=" * 70)
for x in lines:
    print(x)
