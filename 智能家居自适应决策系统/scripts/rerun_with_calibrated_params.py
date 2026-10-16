# -*- coding: utf-8 -*-
"""用标定后的热参数重跑全部关键实验。

背景
----
2026-10-16 用 49.9 小时实测数据标定了仿真热参数，四个参数变化显著：

    temp_outdoor_mean   30.0 -> 22.0   （实测十月上海室外均值）
    temp_outdoor_swing   5.0 ->  3.0   （实测日内波动）
    ua_coef             0.040 -> 0.115 （由实测时间常数 tau≈130 分钟反推）
    internal_gain        0.02 -> 0.80  （由实测稳态偏移 +7 摄氏度反推）

同时电价由三段式改为上海居民实际的两段式（峰 0.617 / 谷 0.307）。

此前文档中的全部实验结果都是在旧参数下得到的，定量结论会变化，
因此需要重跑，使结题报告的数字与当前模型一致。

本脚本的作用
------------
按顺序调用各实验脚本，记录起止时间与退出码，并先把旧结果备份到
`models/_oldparams_backup/`，便于新旧对比与追溯。

注意：本脚本只编排，不修改任何实验逻辑。
"""

import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

PROJ = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
PY = os.path.join(PROJ, ".venv", "Scripts", "python.exe")
SCRIPTS = os.path.join(PROJ, "scripts")
MODELS = os.path.join(PROJ, "models")
BACKUP = os.path.join(MODELS, "_oldparams_backup")
LOG = os.path.join(MODELS, "rerun_calibrated.log")

# 需要备份的旧结果文件（小体积，便于新旧对比）
BACKUP_GLOBS = ["results.json", "train_result.json", "summary.json",
                "cost_eval.json"]

# 实验序列：(名称, 脚本, 参数)
# 顺序原则：先重训产生模型与结果的，再跑读取这些结果的派生分析
STEPS = [
    ("主实验 + 消融（三种权重模式 × 5 种子）", "ablation_multiseed.py",
     ["--seeds", "5", "--episodes", "15", "--timesteps", "400000"]),
    ("显式储能对比（3 种子）", "storage_experiment.py",
     ["--seeds", "3", "--episodes", "10", "--timesteps", "400000"]),
    ("循环网络对比（3 种子）", "recurrent_experiment.py",
     ["--seeds", "3", "--episodes", "10", "--timesteps", "400000"]),
    ("能耗权重扫描", "energy_weight_sweep.py",
     ["--seeds", "2", "--episodes", "8", "--timesteps", "300000"]),
    ("参数敏感性扫描（快速）", "param_sweep_full.py",
     ["--seeds", "2", "--episodes", "8", "--timesteps", "300000", "--quick"]),
    ("规则设定点扫描", "sweep_setpoint.py", []),
    ("统一评估（20 回合）", "evaluate_all.py", ["--episodes", "20"]),
    ("配平比较", "balanced_comparison.py", []),
    ("帕累托前沿分析", "pareto_analysis.py", []),
]


def log(msg, also_print=True):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    if also_print:
        print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def backup_old():
    """把现有结果 json 备份，便于新旧对比。"""
    os.makedirs(BACKUP, exist_ok=True)
    n = 0
    for d in os.listdir(MODELS):
        src_dir = os.path.join(MODELS, d)
        if not os.path.isdir(src_dir) or d.startswith("_") or d == "figures":
            continue
        for g in BACKUP_GLOBS:
            fp = os.path.join(src_dir, g)
            if os.path.exists(fp):
                dst_dir = os.path.join(BACKUP, d)
                os.makedirs(dst_dir, exist_ok=True)
                shutil.copy2(fp, os.path.join(dst_dir, g))
                n += 1
    return n


def main():
    log("=" * 78)
    log(" 用标定后的热参数重跑全部关键实验")
    log("=" * 78)
    # 从配置读取实际参数打印，避免文字与实际不符
    try:
        sys.path.insert(0, PROJ)
        from src.env.config import SimConfig as _SC
        _c = _SC()
        log(f" 实际参数（读自 src/env/config.py）：")
        log(f"   ua_coef={_c.ua_coef}（tau={15.0/_c.ua_coef:.1f} 分钟）  "
            f"internal_gain={_c.internal_gain}  "
            f"ac_coef={_c.ac_coef}  ac_max_cooling={_c.ac_max_cooling}")
        log(f"   temp_outdoor_mean={_c.temp_outdoor_mean}  "
            f"temp_outdoor_swing={_c.temp_outdoor_swing}")
    except Exception as e:
        log(f"  （读取配置失败：{e}）")

    # 备份同时保留历史代次：除 _oldparams_backup 外，
    # 若已有 _gen1_backup / _gen2_backup 则不再覆盖。
    nb = backup_old()
    log(f" 已备份上一代结果 {nb} 个 json 到 models/_oldparams_backup/")

    results = []
    t_all = time.time()
    for i, (name, script, args) in enumerate(STEPS, 1):
        sp = os.path.join(SCRIPTS, script)
        if not os.path.exists(sp):
            log(f" [{i}/{len(STEPS)}] {name} —— 脚本不存在，跳过")
            results.append((name, "缺失", 0))
            continue
        log("")
        log(f" [{i}/{len(STEPS)}] {name}")
        log(f"        python {script} {' '.join(args)}")
        t0 = time.time()
        try:
            r = subprocess.run([PY, sp] + args, cwd=PROJ,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            rc = r.returncode
            tail = (r.stdout or "").strip().splitlines()[-6:]
        except Exception as e:
            rc = -1
            tail = [f"异常: {type(e).__name__}: {e}"]
        dur = time.time() - t0
        state = "成功" if rc == 0 else f"失败(rc={rc})"
        log(f"        -> {state}，耗时 {dur/60:.1f} 分钟")
        for line in tail:
            log(f"           | {line[:110]}")
        results.append((name, state, dur))

    log("")
    log("=" * 78)
    log(" 汇总")
    log("=" * 78)
    for name, state, dur in results:
        log(f"  {name:<40}{state:<16}{dur/60:>7.1f} 分钟")
    log("")
    log(f" 总耗时 {(time.time()-t_all)/60:.1f} 分钟")

    with open(os.path.join(MODELS, "rerun_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump([{"name": n, "state": s, "sec": round(d, 1)}
                   for n, s, d in results], f, ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
