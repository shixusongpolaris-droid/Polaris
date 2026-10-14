# -*- coding: utf-8 -*-
"""调试：打印从雷达收到的每一帧的类型与内容。"""
import struct
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import serial

PORT = "COM5"
BAUD = 256000
HEADER = b"\xf4\xf3\xf2\xf1"
TAIL = b"\xf8\xf7\xf6\xf5"


def build_frame(cmd, params=b""):
    payload = struct.pack("<HH", cmd, 0) + params
    return HEADER + struct.pack("<H", len(payload)) + payload + TAIL


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

    print("=== 观察 8 秒的原始帧 ===")
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 8:
        d = ser.read(4096)
        if d:
            buf += d
    frames = split_frames(buf)
    print(f"共切出 {len(frames)} 帧，原始数据 {len(buf)} 字节")

    # 统计帧长与类型
    from collections import Counter
    lens = Counter(len(f) for f in frames)
    types = Counter()
    for f in frames:
        if len(f) >= 4:
            types[struct.unpack("<H", f[2:4])[0]] += 1
    print(f"帧体长度分布: {dict(lens)}")
    print(f"数据类型分布: { {hex(k): v for k, v in types.items()} }")

    # 打印前 3 帧的完整十六进制
    print("\n前 3 帧完整内容：")
    for f in frames[:3]:
        print(f"  len={len(f):<3} {f.hex(' ')}")

    # 尝试解析目标帧
    print("\n目标帧解析（前 5 帧）：")
    n = 0
    for f in frames:
        if len(f) >= 13 and struct.unpack("<H", f[2:4])[0] == 0x0202:
            state = f[4]
            mv = struct.unpack("<H", f[5:7])[0]
            mve = f[7]
            st = struct.unpack("<H", f[8:10])[0]
            ste = f[10]
            dd = struct.unpack("<H", f[11:13])[0]
            names = {0: "无人", 1: "运动", 2: "静止", 3: "运动+静止"}
            print(f"  状态={names.get(state, hex(state)):<10}"
                  f"运动距离={mv:>4}cm(能量{mve:>3}) "
                  f"静止距离={st:>4}cm(能量{ste:>3}) 探测距离={dd:>4}cm")
            n += 1
            if n >= 5:
                break
    if n == 0:
        print("  未找到类型 0x0202 的帧")

    # ---- 发送配置命令，观察应答 ----
    print("\n=== 发送命令并观察应答 ===")
    for name, cmd in [("使能配置", 0x00FF), ("读取参数", 0x0002),
                      ("结束配置", 0x00FE)]:
        ser.reset_input_buffer()
        frame = build_frame(cmd)
        ser.write(frame)
        print(f"\n发送 {name} (cmd=0x{cmd:04X}): {frame.hex(' ')}")
        time.sleep(0.8)
        buf = b""
        t0 = time.time()
        while time.time() - t0 < 2:
            d = ser.read(4096)
            if d:
                buf += d
        fs = split_frames(buf)
        print(f"  收到 {len(buf)} 字节 / {len(fs)} 帧")
        for f in fs[:6]:
            if len(f) >= 7:
                c = struct.unpack("<H", f[2:4])[0]
                print(f"    len={len(f):<3} cmd=0x{c:04X} "
                      f"status=0x{f[6]:02X}  data={f[7:].hex(' ')[:80]}")
            else:
                print(f"    len={len(f):<3} {f.hex(' ')}")

    ser.close()


if __name__ == "__main__":
    main()
