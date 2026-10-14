# -*- coding: utf-8 -*-
"""复位开发板并立即捕获串口输出，确认当前运行的是哪个固件。"""
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial

PORT = "COM3"
ser = serial.Serial(PORT, 115200, timeout=1)
ser.dtr = False
ser.rts = False
time.sleep(0.3)
ser.reset_input_buffer()

# 用经典 esptool 时序触发 EN 复位
print("触发复位…")
ser.dtr = True
ser.rts = False
time.sleep(0.12)
ser.rts = True
time.sleep(0.12)
ser.rts = False
time.sleep(0.05)
ser.dtr = False

# 立刻读取
buf = b""
t0 = time.time()
while time.time() - t0 < 12:
    d = ser.read(1024)
    if d:
        buf += d
ser.close()

lines = [x.strip() for x in buf.decode("utf-8", errors="replace").splitlines() if x.strip()]
print(f"收到 {len(lines)} 行：")
print("=" * 60)
for l in lines[:30]:
    print(" ", l)

print()
if any("电压测量" in l for l in lines):
    print("=> 当前固件: RadarVoltTest（电压测量）")
elif any("数据采集程序启动" in l for l in lines):
    print("=> 当前固件: 采集固件（Collector）")
elif any("硬件验证程序" in l for l in lines):
    print("=> 当前固件: HardwareTest")
elif any("esp_image" in l or "boot:" in l for l in lines):
    print("=> 板子在启动循环（固件损坏）")
else:
    print("=> 无法判断（无输出或输出被截断）")
