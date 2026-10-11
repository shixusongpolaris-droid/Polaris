r"""
数据采集脚本（电脑端）—— 读取 ESP32 串口并保存为 CSV

用途：
    挂在电脑上长时间运行，持续接收 ESP32 发来的传感器数据，
    保存为 CSV 文件，供后续标定与仿真使用。

设计要点：
    1. 数据落地为"原始 + 时间戳"两层
       · raw 列：ESP32 原样输出，不做任何转换（保留原始证据）
       · 解析列：从 raw 中提取的各字段（便于后续直接分析）
       这样即使解析逻辑有 bug，原始数据也不会丢。
    2. 电脑端时间戳与设备时间戳都记录
       设备可能未联网（时间戳退回 uptime），电脑时间作为兜底。
    3. 断线自动重连
       长时间挂机时串口可能因 USB 波动断开，脚本会自动重试。
    4. 支持事件标记
       按回车即可在日志里打一个时间戳，用于记录"我开关空调了"这类事件。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    # 自动检测串口
    python scripts\collect_data.py

    # 手动指定串口
    python scripts\collect_data.py --port COM5

    # 指定输出目录与备注
    python scripts\collect_data.py --note "第1轮采集_工作日"

操作说明（脚本运行中）：
    回车   记录一个事件到 events.csv
    Ctrl+C 停止采集（数据已实时写入，不会丢）

输出文件：
    data\raw\YYYY-MM-DD.csv          传感器数据（每行一条）
    data\raw\YYYY-MM-DD_events.csv   事件标记
"""

import argparse
import csv
import json
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("错误：未安装 pyserial。请先执行：")
    print("    pip install pyserial")
    sys.exit(1)


# ============================================================
# 串口检测
# ============================================================
def list_serial_ports():
    """列出可用串口，并标注疑似 ESP32 的端口。"""
    ports = list(list_ports.comports())
    result = []
    for p in ports:
        desc = (p.description or "") + " " + (p.manufacturer or "")
        likely = any(k in desc.upper() for k in
                     ["CH343", "CH340", "CP210", "USB-SERIAL", "USB SERIAL",
                      "SILICON LABS", "ESP"])
        result.append((p.device, p.description, likely))
    return result


def auto_detect_port():
    """自动选择串口。优先选疑似 USB 转串口的端口。"""
    ports = list_serial_ports()
    if not ports:
        return None
    likely = [d for d, _, lk in ports if lk]
    if likely:
        return likely[0]
    return ports[0][0]


# ============================================================
# 数据解析
# ============================================================
JSON_RE = re.compile(r"\{.*\}")

# 兼容 HardwareTest 的输出格式：[   12345 ms] PIR1=0  PIR2=1  Radar=0
LEGACY_PIN_RE = re.compile(
    r"\[\s*(\d+)\s*ms\]\s*PIR1=(\d)\s+PIR2=(\d)\s+Radar=(\d)")
# 兼容 HardwareTest 的 SHT31 行：  [SHT31] <原始文本>
LEGACY_SHT_RE = re.compile(r"\[SHT31\]\s*(.+)$")


def parse_line(line: str) -> dict:
    """解析一行串口输出，返回标准字段字典。

    无法解析时返回空字典（该行仍会以 raw 形式保存）。
    """
    line = line.strip()
    if not line:
        return {}

    # ---- 格式 1：JSON（SmartHome_Collector.ino 的输出）----
    m = JSON_RE.search(line)
    if m:
        try:
            obj = json.loads(m.group(0))
            return {
                "ts_device": obj.get("ts"),
                "temp_c": obj.get("temp"),
                "humidity_pct": obj.get("hum"),
                "light_lux": obj.get("lux"),
                "pir1": obj.get("pir1"),
                "pir2": obj.get("pir2"),
                "radar": obj.get("radar"),
                "sht_ok": obj.get("sht_ok"),
                "uptime_s": obj.get("up"),
            }
        except json.JSONDecodeError:
            pass

    # ---- 格式 2：HardwareTest 的引脚行 ----
    m = LEGACY_PIN_RE.search(line)
    if m:
        return {
            "uptime_ms": int(m.group(1)),
            "pir1": int(m.group(2)),
            "pir2": int(m.group(3)),
            "radar": int(m.group(4)),
        }

    # ---- 格式 3：HardwareTest 的 SHT31 行（保留原文，不猜测字段）----
    m = LEGACY_SHT_RE.search(line)
    if m:
        return {"sht_raw": m.group(1).strip()}

    # ---- 其他：芯片信息、提示信息等 ----
    return {"note": line}


# ============================================================
# 输出
# ============================================================
CSV_FIELDS = ["pc_time", "raw",
              "ts_device", "temp_c", "humidity_pct", "light_lux",
              "pir1", "pir2", "radar", "sht_ok", "uptime_s", "uptime_ms",
              "sht_raw", "note"]


class Recorder:
    """负责把数据实时写入 CSV。"""

    def __init__(self, out_dir: Path, note: str = ""):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.note = note
        self.rows = 0
        self.started = time.time()

        day = datetime.now().strftime("%Y-%m-%d")
        self.data_path = out_dir / f"{day}.csv"
        self.event_path = out_dir / f"{day}_events.csv"

        self._fh = open(self.data_path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS,
                                      extrasaction="ignore")
        if self.data_path.stat().st_size == 0:
            self._writer.writeheader()

        self._efh = open(self.event_path, "a", newline="", encoding="utf-8")
        self._ewriter = csv.writer(self._efh)
        if self.event_path.stat().st_size == 0:
            self._ewriter.writerow(["pc_time", "event"])
            self._efh.flush()

        # 记录一次采集开始事件
        self.mark(f"采集开始 {self.note}".strip())

    def write(self, line: str):
        parsed = parse_line(line)
        row = {k: "" for k in CSV_FIELDS}
        row["pc_time"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        row["raw"] = line
        for k, v in parsed.items():
            if k in row:
                row[k] = v
        self._writer.writerow(row)
        self._fh.flush()          # 实时落盘，断电也不丢
        self.rows += 1

    def mark(self, event: str):
        self._ewriter.writerow(
            [datetime.now().strftime("%Y-%m-%d %H:%M:%S"), event])
        self._efh.flush()

    def close(self):
        self.mark("采集结束")
        self._fh.close()
        self._efh.close()


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="ESP32 传感器数据采集")
    ap.add_argument("--port", type=str, default=None,
                    help="串口号（如 COM5）。不填则自动检测")
    ap.add_argument("--baud", type=int, default=115200, help="波特率")
    ap.add_argument("--out", type=str, default=None,
                    help="输出目录，默认 data/raw")
    ap.add_argument("--note", type=str, default="", help="本轮采集备注")
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else (PROJECT_ROOT / "data" / "raw")
    port = args.port or auto_detect_port()

    print("=" * 66)
    print(" 传感器数据采集")
    print("=" * 66)
    print(f"  项目目录 : {PROJECT_ROOT}")
    print(f"  输出目录 : {out_dir}")

    ports = list_serial_ports()
    if not ports:
        print("\n  [错误] 未检测到任何串口。请检查：")
        print("    1. 板子是否已插上（Type-C 线接电脑）")
        print("    2. CH343 驱动是否已安装")
        print("    3. 设备管理器里能否看到端口")
        return 1

    print("\n  可用串口：")
    for dev, desc, likely in ports:
        tag = "  <- 疑似开发板" if likely else ""
        print(f"    {dev:<8} {desc}{tag}")

    if port is None:
        print("\n  [错误] 无法自动选择串口，请用 --port 手动指定。")
        return 1

    print(f"\n  使用串口 : {port}  波特率 {args.baud}")
    print()
    print("  操作说明：")
    print("    · 按【回车】记录一个事件（用于标记开关空调）")
    print("    · 按 Ctrl+C 停止采集")
    print()

    recorder = Recorder(out_dir, args.note)
    print(f"  数据文件 : {recorder.data_path}")
    print(f"  事件文件 : {recorder.event_path}")

    # ---- 键盘监听线程：回车打标记 ----
    stop_flag = threading.Event()

    def key_listener():
        while not stop_flag.is_set():
            try:
                input()
                recorder.mark("用户标记事件")
                print(f"    [{datetime.now():%H:%M:%S}] 已记录事件标记")
            except (EOFError, KeyboardInterrupt):
                break

    threading.Thread(target=key_listener, daemon=True).start()

    # ---- 采集主循环（含断线重连）----
    last_print = 0.0
    try:
        while True:
            try:
                with serial.Serial(port, args.baud, timeout=2) as ser:
                    print(f"\n  [{datetime.now():%H:%M:%S}] 串口已打开，开始接收数据…\n")
                    while True:
                        raw = ser.readline()
                        if not raw:
                            continue
                        try:
                            line = raw.decode("utf-8", errors="replace")
                        except Exception:
                            line = repr(raw)
                        line = line.rstrip("\r\n")
                        if not line:
                            continue

                        recorder.write(line)

                        # 每 20 秒打印一次进度
                        now = time.time()
                        if now - last_print >= 20:
                            last_print = now
                            elapsed = (now - recorder.started) / 60
                            print(f"    [{datetime.now():%H:%M:%S}] "
                                  f"已记录 {recorder.rows} 行，"
                                  f"运行 {elapsed:.1f} 分钟")
            except serial.SerialException as e:
                print(f"\n  [警告] 串口异常：{e}")
                print("  5 秒后尝试重连…（数据已保存，不会丢失）")
                time.sleep(5)
            except KeyboardInterrupt:
                raise
    except KeyboardInterrupt:
        print("\n\n  收到停止信号，正在收尾…")

    stop_flag.set()
    recorder.close()
    print(f"\n  采集结束。共记录 {recorder.rows} 行数据。")
    print(f"  数据文件 : {recorder.data_path}")
    print(f"  事件文件 : {recorder.event_path}")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
