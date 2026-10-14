# -*- coding: utf-8 -*-
"""HLK-LD2410C 正确解析器（依据官方《LD2410C 串口通信协议 V1.09》）。

帧格式（表 10 / 11）：
    F4 F3 F2 F1 | 长度(2B 小端) | 数据类型(1B) | 0xAA |
    目标数据 | 0x55 | 校验(1B) | F8 F7 F6 F5

目标数据（表 13，共 8 字节）：
    [0]     目标状态  0x00 无目标 / 0x01 运动 / 0x02 静止 / 0x03 运动+静止
    [1:3]   运动目标距离 (厘米, 小端)
    [3]     运动目标能量 (0~100)
    [4:6]   静止目标距离 (厘米, 小端)
    [6]     静止目标能量 (0~100)
    [7]     (文档记有"探测距离 2 字节"，但与 8 字节总长矛盾，
             实测该位置为 0x00，按文档顺序处置为保留字节)
    尾部 0x55 + 校验 1 字节

校验：帧内数据（长度字段之后、帧尾之前）按字节累加取低 8 位。

用法：
    python radar_parse.py            实时显示 12 秒
    python radar_parse.py <标签>      带标签记录一次场景
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

STATE_NAME = {
    0x00: "无人",
    0x01: "有人(运动)",
    0x02: "有人(静止)",
    0x03: "有人(运动+静止)",
    0x04: "底噪检测中",
    0x05: "底噪检测成功",
    0x06: "底噪检测失败",
}


def split_frames(buf):
    """按帧头帧尾切分，返回 (帧体bytes, 完整帧bytes) 列表。"""
    out = []
    while True:
        i = buf.find(HEADER)
        if i < 0:
            break
        j = buf.find(TAIL, i)
        if j < 0:
            break
        body = buf[i + 4:j]
        out.append((body, buf[i:j + 4]))
        buf = buf[j + 4:]
    return out


def parse_body(body):
    """解析帧体，返回 dict 或 None。"""
    if len(body) < 12:
        return None
    length = struct.unpack("<H", body[0:2])[0]
    dtype = body[2]                    # 0x01 工程模式 / 0x02 目标基本信息
    if body[3] != 0xAA:                # 帧内固定头部
        return None
    # 帧内数据 = body[2 : 2+length]，其中末尾 2 字节为 0x55 + 校验
    data = body[2:2 + length]
    if len(data) < 2 or data[-2] != 0x55:
        return None
    target = data[1:-2]                # 去掉帧内头 0xAA 与 尾部 0x55+校验
    chk_recv = data[-1]
    chk_calc = sum(data[:-1]) & 0xFF

    out = {"dtype": dtype, "chk_ok": chk_calc == chk_recv,
           "chk_recv": chk_recv, "chk_calc": chk_calc}
    if len(target) >= 8:
        out.update({
            "state": target[0],
            "mv_dist": struct.unpack("<H", target[1:3])[0],
            "mv_energy": target[3],
            "st_dist": struct.unpack("<H", target[4:6])[0],
            "st_energy": target[6],
        })
    return out


def main():
    label = sys.argv[1] if len(sys.argv) > 1 else ""
    seconds = 12

    ser = serial.Serial(PORT, BAUD, timeout=1)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.3)
    ser.reset_input_buffer()

    if label:
        print(f"=== 场景: {label} ===")
    print(f"读取 {seconds} 秒…\n")
    buf = b""
    t0 = time.time()
    while time.time() - t0 < seconds:
        d = ser.read(4096)
        if d:
            buf += d
    ser.close()

    frames = split_frames(buf)
    recs = [p for p in (parse_body(b) for b, _ in frames) if p]

    print(f"共 {len(frames)} 帧，成功解析 {len(recs)} 帧")
    if not recs:
        print("解析失败！")
        return
    ok = sum(1 for r in recs if r["chk_ok"])
    print(f"校验通过 {ok}/{len(recs)}")
    dtypes = {}
    for r in recs:
        dtypes[r["dtype"]] = dtypes.get(r["dtype"], 0) + 1
    print(f"数据类型分布: { {hex(k): v for k, v in dtypes.items()} }")
    print()

    print(f"  {'状态':<16}{'运动距离':>9}{'运动能量':>9}{'静止距离':>9}{'静止能量':>9}")
    print("  " + "-" * 56)
    # 去重展示（相同读数的连续帧只显示一次）
    last = None
    shown = 0
    for r in recs:
        key = (r["state"], r["mv_dist"], r["mv_energy"],
               r["st_dist"], r["st_energy"])
        if key == last:
            continue
        last = key
        name = STATE_NAME.get(r["state"], f"未知(0x{r['state']:02X})")
        print(f"  {name:<16}{r['mv_dist']:>7}cm{r['mv_energy']:>9}"
              f"{r['st_dist']:>7}cm{r['st_energy']:>9}")
        shown += 1
        if shown >= 20:
            print("  …（还有更多变化，已省略）")
            break

    # 状态统计
    print()
    cnt = {}
    for r in recs:
        cnt[r["state"]] = cnt.get(r["state"], 0) + 1
    print("状态占比：")
    for st, n in sorted(cnt.items(), key=lambda x: -x[1]):
        name = STATE_NAME.get(st, f"未知(0x{st:02X})")
        print(f"  {name:<18}{n:>5} 帧  ({100.0 * n / len(recs):.1f}%)")

    # 距离统计
    det = [r for r in recs if r["state"] != 0]
    if det:
        dists = [(r["mv_dist"] if r["mv_dist"] else r["st_dist"]) for r in det]
        dists = [d for d in dists if d > 0]
        if dists:
            print()
            print(f"检测距离范围: {min(dists)} ~ {max(dists)} cm")


if __name__ == "__main__":
    main()
