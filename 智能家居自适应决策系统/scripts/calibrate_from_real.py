r"""
数据标定脚本 —— 从实测（或合成）数据反推仿真模型参数

用途：
    读取采集到的传感器数据，标定出两类参数，供仿真环境使用：

      1. 围护结构传热系数 UA（W/K）
         · 从"空调运行且室温稳定"的时段反推
         · 稳定时制冷量 = 传热量 + 内部热增益，可解出 UA

      2. 房间等效热容 C（J/K）
         · 从"空调关闭时的自由升温/降温曲线"拟合
         · 一阶系统的指数弛豫，时间常数 τ = C / UA

      3. 真实占用概率表（7×24）
         · 直接统计每个时段的在室比例
         · 用于替换仿真中内置的"典型上班族"假设

设计说明：
    标定结果同时输出"原始拟合值"与"建议写入配置的值"，
    并给出拟合优度，便于判断结果是否可信。

运行方式：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate

    # 标定（自动读取 data/raw 下所有 CSV）
    python scripts\calibrate_from_real.py

    # 与合成数据的真值对比（验证标定脚本正确性）
    python scripts\calibrate_from_real.py --truth

    # 指定数据目录
    python scripts\calibrate_from_real.py --dir data\raw

输出：
    data\calibrated_params.json     标定结果
    data\occupancy_table.csv        真实占用概率表
"""

import argparse
import csv
import json
import math
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

import numpy as np

# ============================================================
# 常量（与仿真环境 / 合成数据生成器对应）
# ============================================================
DT_SECONDS = 10.0                     # 采样间隔（从数据里也会自动检测）

# 空调参数（对应 1.5 匹大金中央空调，只开一个房间）
AC_COOLING_MAX_W = 3500.0             # 最大制冷量 W（1.5 匹 ≈ 3500 W 制冷量）
AC_GAIN_PER_K = 1400.0                # 每高出设定温度 1°C 的制冷量增量 W/K
AC_COP = 3.0                          # 能效比
AC_SETPOINT_C = 26.0                  # 设定温度 °C
AC_HYSTERESIS_C = 1.0                 # 启停滞环 ±°C

Q_INTERNAL_W = 120.0                  # 有人时内部热增益（W）
Q_INTERNAL_EMPTY_RATIO = 0.3          # 无人时的热增益比例

# 模型内部使用的时间步长（仿真环境用 15 分钟）
MODEL_DT_MIN = 15.0


# ============================================================
# 数据读取
# ============================================================
def load_records(data_dir: Path, pattern: str = "*.csv"):
    """读取所有数据 CSV，返回按时间排序的记录列表。"""
    records = []
    files = sorted(p for p in data_dir.glob(pattern)
                   if not p.name.endswith("_events.csv"))
    if not files:
        return records, files

    for f in files:
        with open(f, encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                ts = (row.get("ts_device") or row.get("pc_time") or "").strip()
                if not ts:
                    continue
                try:
                    t = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                try:
                    temp = float(row["temp_c"])
                except (KeyError, ValueError, TypeError):
                    continue
                if not (-20 < temp < 60):
                    continue

                def fnum(key):
                    v = row.get(key, "")
                    try:
                        return float(v)
                    except (ValueError, TypeError):
                        return None

                records.append({
                    "t": t,
                    "temp": temp,
                    "hum": fnum("humidity_pct"),
                    "lux": fnum("light_lux"),
                    "pir1": fnum("pir1"),
                    "pir2": fnum("pir2"),
                    "radar": fnum("radar"),
                })

    records.sort(key=lambda r: r["t"])
    return records, files


def load_ac_events(data_dir: Path):
    """读取空调开关事件。返回 [(datetime, is_on), ...]"""
    events = []
    for f in sorted(data_dir.glob("*_events.csv")):
        with open(f, encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                ev = (row.get("event") or "").strip()
                ts = (row.get("ts") or "").strip()
                if "空调" not in ev or not ts:
                    continue
                try:
                    t = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                is_on = "开启" in ev
                events.append((t, is_on))
    events.sort(key=lambda x: x[0])
    return events


def build_ac_state_series(records, events):
    """根据事件生成每条记录的空调开关状态。

    注意：合成数据的事件文件里已含 ac_on 列，但真实采集时
    需要用户手动标记，因此这里统一按事件时间推导。
    """
    if not events:
        return [None] * len(records)

    state = []
    ei = 0
    cur = False
    for r in records:
        while ei < len(events) and events[ei][0] <= r["t"]:
            cur = events[ei][1]
            ei += 1
        state.append(cur)
    return state


# ============================================================
# 计算室外温度（用日变化模型估计，真实项目应接入天气 API）
# ============================================================
def estimate_outdoor_temp(t: datetime, mean=28.0, swing=5.0):
    h = t.hour + t.minute / 60.0
    phase = 2 * math.pi * (h - 15.0) / 24.0
    return mean + swing * math.cos(phase)


# ============================================================
# 标定方法说明
# ============================================================
# 采用【能量平衡积分回归】，而不是"找稳态区间反推"。
#
# 原因：
#   变频空调在室温接近设定温度时制冷量会降到接近零，
#   室温一直在缓慢漂移，**不存在真正的稳态区间**。
#   最初尝试用"温度变化小于阈值"的区间做稳态反推，
#   结果 UA 被高估到真值的近 2.5 倍（见本次调试记录）。
#
# 正确做法：对热平衡方程在时间上积分，避免求导放大噪声：
#
#       C·(T_{k+1} − T_k) = −UA·∫(T_in − T_out)dt
#                           + Q_int·∫1{有人}dt
#                           − Q_ac·∫1{空调开}dt
#
#   令 y = T_{k+1} − T_k，构造三个自变量：
#       x1 = ∫(T_out − T_in)dt      （向室内的净传热积分）
#       x2 = ∫1{有人}dt             （内部热增益积分）
#       x3 = ∫1{空调开}dt           （制冷量积分）
#
#   则  y = (UA/C)·x1 + (Q_int/C)·x2 − (Q_ac/C)·x3
#   即 y = a1·x1 + a2·x2 + a3·x3
#
#   由三系数反解：
#       C   = −Q_ac / a3
#       UA  = a1 · C
#       Q_int = a2 · C
#
# 优点：
#   · 不需求导，对采样噪声稳健
#   · 不需要分段，可直接用全部数据
#   · 能同时标定 UA、C、内部热增益三个参数
# ============================================================


def calibrate_energy_balance(records, ac_state, occupied, dt_s,
                             q_ac_max=AC_COOLING_MAX_W,
                             ac_gain_per_k=AC_GAIN_PER_K,
                             ac_setpoint=AC_SETPOINT_C,
                             ac_hysteresis=AC_HYSTERESIS_C):
    """标定 UA 与 C（前向仿真 + 参数优化）。

    方法说明：
        采用【前向仿真 + 数值优化】而非解析回归。原因：
          1. 空调制冷量随室温变化（非线性），且受设定温度滞环控制，
             空调状态本身就是温度的反馈结果 —— 解析回归的条件难以满足；
          2. 单步温度变化（10 秒约 5 mK）小于传感器噪声（约 5 mK），
             逐点回归的信噪比极低（实测 R² 仅 0.0008）。

        做法：
          用候选 (UA, C) 模拟整条温度轨迹，用 RMSE 衡量拟合误差，
          在参数空间上做网格搜索 + 局部优化（Nelder-Mead）。

    注意：
        空调的开关状态**直接采用实测记录的事件**，而不是由模型自行判断。
        这样避免"模型判断的开关状态与实际不符"引入的额外误差。

    返回：(ua, c, q_int, rmse, n, note)
    """
    from scipy.optimize import minimize

    n = len(records)
    if n < 500:
        return None, None, None, None, 0, "样本过少"

    t_arr = np.array([r["t"].timestamp() for r in records])
    T_meas = np.array([r["temp"] for r in records])
    T_out = np.array([estimate_outdoor_temp(r["t"]) for r in records])
    ac = np.array([1.0 if s is True else 0.0 for s in ac_state])
    occ = np.array([1.0 if o else 0.0 for o in occupied])

    # 为提速，对数据进行抽稀（保留足够的时间分辨率）
    stride = max(1, int(len(T_meas) / 20000))
    if stride > 1:
        t_arr = t_arr[::stride]
        T_meas = T_meas[::stride]
        T_out = T_out[::stride]
        ac = ac[::stride]
        occ = occ[::stride]
    dt = dt_s * stride

    def simulate(ua, c, q_int_base):
        """给定参数，模拟温度轨迹，返回 RMSE。"""
        T = T_meas[0]
        err2 = 0.0
        m = len(T_meas)
        for i in range(1, m):
            q_ua = ua * (T_out[i - 1] - T)
            q_int = q_int_base if occ[i - 1] > 0 else q_int_base * 0.3
            if ac[i - 1] > 0:
                q_cool = min(max(ac_gain_per_k * (T - ac_setpoint), 0.0),
                             q_ac_max)
            else:
                q_cool = 0.0
            T = T + (q_ua + q_int - q_cool) * dt / c
            d = T - T_meas[i]
            err2 += d * d
        return math.sqrt(err2 / (m - 1))

    # ---- 网格搜索给初值 ----
    best = (1e9, None, None)
    for ua in [60, 100, 150, 220, 320]:
        for c in [0.8e6, 1.2e6, 1.8e6, 2.6e6, 3.6e6]:
            r = simulate(ua, c, Q_INTERNAL_W)
            if r < best[0]:
                best = (r, ua, c)

    rmse0, ua0, c0 = best

    # ---- 局部优化（Nelder-Mead，无梯度）----
    def obj(x):
        ua_, c_ = x
        if ua_ <= 1 or c_ <= 1e4:
            return 1e9
        return simulate(ua_, c_, Q_INTERNAL_W)

    res = minimize(obj, [ua0, c0], method="Nelder-Mead",
                   options={"xatol": 0.5, "fatol": 1e-4, "maxiter": 400})
    ua_opt, c_opt = float(res.x[0]), float(res.x[1])
    rmse = float(res.fun)

    if ua_opt <= 1 or c_opt <= 1e4:
        return None, None, None, None, len(T_meas), "优化未收敛到合理参数"

    # 归一化误差指标（与温度标准差比较，越接近 0 越好）
    std_T = float(np.std(T_meas))
    nr = rmse / std_T if std_T > 0 else float("nan")

    note = (f"前向仿真优化（{len(T_meas)} 点，步长 {dt:.0f} 秒）；"
            f"RMSE = {rmse:.3f} °C（温度标准差 {std_T:.3f} °C，"
            f"归一化 {nr:.2%}）")
    return ua_opt, c_opt, Q_INTERNAL_W, rmse, len(T_meas), note


def calibrate_ua(records, ac_state, occupied, dt_s):
    """兼容旧接口：返回 (ua, n_samples, note)。"""
    ua, c, q_int, r2, n, note = calibrate_energy_balance(
        records, ac_state, occupied, dt_s)
    if ua is None:
        return None, 0, note
    return ua, n, f"R² = {r2:.4f}；{note}"


def calibrate_c(records, ac_state, occupied, ua, dt_s):
    """兼容旧接口：热容已在能量平衡回归中一并得到。

    这里改为用"空调关闭区间的弛豫时间常数"做独立交叉验证，
    若与回归结果接近，说明标定可信。
    """
    if ua is None or ua <= 0:
        return None, 0, "UA 未标定成功"

    taus = []
    i = 0
    n = len(records)
    temps = np.array([r["temp"] for r in records])

    while i < n:
        if ac_state[i] is not False:
            i += 1
            continue
        j = i
        while j < n and ac_state[j] is False:
            j += 1
        seg = records[i:j]
        i = j

        if len(seg) * dt_s < 1800:      # 至少 30 分钟
            continue

        T = temps[i - len(seg):i]
        T_out = np.array([estimate_outdoor_temp(r["t"]) for r in seg])

        # 一阶弛豫：dT/dt = (UA/C)(T_out + Q_int/UA − T)
        dT = np.diff(T) / dt_s
        T_mid = 0.5 * (T[:-1] + T[1:])
        Tout_mid = 0.5 * (T_out[:-1] + T_out[1:])

        x = Tout_mid + Q_INTERNAL_W / ua - T_mid
        y = dT
        m = np.abs(y) < 0.01
        if m.sum() < 20:
            continue
        x, y = x[m], y[m]
        denom = float(np.sum(x * x))
        if denom <= 1e-15:
            continue
        k = float(np.sum(x * y) / denom)
        if k <= 1e-9:
            continue
        tau = 1.0 / k
        if 60 < tau < 24 * 3600:
            taus.append(tau)

    if not taus:
        return None, 0, "未找到足够长的空调关闭区间"
    tau_med = float(np.median(taus))
    return ua * tau_med, len(taus), \
        f"由弛豫时间常数推算：τ 中位数 = {tau_med/60:.1f} 分钟（{len(taus)} 个区间）"


# ============================================================
# 标定 3：真实占用概率表
# ============================================================
def calibrate_occupancy(records, smooth_prior=0.15):
    """统计 7×24 的在室概率表。

    平滑处理：当某时段样本很少时，用先验权重做向均值收缩，
    避免出现"某时段 100% 在家"这种过拟合结论。
    """
    counts = np.zeros((7, 24))
    totals = np.zeros((7, 24))

    for r in records:
        dow = r["t"].weekday()
        hour = r["t"].hour
        occ = 1.0 if ((r["pir1"] or 0) > 0 or (r["pir2"] or 0) > 0
                      or (r["radar"] or 0) > 0) else 0.0
        counts[dow, hour] += occ
        totals[dow, hour] += 1

    # 全局在室比例作为先验中心
    global_p = counts.sum() / max(totals.sum(), 1)

    table = np.zeros((7, 24))
    for d in range(7):
        for h in range(24):
            n = totals[d, h]
            if n == 0:
                table[d, h] = global_p
            else:
                # 向先验收缩：p = (occ + prior*global_p*K) / (n + prior*K)
                K = 5.0     # 先验等效样本数
                table[d, h] = ((counts[d, h] + smooth_prior * K * global_p)
                               / (n + smooth_prior * K))
    return table, counts, totals, global_p


# ============================================================
# 主流程
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="从实测数据标定仿真模型参数")
    ap.add_argument("--dir", type=str, default=None,
                    help="数据目录，默认 data/raw")
    ap.add_argument("--truth", action="store_true",
                    help="与 _ground_truth.json 对比（合成数据验证用）")
    ap.add_argument("--out", type=str, default=None,
                    help="输出目录，默认 data")
    args = ap.parse_args()

    data_dir = Path(args.dir) if args.dir else (PROJECT_ROOT / "data" / "raw")
    out_dir = Path(args.out) if args.out else (PROJECT_ROOT / "data")
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print(" 数据标定 —— 从实测数据反推仿真模型参数")
    print("=" * 70)
    print(f"  数据目录 : {data_dir}")

    records, files = load_records(data_dir)
    if not records:
        print("\n  [错误] 未找到有效数据。请检查：")
        print(f"    · {data_dir} 下是否有 *.csv 文件")
        print("    · 是否先运行过采集脚本或合成数据生成器")
        return 1

    # 自动检测采样间隔
    if len(records) > 1:
        gaps = [(records[i + 1]["t"] - records[i]["t"]).total_seconds()
                for i in range(min(len(records) - 1, 500))]
        gaps = [g for g in gaps if g > 0]
        dt_s = float(np.median(gaps)) if gaps else DT_SECONDS
    else:
        dt_s = DT_SECONDS

    events = load_ac_events(data_dir)
    ac_state = build_ac_state_series(records, events)

    occupied = [1 if ((r["pir1"] or 0) > 0 or (r["pir2"] or 0) > 0
                      or (r["radar"] or 0) > 0) else 0 for r in records]

    t_start = records[0]["t"]
    t_end = records[-1]["t"]
    span_h = (t_end - t_start).total_seconds() / 3600.0

    print(f"  数据文件 : {len(files)} 个")
    print(f"  记录条数 : {len(records)}")
    print(f"  时间范围 : {t_start:%Y-%m-%d %H:%M} ~ {t_end:%Y-%m-%d %H:%M}"
          f"（约 {span_h:.1f} 小时）")
    print(f"  采样间隔 : {dt_s:.0f} 秒")
    print(f"  空调事件 : {len(events)} 条")
    print(f"  在室比例 : {np.mean(occupied):.1%}")

    if not events:
        print("\n  [警告] 未读到空调开关事件，标定将无法进行。")
        print("         请确认 events 文件里记录了'空调开启/关闭'。")
        return 1

    # ---- 标定 UA / C（前向仿真优化）----
    print("\n" + "-" * 70)
    print(" 标定 1/2：前向仿真参数优化（反推 UA 与 C）")
    print("-" * 70)
    ua, c_reg, q_int_reg, rmse, n_ua, note = calibrate_energy_balance(
        records, ac_state, occupied, dt_s)

    if ua is None:
        print(f"  [失败] {note}")
        c_est = None
    else:
        print(f"  拟合优度 : {note}")
        print()
        print(f"  围护传热系数 UA  = {ua:8.1f} W/K")
        print(f"  等效热容     C   = {c_reg:.3e} J/K")
        c_est = c_reg

        # 换算成仿真参数
        ua_coef_eq = ua * (MODEL_DT_MIN * 60) / c_reg
        print()
        print(f"  换算到仿真参数（步长 {MODEL_DT_MIN:.0f} 分钟）：")
        print(f"    当前 ua_coef = 0.0400")
        print(f"    标定等效值   = {ua_coef_eq:.4f}")
        print(f"    （换算公式：ua_coef = UA × 步长秒数 / C）")

    # ---- 粗略旁证：弛豫时间常数 ----
    print("\n" + "-" * 70)
    print(" 旁证：由弛豫时间常数粗估热容（仅供参考，精度低）")
    print("-" * 70)
    c_cross, n_c, c_msg = calibrate_c(records, ac_state, occupied, ua, dt_s)
    if c_cross is None:
        print(f"  [跳过] {c_msg}")
    else:
        print(f"  粗估值 : C ≈ {c_cross:.3e} J/K")
        print(f"  {c_msg}")
        print()
        print("  说明：该估计仅利用少数几个空调关闭区间的温度弛豫，")
        print("        需要对含噪温度序列求导，误差本就很大，")
        print("        只用于确认数量级，不作为标定依据。")
        print("        正式结果以上面的前向仿真优化为准。")

    # ---- 标定占用表 ----
    print("\n" + "-" * 70)
    print(" 标定 3/3：真实占用概率表（7×24）")
    print("-" * 70)
    table, counts, totals, global_p = calibrate_occupancy(records)
    print(f"  全局在室比例 : {global_p:.1%}")
    print()
    print("  " + "     ".join(f"{h:>2}时" for h in range(0, 24, 3)))
    print("  " + "-" * 62)
    names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for d in range(7):
        vals = "  ".join(f"{table[d, h]*100:3.0f}%" for h in range(0, 24, 3))
        print(f"  {names[d]}  {vals}")
    print()
    print("  对比：仿真内置的'典型上班族'假设（工作日 9-17 时约 10% 在室）")

    # ---- 输出 ----
    result = {
        "data_files": [f.name for f in files],
        "n_records": len(records),
        "time_span_hours": round(span_h, 2),
        "sample_interval_s": dt_s,
        "n_ac_events": len(events),
        "occupancy_ratio": float(np.mean(occupied)),
        "ua_w_per_k": None if ua is None else round(ua, 2),
        "ua_rmse_c": None if ua is None else round(rmse, 4),
        "ua_n_samples": n_ua,
        "c_j_per_k": None if c_est is None else float(f"{c_est:.4e}"),
        "c_note": c_msg,
        "occupancy_table": [[round(float(v), 4) for v in row] for row in table],
        "occupancy_global": round(float(global_p), 4),
    }

    params_path = out_dir / "calibrated_params.json"
    with open(params_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    occ_path = out_dir / "occupancy_table.csv"
    with open(occ_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day_of_week"] + [f"h{h:02d}" for h in range(24)])
        for d in range(7):
            w.writerow([names[d]] + [f"{table[d, h]:.4f}" for h in range(24)])

    print("=" * 70)
    print(" 输出")
    print("=" * 70)
    print(f"  标定结果 : {params_path}")
    print(f"  占用表   : {occ_path}")

    # ---- 与真值对比 ----
    if args.truth:
        gt_path = data_dir / "_ground_truth.json"
        print("\n" + "=" * 70)
        print(" 与合成数据真值对比（验证标定正确性）")
        print("=" * 70)
        if not gt_path.exists():
            print(f"  未找到真值文件：{gt_path}")
        else:
            with open(gt_path, encoding="utf-8") as f:
                gt = json.load(f)["ground_truth"]
            print(f"  {'参数':<20}{'真值':>14}{'标定值':>14}{'相对误差':>12}")
            print("  " + "-" * 60)

            def cmp_row(name, truth, est, unit=""):
                if est is None:
                    print(f"  {name:<20}{truth:>14.4g}{'未标定':>14}{'—':>12}")
                    return
                err = (est - truth) / truth if truth else float("nan")
                print(f"  {name:<20}{truth:>14.4g}{est:>14.4g}"
                      f"{err:>11.1%}")

            cmp_row("UA (W/K)", gt["ua_w_per_k"], ua)
            cmp_row("C (J/K)", gt["c_j_per_k"], c_est)
            if gt.get("occupancy_ratio"):
                cmp_row("在室比例", gt["occupancy_ratio"],
                        float(np.mean(occupied)))
            else:
                print(f"  {'在室比例':<20}{'（真值未记录）':>28}"
                      f"{float(np.mean(occupied)):>12.1%}")
            print()
            print("  说明：")
            print("    · UA 与 C 由前向仿真优化反推，")
            print("      误差在数个百分点内即说明标定链路可用")
            print("    · 若误差很大，先检查空调功率参数（AC_COOLING_MAX_W /")
            print("      AC_GAIN_PER_K）是否与实际设备相符")

    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
