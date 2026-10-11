r"""
合成数据生成器 —— 在没有硬件的情况下打通整条数据处理链路

用途：
    生成与真实采集格式完全一致的假数据，用于：
      1. 现在就能测试采集 → 标定 → 仿真的完整流程
      2. 验证标定脚本的正确性（因为生成时用的是已知参数，
         标定脚本若能从数据中反推出这些参数，就说明流程无误）
      3. 硬件到货前先把 bug 找出来，避免现场调试

数据格式与 SmartHome_Collector.ino 的输出完全一致：
    {"ts":"...","temp":...,"hum":...,"lux":...,
     "pir1":...,"pir2":...,"radar":...,"sht_ok":...,"up":...}

物理模型（与 src/env/simulator.py 一致，但用真实单位）：
    dT/dt = -UA/C * (T_in - T_out) + Q_internal/C - Q_ac/C

    其中 C 为房间等效热容（J/K），UA 为传热系数（W/K）。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    # 生成 3 天数据
    python scripts\make_synthetic_data.py

    # 指定天数、输出目录、随机种子
    python scripts\make_synthetic_data.py --days 5 --seed 42

输出：
    data\raw\YYYY-MM-DD.csv          传感器数据
    data\raw\YYYY-MM-DD_events.csv   空调开关事件（含真实参数，供标定验证）
    data\raw\_ground_truth.json      生成时使用的真实参数
"""

import argparse
import json
import math
import random
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


# ============================================================
# 真实参数（生成时的"真值"，供标定脚本反向验证）
# ============================================================
GROUND_TRUTH = {
    # 房间热工参数
    # 取值依据：使空调日耗电落在实测的 4~5 kWh/天 量级
    "ua_w_per_k": 250.0,         # 围护结构传热系数 W/K（单间卧室，含外墙与窗户）
    "c_j_per_k": 1.8e6,          # 等效热容 J/K（约 15 m² 卧室，含家具与墙体蓄热）
    "q_internal_w": 120.0,       # 内部热增益 W（人员 + 电器）

    # 空调参数
    # 制冷量随"室温超出设定值的程度"变化，与 src/env/simulator.py 同构：
    #   q_cool = clip(gain * (T_in - T_set), 0, q_max)
    "ac_cooling_max_w": 3500.0,  # 最大制冷量 W（1.5 匹 ≈ 3500 W 制冷量）
    "ac_gain_per_k": 1400.0,     # 每高出设定温度 1°C 的制冷量增量 W/K
                                 # （= 最大制冷量 / 2.5K）
    "ac_cop": 3.0,               # 能效比（用于把制冷量换算成耗电）
    "ac_setpoint_c": 26.0,       # 设定温度 °C
    "ac_hysteresis_c": 1.0,      # 启停滞环（±1°C）

    # 环境
    "temp_out_mean_c": 28.0,     # 室外日平均温度
    "temp_out_swing_c": 5.0,     # 室外温度日波动
    "temp_in_init_c": 28.0,

    # 采样
    "sample_interval_s": 10,     # 采样周期（与采集程序一致）
}


# ============================================================
# 占用模型（与 config.py 的 occupancy_prob 同源，便于对照）
# ============================================================
def occupancy_prob(hour: int, dow: int) -> float:
    """返回某时刻在室概率。仿照 config.py 的"典型上班族"作息。"""
    weekday = dow < 5
    if weekday:
        if 0 <= hour < 7:
            return 0.95
        if 7 <= hour < 9:
            return 0.40
        if 9 <= hour < 17:
            return 0.10
        if 17 <= hour < 24:
            return 0.90
    else:
        if 0 <= hour < 8:
            return 0.95
        if 8 <= hour < 12:
            return 0.60
        if 12 <= hour < 18:
            return 0.50
        return 0.90
    return 0.5


def outdoor_temp(t: datetime) -> float:
    """室外温度：正弦日变化，最低约 05:00，最高约 15:00。"""
    h = t.hour + t.minute / 60.0
    phase = 2 * math.pi * (h - 15.0) / 24.0
    return GROUND_TRUTH["temp_out_mean_c"] + \
        GROUND_TRUTH["temp_out_swing_c"] * math.cos(phase)


def outdoor_lux(t: datetime, rng: random.Random) -> float:
    """室外光照：6-18 时按正弦变化，加入少量云层扰动。"""
    h = t.hour + t.minute / 60.0
    if not (6.0 <= h <= 18.0):
        return 0.0
    base = 20000.0 * math.sin(math.pi * (h - 6.0) / 12.0)
    cloud = rng.uniform(0.6, 1.0)      # 云层遮挡
    return max(0.0, base * cloud)


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="生成合成采集数据")
    ap.add_argument("--days", type=int, default=3, help="生成天数")
    ap.add_argument("--seed", type=int, default=2026, help="随机种子")
    ap.add_argument("--out", type=str, default=None,
                    help="输出目录，默认 data/raw")
    ap.add_argument("--start", type=str, default=None,
                    help="起始日期 YYYY-MM-DD，默认从今天起")
    ap.add_argument("--interval", type=int, default=None,
                    help="采样间隔秒数，默认用 GROUND_TRUTH")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    out_dir = Path(args.out) if args.out else (PROJECT_ROOT / "data" / "raw")
    out_dir.mkdir(parents=True, exist_ok=True)

    dt_s = args.interval or GROUND_TRUTH["sample_interval_s"]
    gt = dict(GROUND_TRUTH)
    gt["sample_interval_s"] = dt_s
    gt["seed"] = args.seed
    gt["days"] = args.days

    start = (datetime.strptime(args.start, "%Y-%m-%d")
             if args.start else datetime.now().replace(
                 hour=0, minute=0, second=0, microsecond=0))

    print("=" * 68)
    print(" 合成数据生成器")
    print("=" * 68)
    print(f"  输出目录 : {out_dir}")
    print(f"  生成天数 : {args.days}（采样间隔 {dt_s} 秒）")
    print(f"  随机种子 : {args.seed}")
    print()
    print("  使用的真实参数（用于验证标定脚本能否反推）：")
    print(f"    UA 传热系数     : {gt['ua_w_per_k']} W/K")
    print(f"    C  等效热容     : {gt['c_j_per_k']:.3e} J/K")
    print(f"    内部热增益      : {gt['q_internal_w']} W")
    print(f"    空调最大制冷量  : {gt['ac_cooling_max_w']} W")
    print(f"    空调能效比 COP  : {gt['ac_cop']}")
    print(f"    空调设定温度    : {gt['ac_setpoint_c']} °C")
    print()

    UA = gt["ua_w_per_k"]
    C = gt["c_j_per_k"]
    Q_INT = gt["q_internal_w"]
    Q_AC_MAX = gt["ac_cooling_max_w"]
    AC_GAIN_PER_K = gt["ac_gain_per_k"]
    T_SET = gt["ac_setpoint_c"]
    HYS = gt["ac_hysteresis_c"]

    # 状态
    T_in = gt["temp_in_init_c"]
    hum_in = 55.0
    ac_on = False

    total_rows = 0
    total_ac_seconds = 0
    total_seconds = 0
    total_cooling_j = 0.0        # 累计制冷量（J），用于换算耗电
    day_records = []

    for day_i in range(args.days):
        day_start = start + timedelta(days=day_i)
        day_str = day_start.strftime("%Y-%m-%d")
        data_path = out_dir / f"{day_str}.csv"
        event_path = out_dir / f"{day_str}_events.csv"

        n_steps = int(24 * 3600 / dt_s)
        rows = []
        events = []
        ac_seconds_day = 0
        cooling_j_day = 0.0

        for step in range(n_steps):
            t = day_start + timedelta(seconds=step * dt_s)
            hour, dow = t.hour, t.weekday()

            # ---- 占用（每次采样重新判定，模拟真实传感器抖动）----
            p_occ = occupancy_prob(hour, dow)
            occupied = rng.random() < p_occ
            if occupied:
                # 有人在时，多数时间在 A 区
                if rng.random() < 0.75:
                    pir1 = 1 if rng.random() < 0.35 else 0   # PIR 需移动才触发
                    pir2 = 0
                else:
                    pir1 = 0
                    pir2 = 1 if rng.random() < 0.35 else 0
                radar = 1
            else:
                pir1 = pir2 = 0
                radar = 0

            # ---- 空调控制（滞环）----
            # 认为有人时才使用空调（这符合真实使用习惯）
            if occupied:
                if T_in > T_SET + HYS:
                    ac_on = True
                elif T_in < T_SET - HYS:
                    ac_on = False
            else:
                # 无人时关空调（节能习惯）
                ac_on = False

            if ac_on:
                ac_seconds_day += dt_s

            # ---- 温度演变（显式欧拉）----
            T_out = outdoor_temp(t)
            # 传导热流
            q_ua = UA * (T_out - T_in)            # 室外传入为正
            # 内部热增益
            q_int = Q_INT if occupied else Q_INT * 0.3
            # 空调制冷：随"室温超出设定值的程度"变化，且有上限
            if ac_on:
                q_cool = min(max(AC_GAIN_PER_K * (T_in - T_SET), 0.0),
                             Q_AC_MAX)
            else:
                q_cool = 0.0
            # 净热流
            q_net = q_ua + q_int - q_cool
            T_in += q_net * dt_s / C
            # 累计制冷量（用于统计耗电）
            cooling_j_day += q_cool * dt_s
            # 加入测量噪声（真实传感器不是理想值）
            temp_meas = T_in + rng.gauss(0, 0.05)

            # ---- 湿度（简化模型）----
            hum_in += (60.0 - hum_in) * 0.001 * dt_s / 60.0
            if occupied:
                hum_in += 0.02
            hum_in = max(30.0, min(80.0, hum_in))
            hum_meas = hum_in + rng.gauss(0, 0.3)

            # ---- 光照（室外透光 + 人工照明）----
            natural = outdoor_lux(t, rng) * 0.12      # 窗户透光比
            artificial = 0.0
            if occupied and natural < 300:
                artificial = 400.0 if rng.random() < 0.8 else 800.0
            lux_meas = max(0.0, natural + artificial + rng.gauss(0, 5))

            rows.append({
                "ts": t.strftime("%Y-%m-%d %H:%M:%S"),
                "temp": round(temp_meas, 2),
                "hum": round(hum_meas, 1),
                "lux": round(lux_meas, 1),
                "pir1": pir1, "pir2": pir2, "radar": radar,
                "sht_ok": 1,
                "up": step * dt_s,
            })

            # ---- 记录空调状态变化事件 ----
            prev_on = events[-1]["ac_on"] if events else False
            if bool(ac_on) != prev_on:
                events.append({
                    "ts": t.strftime("%Y-%m-%d %H:%M:%S"),
                    "ac_on": bool(ac_on),
                    "temp_at_event": round(T_in, 2),
                })

        # ---- 写数据文件（格式与 collect_data.py 完全一致）----
        with open(data_path, "w", encoding="utf-8", newline="") as f:
            f.write("pc_time,raw,ts_device,temp_c,humidity_pct,light_lux,"
                    "pir1,pir2,radar,sht_ok,uptime_s,uptime_ms,sht_raw,note\n")
            for r in rows:
                raw = json.dumps(r, ensure_ascii=False, separators=(",", ":"))
                f.write(f'{r["ts"]},"{raw.replace(chr(34), chr(34)*2)}",'
                        f'{r["ts"]},{r["temp"]},{r["hum"]},{r["lux"]},'
                        f'{r["pir1"]},{r["pir2"]},{r["radar"]},{r["sht_ok"]},'
                        f'{r["up"]},,,\n')

        # ---- 写事件文件 ----
        with open(event_path, "w", encoding="utf-8", newline="") as f:
            f.write("ts,event,ac_on,temp_at_event\n")
            f.write(f"{day_start:%Y-%m-%d %H:%M:%S},采集开始,,\n")
            for e in events:
                label = "空调开启" if e["ac_on"] else "空调关闭"
                f.write(f'{e["ts"]},{label},{int(e["ac_on"])},'
                        f'{e["temp_at_event"]}\n')

        total_rows += len(rows)
        total_ac_seconds += ac_seconds_day
        total_seconds += n_steps * dt_s
        total_cooling_j += cooling_j_day
        day_records.append({
            "date": day_str,
            "rows": len(rows),
            "ac_on_hours": round(ac_seconds_day / 3600.0, 2),
            "data_file": str(data_path.name),
            "event_file": str(event_path.name),
        })

        print(f"  已生成 {day_str}: {len(rows)} 行, "
              f"空调运行 {ac_seconds_day/3600:.1f} 小时")

    # ---- 写真值文件 ----
    ac_kwh_per_day = (total_cooling_j / gt["ac_cop"]) / 3.6e6 / args.days
    gt_path = out_dir / "_ground_truth.json"
    with open(gt_path, "w", encoding="utf-8") as f:
        json.dump({
            "ground_truth": gt,
            "days": day_records,
            "summary": {
                "total_rows": total_rows,
                "total_ac_hours": round(total_ac_seconds / 3600.0, 2),
                "ac_duty_cycle": round(total_ac_seconds / max(total_seconds, 1), 4),
                "ac_cooling_kwh_per_day": round(
                    total_cooling_j / 3.6e6 / args.days, 3),
                "ac_electric_kwh_per_day": round(ac_kwh_per_day, 3),
                "occupancy_ratio": None,   # 由标定脚本统计
            },
        }, f, ensure_ascii=False, indent=2)

    print()
    print("=" * 68)
    print(" 生成完成")
    print("=" * 68)
    print(f"  数据文件     : {out_dir}\\YYYY-MM-DD.csv")
    print(f"  事件文件     : {out_dir}\\YYYY-MM-DD_events.csv")
    print(f"  真值文件     : {gt_path}")
    print()
    print(f"  合计 {total_rows} 行数据，空调总运行 "
          f"{total_ac_seconds/3600:.1f} 小时")
    print(f"  空调占空比   : {total_ac_seconds/max(total_seconds,1):.1%}")
    print(f"  制冷量       : {total_cooling_j/3.6e6/args.days:.2f} kWh/天（冷量）")
    print(f"  空调耗电     : {ac_kwh_per_day:.2f} kWh/天（按 COP "
          f"{gt['ac_cop']} 折算）")
    print()
    print("  提示：空调耗电应与实测的 3.5~4 kWh/天 同量级。")
    print("        若偏离较大，可调整 GROUND_TRUTH 中的 ua_w_per_k。")
    print()
    print("  下一步：运行标定脚本，验证能否反推出上面的真值")
    print("    python scripts\\calibrate_from_real.py --truth")
    print("=" * 68)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
