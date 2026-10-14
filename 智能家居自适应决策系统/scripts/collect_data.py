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
# 室外天气（热模型标定的边界条件）
# ============================================================
class WeatherFetcher:
    """后台线程定时抓取室外天气（Open-Meteo，免费无需密钥）。

    采集数据里带上室外温度/湿度后，热模型标定就不必再去查历史天气，
    而且能保证室内外数据时间对齐。
    抓取失败不影响采集，只是对应字段留空。
    """

    def __init__(self, interval_s=900):
        self.interval_s = interval_s
        self.temp = ""
        self.hum = ""
        self.local_time = ""
        self.last_ok = None
        self._stop = threading.Event()
        self._thread = None

    def _loop(self):
        # 延迟几秒再抓，避免与串口初始化抢资源
        self._stop.wait(3)
        while not self._stop.is_set():
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                from fetch_weather import fetch
                w = fetch(timeout=12)
                if w and "error" not in w and w.get("outdoor_temp_c") is not None:
                    self.temp = w["outdoor_temp_c"]
                    self.hum = w.get("outdoor_humidity_pct", "")
                    self.local_time = w.get("local_time", "")
                    self.last_ok = datetime.now()
            except Exception:
                pass          # 网络问题不影响采集
            self._stop.wait(self.interval_s)

    def start(self):
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()


# ============================================================
# 输出
# ============================================================
CSV_FIELDS = ["pc_time", "raw",
              "ts_device", "temp_c", "humidity_pct", "light_lux",
              "pir1", "pir2", "radar", "sht_ok", "uptime_s", "uptime_ms",
              "outdoor_temp_c", "outdoor_humidity_pct",
              "sht_raw", "note"]


class Recorder:
    """负责把数据实时写入 CSV。

    按自然日自动分割文件：长时间连续采集（如 3 天）时，
    跨零点会自动切换到当天的文件，避免全部堆在第一个文件里。
    """

    def __init__(self, out_dir: Path, note: str = "", prefix: str = "",
                 weather=None):
        self.out_dir = out_dir
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.note = note
        # prefix 用于区分不同批次的采集（例如每日定时任务用 "daily_"），
        # 避免同一天多次运行写进同一个文件而混在一起。
        self.prefix = prefix
        # weather: WeatherFetcher 实例，用于给每行附加室外温度/湿度
        self.weather = weather
        self.rows = 0
        self.started = time.time()
        self._day = None
        self._fh = None
        self._efh = None
        self._writer = None
        self._ewriter = None
        self._open_for_day(datetime.now().strftime("%Y-%m-%d"))
        # 记录一次采集开始事件
        self.mark(f"采集开始 {self.note}".strip())

    def _open_for_day(self, day: str):
        """打开（或切换到）指定日期对应的数据/事件文件。"""
        if self._day == day and self._fh is not None:
            return
        # 关闭上一个文件
        if self._fh is not None:
            self._fh.close()
        if self._efh is not None:
            self._efh.close()

        self._day = day
        base = f"{self.prefix}{day}" if self.prefix else day
        self.data_path = self.out_dir / f"{base}.csv"
        self.event_path = self.out_dir / f"{base}_events.csv"

        self._fh = open(self.data_path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=CSV_FIELDS,
                                      extrasaction="ignore")
        if self.data_path.stat().st_size == 0:
            self._writer.writeheader()
            self._fh.flush()

        self._efh = open(self.event_path, "a", newline="", encoding="utf-8")
        self._ewriter = csv.writer(self._efh)
        if self.event_path.stat().st_size == 0:
            self._ewriter.writerow(["pc_time", "event"])
            self._efh.flush()

    def write(self, line: str):
        now = datetime.now()
        day = now.strftime("%Y-%m-%d")
        if day != self._day:
            # 跨零点：切换文件，并在新文件里记录一次"跨日"事件
            self._open_for_day(day)
            self._ewriter.writerow([now.strftime("%Y-%m-%d %H:%M:%S"),
                                    "跨日，文件切换"])
            self._efh.flush()

        parsed = parse_line(line)

        # 跳过"设备提示/诊断"这类非数据行，避免污染数据 CSV。
        # 判据：解析结果里除了 note 之外没有任何传感器字段。
        DATA_KEYS = ("temp_c", "humidity_pct", "light_lux", "pir1", "pir2",
                     "radar", "sht_raw", "uptime_ms")
        if parsed and all(k not in parsed for k in DATA_KEYS):
            txt = str(parsed.get("note", ""))[:120]
            if txt:
                self._ewriter.writerow([now.strftime("%Y-%m-%d %H:%M:%S"),
                                        f"设备消息: {txt}"])
                self._efh.flush()
            return

        row = {k: "" for k in CSV_FIELDS}
        row["pc_time"] = now.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        row["raw"] = line
        for k, v in parsed.items():
            if k in row:
                row[k] = v
        # 附加室外天气（若抓取成功）
        if self.weather is not None:
            row["outdoor_temp_c"] = self.weather.temp
            row["outdoor_humidity_pct"] = self.weather.hum
        self._writer.writerow(row)
        self._fh.flush()          # 实时落盘，断电也不丢
        self.rows += 1

    def mark(self, event: str):
        now = datetime.now()
        day = now.strftime("%Y-%m-%d")
        if day != self._day:
            self._open_for_day(day)
        self._ewriter.writerow([now.strftime("%Y-%m-%d %H:%M:%S"), event])
        self._efh.flush()

    def close(self):
        self.mark("采集结束")
        if self._fh is not None:
            self._fh.close()
        if self._efh is not None:
            self._efh.close()
        self._fh = None
        self._efh = None


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
    ap.add_argument("--no-keyboard", action="store_true",
                    help="禁用键盘事件标记（后台运行、或无法交互时使用）")
    ap.add_argument("--minutes", type=float, default=0,
                    help="采集时长（分钟）；0 表示一直采到手动停止")
    ap.add_argument("--prefix", type=str, default="",
                    help="输出文件名前缀（如 daily_），用于区分不同批次采集")
    ap.add_argument("--no-weather", action="store_true",
                    help="禁用室外天气抓取（默认开启，写进 outdoor_temp_c 列）")
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

    # 启动室外天气抓取（后台线程，失败不影响采集）
    weather = None
    if not args.no_weather:
        weather = WeatherFetcher(interval_s=900)
        weather.start()
        print("  室外天气 : 已启动（Open-Meteo，每 15 分钟更新一次）")
    else:
        print("  室外天气 : 已禁用（--no-weather）")

    print()
    print("  操作说明：")
    if args.no_keyboard:
        print("    · 已禁用键盘事件标记（--no-keyboard）")
    else:
        print("    · 按【回车】记录一个事件（用于标记开关空调）")
    if args.minutes > 0:
        print(f"    · 采集 {args.minutes:g} 分钟后自动停止")
    else:
        print("    · 按 Ctrl+C 停止采集")
    print()

    recorder = Recorder(out_dir, args.note, prefix=args.prefix,
                        weather=weather)
    print(f"  数据文件 : {recorder.data_path}")
    print(f"  事件文件 : {recorder.event_path}")

    # ---- 键盘监听线程：回车打标记 ----
    # 注意：后台运行或无法交互时必须用 --no-keyboard 关闭，
    # 否则 input() 在无标准输入的环境下会抛异常。
    stop_flag = threading.Event()

    if not args.no_keyboard:
        def key_listener():
            while not stop_flag.is_set():
                try:
                    input()
                    recorder.mark("用户标记事件")
                    print(f"    [{datetime.now():%H:%M:%S}] 已记录事件标记")
                except (EOFError, KeyboardInterrupt, OSError):
                    break

        threading.Thread(target=key_listener, daemon=True).start()

    # ---- 采集主循环（含断线重连）----
    last_print = 0.0
    deadline = (time.time() + args.minutes * 60) if args.minutes > 0 else None
    try:
        while True:
            if deadline is not None and time.time() >= deadline:
                print(f"\n  已到设定时长（{args.minutes:g} 分钟），停止采集。")
                break
            try:
                with serial.Serial(port, args.baud, timeout=2) as ser:
                    print(f"\n  [{datetime.now():%H:%M:%S}] 串口已打开，开始接收数据…\n")
                    while True:
                        if deadline is not None and time.time() >= deadline:
                            break
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
    if weather is not None:
        weather.stop()
    print(f"\n  采集结束。共记录 {recorder.rows} 行数据。")
    print(f"  数据文件 : {recorder.data_path}")
    print(f"  事件文件 : {recorder.event_path}")
    if weather is not None and weather.temp != "":
        print(f"  室外天气 : {weather.temp} °C / {weather.hum} % "
              f"（更新于 {weather.local_time}）")
    print("=" * 66)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
