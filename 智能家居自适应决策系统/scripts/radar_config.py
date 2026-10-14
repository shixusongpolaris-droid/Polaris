# -*- coding: utf-8 -*-
"""HLK-LD2410C 参数读取与配置工具。

协议要点（官方《LD2410C 串口通信协议》）：
  帧格式：帧头 F4F3F2F1 | 长度(2B,小端) | 数据 | 帧尾 F8F7F6F5
  命令帧数据段： [cmd_lo cmd_hi 0x00 0x00] + 参数
    例：读取参数   cmd=0x0002 -> 02 00 00 00
    例：使能配置   cmd=0x00FF -> FF 00 00 00
    例：结束配置   cmd=0x00FE -> FE 00 00 00
    例：工程模式   cmd=0x0062 -> 62 00 00 00
  应答帧数据段： [cmd_lo cmd_hi 0x00 0x00] + 状态(1B) + 数据

  读取参数应答（0x0002）数据布局：
    [0:2]   cmd = 02 00
    [2:4]   0x00 0x00
    [4]     status (0x00 = 成功)
    [5:7]   最大运动距离门 (2B 小端)
    [7:9]   最大静止距离门 (2B 小端)
    [9]     无人延时 (秒)
    [10:26] 运动灵敏度，16 个门各 1 字节
    [26:42] 静止灵敏度，16 个门各 1 字节
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

CMD_ENABLE_CFG = 0x00FF
CMD_END_CFG = 0x00FE
CMD_READ_PARAMS = 0x0002
CMD_SET_MAX_DIST = 0x0060
CMD_SET_GATE_SENS = 0x0061
CMD_ENG_MODE = 0x0062


def build_frame(cmd: int, params: bytes = b"") -> bytes:
    payload = struct.pack("<HH", cmd, 0) + params
    length = len(payload)
    return HEADER + struct.pack("<H", length) + payload + TAIL


def read_frames(ser, seconds=2.0):
    """读取并切分出所有完整帧。"""
    buf = b""
    t0 = time.time()
    while time.time() - t0 < seconds:
        d = ser.read(4096)
        if d:
            buf += d
    frames = []
    while True:
        i = buf.find(HEADER)
        if i < 0:
            break
        j = buf.find(TAIL, i)
        if j < 0:
            break
        body = buf[i + 4:j]
        frames.append(body)
        buf = buf[j + 4:]
    return frames


def parse_status_frame(body: bytes):
    """解析应答帧，返回 (cmd, status, data)。

    帧结构（去掉帧头帧尾后的 body）：
        [0:2]   长度
        [2:4]   命令字 (小端)
        [4:6]   0x0000
        [6]     状态 (0x00 = 成功)
        [7:]    数据
    """
    if len(body) < 7:
        return None
    cmd = struct.unpack("<H", body[2:4])[0]
    status = body[6]
    return cmd, status, body[7:]


def parse_target_frame(body: bytes):
    """解析目标数据帧，返回检测结果。

    帧结构（去掉帧头帧尾后的 body）：
        [0:2]   长度 (0x000D = 13)
        [2:4]   数据类型 (0x0202)
        [4]     目标状态: 0x00 无人 / 0x01 运动 / 0x02 静止 / 0x03 运动+静止
        [5:7]   运动目标距离 (cm, 小端)
        [7]     运动目标能量
        [8:10]  静止目标距离 (cm, 小端)
        [10]    静止目标能量
        [11:13] 探测距离 (cm, 小端)
    """
    if len(body) < 13:
        return None
    ftype = struct.unpack("<H", body[2:4])[0]
    if ftype != 0x0202:
        return None
    state = body[4]
    mv_dist = struct.unpack("<H", body[5:7])[0]
    mv_energy = body[7]
    st_dist = struct.unpack("<H", body[8:10])[0]
    st_energy = body[10]
    det_dist = struct.unpack("<H", body[11:13])[0]
    return {
        "state": state, "mv_dist": mv_dist, "mv_energy": mv_energy,
        "st_dist": st_dist, "st_energy": st_energy, "det_dist": det_dist,
    }


STATE_NAME = {0x00: "无人", 0x01: "有人运动", 0x02: "有人静止",
              0x03: "有人(运动+静止)"}


def main():
    print(f"打开 {PORT} @ {BAUD} …")
    ser = serial.Serial(PORT, BAUD, timeout=1)
    ser.dtr = False
    ser.rts = False
    time.sleep(0.3)
    ser.reset_input_buffer()

    # ---------- 1. 观察实时检测状态 ----------
    print("\n=== 1. 实时检测状态（观察 6 秒）===")
    frames = read_frames(ser, 6.0)
    seen = {}
    for b in frames:
        r = parse_target_frame(b)
        if r:
            key = (r["state"], r["det_dist"])
            seen[key] = seen.get(key, 0) + 1
    if seen:
        print(f"  {'状态':<16}{'检测距离':>10}{'出现次数':>10}")
        for (st, dd), n in sorted(seen.items(), key=lambda x: -x[1]):
            name = STATE_NAME.get(st, f"未知(0x{st:02X})")
            print(f"  {name:<16}{dd:>8} cm{n:>10}")
        states = set(s for s, _ in seen)
        if states == {0x00}:
            print("\n  -> 当前检测到【无人】。雷达工作正常。")
        else:
            print(f"\n  -> 检测到人体（状态 {[hex(s) for s in states]}）")
    else:
        print("  未解析到目标数据帧")

    # ---------- 2. 进入配置模式并读取参数 ----------
    print("\n=== 2. 读取雷达当前参数 ===")
    ser.reset_input_buffer()
    ser.write(build_frame(CMD_ENABLE_CFG))
    time.sleep(0.3)
    ser.reset_input_buffer()
    read_frames(ser, 0.5)

    ser.write(build_frame(CMD_READ_PARAMS))
    time.sleep(0.5)
    frames = read_frames(ser, 1.5)

    got = False
    for b in frames:
        r = parse_status_frame(b)
        if r and r[0] == CMD_READ_PARAMS:
            cmd, status, data = r
            print(f"  应答状态: {'成功' if status == 0 else f'失败(0x{status:02X})'}")
            if status == 0 and len(data) >= 42:
                max_mv = struct.unpack("<H", data[0:2])[0]
                max_st = struct.unpack("<H", data[2:4])[0]
                no_delay = data[4]
                mv_sens = list(data[5:21])
                st_sens = list(data[21:37])
                print(f"  最大运动距离门 : {max_mv}  (约 {max_mv * 0.75:.2f} m)")
                print(f"  最大静止距离门 : {max_st}  (约 {max_st * 0.75:.2f} m)")
                print(f"  无人延时       : {no_delay} 秒")
                print(f"  运动灵敏度(门0~15): {mv_sens}")
                print(f"  静止灵敏度(门0~15): {st_sens}")
                got = True
                # 分析：哪些门是开启的
                print("\n  各距离门灵敏度（门 N 对应距离 N×0.75m）：")
                print(f"    {'门':>3}{'距离':>9}{'运动灵敏':>10}{'静止灵敏':>10}")
                for g in range(16):
                    m = mv_sens[g] if g < len(mv_sens) else 0
                    s = st_sens[g] if g < len(st_sens) else 0
                    flag = "" if (m or s) else "   <- 已关闭"
                    print(f"    {g:>3}{g * 0.75:>8.2f}m{m:>10}{s:>10}{flag}")
            break

    if not got:
        print("  未收到参数应答")

    # ---------- 3. 退出配置模式 ----------
    ser.write(build_frame(CMD_END_CFG))
    time.sleep(0.2)
    ser.close()
    print("\n已退出配置模式，串口关闭。")


if __name__ == "__main__":
    main()
