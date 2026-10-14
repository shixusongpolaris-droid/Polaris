# -*- coding: utf-8 -*-
"""严格分析 LD2410C 帧结构：用校验值反推字段边界。

思路：官方文档说帧内数据末尾是 [0x55][校验]。
已知官方实例：
  F4F3F2F1 0D00 02 AA 02 51 00 00 00 00 3B 00 00 55 00 F8F7F6F5
数据段（长度=13）= 02 AA 02 51 00 00 00 00 3B 00 00 55 00
  末两字节 = 55 00  ->  0x55 是固定尾部，0x00 是校验
  校验 = 前面所有字节之和 mod 256 ?  验证中

本脚本对抓到的每个帧尝试多种校验算法，找出与实测一致的那种，
以此确定"数据段"的确切范围。
"""
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

# 官方实例（用于校准校验算法）
OFFICIAL = bytes.fromhex("f4f3f2f10d0002aa0251000000003b00005500f8f7f6f5")


def body_of(frame):
    return frame[4:-4]


def try_checksums(body):
    """对帧体尝试多种校验算法，返回命中的算法名列表。"""
    length = struct.unpack("<H", body[0:2])[0]
    # 数据段：从 body[2] 起，长度 = length
    data = body[2:2 + length]
    if len(data) != length:
        return length, None, []
    hits = []
    # 算法 A：数据段除最后 1 字节外求和
    if (sum(data[:-1]) & 0xFF) == data[-1]:
        hits.append("A:sum(data[:-1])")
    # 算法 B：数据段除最后 2 字节外求和
    if length >= 2 and (sum(data[:-2]) & 0xFF) == data[-2]:
        hits.append("B:sum(data[:-2])==data[-2]")
    # 算法 C：数据段除最后 2 字节外求和 == data[-1]
    if length >= 2 and (sum(data[:-2]) & 0xFF) == data[-1]:
        hits.append("C:sum(data[:-2])==data[-1]")
    # 算法 D：整帧除帧尾外求和
    if (sum(body) & 0xFF) == data[-1]:
        hits.append("D:sum(body)==data[-1]")
    # 算法 E：数据段全部求和 == data[-1]（自校验）
    if (sum(data) & 0xFF) == data[-1]:
        hits.append("E:sum(data)==data[-1]")
    return length, data, hits


def main():
    print("=== 第 0 步：用官方实例校准校验算法 ===")
    b = body_of(OFFICIAL)
    length, data, hits = try_checksums(b)
    print(f"  官方帧体: {b.hex(' ')}")
    print(f"  长度字段: {length}  数据段: {data.hex(' ')}")
    print(f"  数据段末字节(校验候选): 0x{data[-1]:02X}")
    print(f"  命中算法: {hits if hits else '（都不命中）'}")
    print(f"  数据段求和 mod256 = 0x{sum(data) & 0xFF:02X}")
    print(f"  除末字节求和     = 0x{sum(data[:-1]) & 0xFF:02X}")
    print(f"  除末两字节求和   = 0x{sum(data[:-2]) & 0xFF:02X}")
    print(f"  帧体求和 mod256  = 0x{sum(b) & 0xFF:02X}")
    print()

    print("=== 第 1 步：抓取实测帧，逐一检验 ===")
    ser = serial.Serial(PORT, BAUD, timeout=1)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.3)
    ser.reset_input_buffer()
    buf = b""
    t0 = time.time()
    while time.time() - t0 < 6:
        d = ser.read(4096)
        if d:
            buf += d
    ser.close()

    frames = []
    while True:
        i = buf.find(HEADER)
        if i < 0:
            break
        j = buf.find(TAIL, i)
        if j < 0:
            break
        frames.append(buf[i:j + 4])
        buf = buf[j + 4:]

    print(f"  抓到 {len(frames)} 帧")
    algos = {}
    samples = []
    for f in frames:
        b = body_of(f)
        length, data, hits = try_checksums(b)
        for h in hits:
            algos[h] = algos.get(h, 0) + 1
        if len(samples) < 6:
            samples.append((f, b, length, data, hits))

    print(f"  校验算法命中统计: {algos if algos else '（无任何算法命中）'}")
    print()
    print("=== 前 6 帧详细对照 ===")
    for f, b, length, data, hits in samples:
        print(f"  完整帧 : {f.hex(' ')}")
        print(f"    长度={length}  数据段={data.hex(' ')}")
        print(f"    实测末字节=0x{data[-1]:02X}  "
              f"求和mod256=0x{sum(data) & 0xFF:02X}  "
              f"除末=0x{sum(data[:-1]) & 0xFF:02X}  命中={hits}")
        print()


if __name__ == "__main__":
    main()
