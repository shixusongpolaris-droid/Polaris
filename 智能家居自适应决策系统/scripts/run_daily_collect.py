# -*- coding: utf-8 -*-
"""每日采集调度入口（由 Windows 计划任务调用）。

为什么不用 .bat：
    cmd.exe 以 OEM 代码页（中文系统为 GBK）读取批处理文件，且对
    LF 行尾不兼容；项目路径含中文，编码问题反复导致批处理静默失败
    （实测退出码 255、日志与数据均未生成）。
    Python 原生支持 UTF-8 路径与日期运算，因此把调度逻辑放在这里，
    计划任务只需调用 python.exe + 本脚本，避免批处理的编码陷阱。

职责：
    1. 判断是否已超过采集截止日期，超过则直接退出
    2. 调用 collect_data.py 采集指定时长
    3. 把运行信息写入 data/logs/collect_<日期>.log

用法：
    python run_daily_collect.py [--minutes 1420] [--port COM3]
                                [--stop-after 2026-10-16]
"""

import argparse
import subprocess
import sys
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PY_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
COLLECT = PROJECT_ROOT / "scripts" / "collect_data.py"
LOG_DIR = PROJECT_ROOT / "data" / "logs"


def log_line(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(text + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="每日采集调度入口")
    ap.add_argument("--minutes", type=float, default=1420,
                    help="采集时长（分钟），默认 1420（约 23.7 小时）")
    ap.add_argument("--port", default="COM3", help="串口")
    ap.add_argument("--stop-after", default="2026-10-16",
                    help="采集截止日期（含当天）；超过则不再采集")
    ap.add_argument("--prefix", default="daily_", help="输出文件名前缀")
    args = ap.parse_args()

    today = date.today()
    try:
        cutoff = datetime.strptime(args.stop_after, "%Y-%m-%d").date()
    except ValueError:
        print(f"[错误] --stop-after 格式应为 YYYY-MM-DD，收到 {args.stop_after}")
        return 2

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"collect_{today:%Y-%m-%d}.log"

    # ---- 到期检查 ----
    if today > cutoff:
        msg = (f"[{datetime.now():%Y-%m-%d %H:%M:%S}] "
               f"已超过采集截止日期 {cutoff}（今天 {today}），本次不采集。")
        log_line(logfile, msg)
        print(msg)
        return 0

    # ---- 前置检查 ----
    if not PY_EXE.exists():
        msg = f"[错误] 找不到 Python 环境: {PY_EXE}"
        log_line(logfile, msg)
        print(msg)
        return 1
    if not COLLECT.exists():
        msg = f"[错误] 找不到采集脚本: {COLLECT}"
        log_line(logfile, msg)
        print(msg)
        return 1

    start = datetime.now()
    log_line(logfile, "=" * 60)
    log_line(logfile, f"[{start:%Y-%m-%d %H:%M:%S}] 开始采集 "
                       f"时长={args.minutes:g}分钟 串口={args.port} "
                       f"截止={cutoff}")
    print(f"[{start:%H:%M:%S}] 开始采集（{args.minutes:g} 分钟，{args.port}）")

    cmd = [str(PY_EXE), str(COLLECT),
           "--port", args.port,
           "--no-keyboard",
           "--minutes", str(args.minutes),
           "--prefix", args.prefix,
           "--note", "每日定时采集"]

    rc = 1
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        rc = proc.returncode
        if proc.stdout:
            log_line(logfile, "--- stdout ---")
            log_line(logfile, proc.stdout.rstrip())
        if proc.stderr:
            log_line(logfile, "--- stderr ---")
            log_line(logfile, proc.stderr.rstrip())
    except Exception as e:
        log_line(logfile, f"[异常] {type(e).__name__}: {e}")
        print(f"[异常] {e}")

    end = datetime.now()
    dur = (end - start).total_seconds() / 60
    log_line(logfile, f"[{end:%Y-%m-%d %H:%M:%S}] 采集结束 "
                       f"耗时={dur:.1f}分钟 退出码={rc}")
    print(f"[{end:%H:%M:%S}] 采集结束，耗时 {dur:.1f} 分钟，退出码 {rc}")
    return rc


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    raise SystemExit(main())
