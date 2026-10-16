# -*- coding: utf-8 -*-
"""仿真参数系统审计：逐项区分"已标定"与"未经核实"。

为什么需要这个脚本
------------------
项目至今已发现三处问题：
  1. get_forward_price 在未分时场景硬编码返回 1.0，使电价进不了奖励
  2. param_sweep_full / key_experiments 硬编码了旧默认值的扫描网格
  3. ac_coef / ac_max_cooling 从未标定，导致"空调压不住温度"，
     连"全程开空调"都有 16.7% 不舒适

这三次都是"逐个发现"的。为避免继续逐个试错，本脚本把 SimConfig 的
全部字段列出来，逐项标注：
  · 是否已用实测数据标定
  · 是否有实测依据可核对
  · 物理量纲是否自洽
并给出需要补标的参数清单（按影响排序）。

只做审计与打印，不修改任何配置。
"""

import json
import os
import sys
import dataclasses

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
sys.path.insert(0, PROJ)

from src.env.config import SimConfig, ActionConfig, RewardConfig  # noqa: E402

OUT_MD = os.path.join(PROJ, "docs", "仿真参数审计.md")
OUT_JSON = os.path.join(PROJ, "models", "param_audit.json")

# ---------------------------------------------------------------
# 已标定参数（2026-10-16，依据 49.9 小时实测数据）
# ---------------------------------------------------------------
CALIBRATED = {
    "ua_coef": ("0.040 -> 0.115", "由实测时间常数 tau≈130 分钟反推"),
    "internal_gain": ("0.02 -> 0.80", "由实测稳态室内外温差约 +7 摄氏度反推"),
    "temp_outdoor_mean": ("30.0 -> 22.0", "实测十月上海室外均值"),
    "temp_outdoor_swing": ("5.0 -> 3.0", "实测室外日内波动"),
}

# ---------------------------------------------------------------
# 实测事实（可用来核对仿真参数）
# ---------------------------------------------------------------
MEASURED = {
    "室内温度范围": "19.3 ~ 31.0 摄氏度",
    "室内温度均值": "约 24.5 摄氏度",
    "客厅温度（两次独立测量）": "26.0 摄氏度",
    "室外温度（10-15）": "18.8 ~ 22.6 摄氏度",
    "室外温度（10-16）": "20.5 ~ 25.8 摄氏度",
    "室内湿度": "44.0 ~ 68.1 %",
    "室内光照": "0.8 ~ 15982 lx",
    "空调降温速率（开启期间）": "最大 -14.9 摄氏度/小时，中位 -6.6 摄氏度/小时",
    "房间时间常数": "约 70 ~ 130 分钟（两个独立窗口）",
    "空调启停序列": "10 次切换，严格交替（已核对）",
}

# ---------------------------------------------------------------
# 逐项判断：需要补标的参数
# ---------------------------------------------------------------
NEEDS_CALIBRATION = [
    ("ac_coef", "0.30",
     "空调制冷系数：决定制冷量与室内外温差的比例关系。"
     "实测降温速率 1.65~3.7 摄氏度/步，而仿真在典型温差下仅约 0.3 摄氏度/步，"
     "疑似低估约 3~5 倍。直接影响所有策略的舒适度水平。"),
    ("ac_max_cooling", "1.2",
     "空调单步最大制冷量（摄氏度/步）。"
     "实测最大降温 14.9 摄氏度/小时 = 3.7 摄氏度/步，高于仿真上限 1.2。"
     "该上限决定了'空调压不住温度'的现象，是本次诊断的直接原因。"),
    ("ac_min_power_ratio", "见 ActionConfig",
     "空调最低功率比：决定规则控制能否细调功率。"
     "参数扫描显示 PPO 与规则的优劣对该值高度敏感"
     "（规则电费从 3.6 变到 8.9 元/天）。该值目前是估计值。"),
    ("vent_coef", "0.12",
     "通风换热系数。窗开实验做了两次但结论是'开窗无效'，"
     "说明该系数或开窗建模方式与实测不符，需重新核对。"),
    ("hum_ac_dehumid / hum_vent_rate", "见 SimConfig",
     "湿度模型参数。实测湿度 44~68%，需核对仿真湿度分布是否匹配。"),
    ("light_window_ratio", "见 SimConfig",
     "自然采光比例。实测光照 0.8~15982 lx，跨度极大（4 个数量级），"
     "需核对该线性系数能否覆盖。"),
    ("temp_outdoor_swing 的日内形状", "正弦假设",
     "目前用正弦模拟日内波动，但实测仅 2 天数据，"
     "且未验证正弦形状是否合适。"),
]

# ---------------------------------------------------------------
# 量纲/自洽性检查
# ---------------------------------------------------------------
def consistency_checks():
    c = SimConfig()
    rows = []
    ua, gain, ac = c.ua_coef, c.internal_gain, c.ac_coef
    Tout = c.temp_outdoor_mean

    # 1. 无空调时的平衡温度
    T_no_ac = Tout + gain / ua
    rows.append(("无空调平衡温度（有人）", f"{T_no_ac:.2f} °C",
                 "实测室内最高 31.0 °C",
                 "偏高" if T_no_ac > 31.0 else
                 ("接近" if T_no_ac > 28 else "偏低")))

    # 2. 空调全开@25°C 的平衡温度
    T_eq = (ua * Tout + gain + ac * 25.0) / (ua + ac)
    rows.append(("空调全开@25°C 平衡温度（有人）", f"{T_eq:.2f} °C",
                 "有人舒适上限 26 °C",
                 "压不住（高于 26）" if T_eq > 26 else "可以压住"))

    # 3. 房间时间常数
    tau = c.dt_minutes / ua
    rows.append(("房间时间常数 tau", f"{tau:.1f} 分钟",
                 "实测约 70 ~ 130 分钟",
                 "合理" if 70 <= tau <= 130 else "超出实测范围"))

    # 4. 制冷能力对比
    max_cool_per_hour = c.ac_max_cooling / (c.dt_minutes / 60.0)
    rows.append(("空调最大制冷速率", f"{max_cool_per_hour:.1f} °C/小时",
                 "实测最大 -14.9 °C/小时",
                 "低估" if max_cool_per_hour < 14.9 else "足够"))

    # 5. 内热增益对应的稳态温差
    rows.append(("内热导致的稳态温差", f"{gain/ua:.2f} °C",
                 "实测室内外稳态温差约 +7 °C",
                 "合理" if 4 <= gain / ua <= 10 else "偏差较大"))
    return rows


def main():
    c = SimConfig()
    lines = []
    def P(s=""):
        print(s)
        lines.append(s)

    P("# 仿真参数审计")
    P()
    P("## 一、已标定参数（2026-10-16，依据 49.9 小时实测数据）")
    P()
    P("| 参数 | 变化 | 依据 |")
    P("|---|---|---|")
    for k, (chg, why) in CALIBRATED.items():
        P(f"| `{k}` | {chg} | {why} |")
    P()

    P("## 二、量纲与自洽性检查")
    P()
    P("| 检查项 | 仿真值 | 实测/目标 | 判断 |")
    P("|---|---|---|---|")
    for name, val, target, verdict in consistency_checks():
        flag = "OK" if verdict in ("合理", "接近", "可以压住", "足够") else "**需注意**"
        P(f"| {name} | {val} | {target} | {flag} {verdict} |")
    P()

    P("## 三、需要补标定的参数（按影响排序）")
    P()
    P("| 参数 | 当前值 | 为什么需要标定 |")
    P("|---|---|---|")
    for k, v, why in NEEDS_CALIBRATION:
        P(f"| `{k}` | {v} | {why} |")
    P()

    P("## 四、可用的实测事实（核对依据）")
    P()
    P("| 项目 | 实测值 |")
    P("|---|---|")
    for k, v in MEASURED.items():
        P(f"| {k} | {v} |")
    P()

    P("## 五、SimConfig 全部字段")
    P()
    P("| 字段 | 当前值 |")
    P("|---|---|")
    n_cal = 0
    for f in dataclasses.fields(c):
        name = f.name
        val = getattr(c, name)
        mark = ""
        if name in CALIBRATED:
            mark = " **[已标定]**"
            n_cal += 1
        P(f"| `{name}` | `{val}`{mark} |")
    P()
    total = len(dataclasses.fields(c))
    P(f"**共 {total} 个字段，其中已用实测数据标定 {n_cal} 个"
      f"（{n_cal/total:.0%}）。**")
    P()

    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump({
            "calibrated": {k: list(v) for k, v in CALIBRATED.items()},
            "checks": consistency_checks(),
            "needs_calibration": [{"param": k, "current": v, "why": w}
                                  for k, v, w in NEEDS_CALIBRATION],
            "measured": MEASURED,
            "all_fields": {f.name: str(getattr(c, f.name))
                           for f in dataclasses.fields(c)},
        }, f, ensure_ascii=False, indent=2)

    P(f"报告已保存: {OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
