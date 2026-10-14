# -*- coding: utf-8 -*-
"""雷达帧格式实证分析：按距离做对照实验，确定字段含义。

用法：
    python radar_probe.py <标签>
    例：python radar_probe.py 近处0.5m
采集 8 秒数据，对每一帧打印若干种候选解析结果，便于对照。
"""
import struct
import sys
import time
from collections import Counter

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial

PORT = "COM5"
BAUD = 256000
HEADER = b"\xf4\xf3\xf2\xf1"
TAIL = b"\xf8\xf7\xf6\xf5"

label = sys.argv[1] if len(sys.argv) > 1 else "(未命名)"


def split_frames(buf):
    frames = []
    while True:
        i = buf.find(HEADER)
        if i < 0:
            break
        j = buf.find(TAIL, i)
        if j < 0:
            break
        frames.append(buf[i + 4:j])
        buf = buf[j + 4:]
    return frames


def main():
    ser = serial.Serial(PORT, BAUD, timeout=1)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.3)
    ser.reset_input_buffer()

    print(f"=== 测试场景: {label} ===")
    print("采集 8 秒…")
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 8:
        d = ser.read(4096)
        if d:
            buf += d
    ser.close()

    frames = split_frames(buf)
    print(f"共 {len(frames)} 帧，帧长分布 {dict(Counter(len(f) for f in frames))}")
    print()

    # 统计首字节（可能是目标数量）
    first_bytes = Counter(f[0] for f in frames if len(f) >= 1)
    print(f"帧内首字节分布（可能是目标数量）: "
          f"{ {hex(k): v for k, v in first_bytes.items()} }")
    print()

    # 多种候选解析对照
    print("候选解析对比（取最后 12 帧）：")
    print(f"  {'帧内hex':<46}{'b1/2 LE':>9}{'b3/4 LE':>9}{'b6/7 LE':>9}{'b8/9 LE':>9}")
    print("  " + "-" * 82)
    for f in frames[-12:]:
        h = f.hex(" ")
        def le(o):
            if len(f) >= o + 2:
                return struct.unpack("<H", f[o:o + 2])[0]
            return -1
        print(f"  {h:<46}{le(1):>9}{le(3):>9}{le(6):>9}{le(8):>9}")

    # 汇总各候选字段的取值范围
    print()
    print("各候选字段的取值范围（判断哪个像距离 cm）：")
    for name, o in [("帧[1:3] LE", 1), ("帧[3:5] LE", 3),
                    ("帧[6:8] LE", 6), ("帧[8:10] LE", 8)]:
        vals = [struct.unpack("<H", f[o:o + 2])[0]
                for f in frames if len(f) >= o + 2]
        if vals:
            print(f"  {name:<14} 最小 {min(vals):>6}  最大 {max(vals):>6}  "
                  f"中位 {sorted(vals)[len(vals)//2]:>6}")

    # 能量类字段（单字节）
    print()
    print("候选单字节字段的取值范围（判断哪个像能量）：")
    for name, o in [("帧[2]", 2), ("帧[5]", 5), ("帧[7]", 7), ("帧[10]", 10)]:
        vals = [f[o] for f in frames if len(f) > o]
        if vals:
            print(f"  {name:<14} 最小 {min(vals):>4}  最大 {max(vals):>4}  "
                  f"中位 {sorted(vals)[len(vals)//2]:>4}")

    print()
    print("=" * 84)
    print("请记录：本场景下哪些字段随距离/动作明显变化。")


if __name__ == "__main__":
    main()
