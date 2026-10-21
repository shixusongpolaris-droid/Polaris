# 冻结模型数据（36ff8e8）

本目录保存单房间冻结模型（`fix/sim-audit` @ `36ff8e8`）的最终结果与复现脚本。
对应报告：`docs/团队统计复核_冻结模型.md`、`docs/团队复现_冻结模型.md`、
`docs/团队审计_冻结模型.md`、`docs/单房间最终结论_冻结模型.md`。

## 结果文件（results/）

| 文件 | 内容 |
|---|---|
| `models_freeze.json` | 30 个已保存 PPO 模型在冻结环境下的重评（9100-9107，8 回合） |
| `models_freeze_c3off.json` | 同上，C3 关闭的对照 |
| `rules_freeze_t{1,2,3}_{flat,tou}.json` | 941 条规则 × 6 个电价格的逐条结果（冻结环境） |
| `rules_freeze_c3off_t{1,2,3}_{flat,tou}.json` | 同上，C3 关闭的对照 |
| `rules_freeze_s9200_t1_{flat,tou}.json` | t1 的 held-out 9200-9207 结果 |
| `setpoint_freeze.json` | 23/25/27/29°C 设定温度能耗 |
| `test_freeze.json` / `test_freeze_c3off.json` | 测试/自检输出 |

## 脚本（scripts/）

- `harness.py`：独立的评估 harness（不依赖项目的 measure/pareto）；
- `run_all_rules.py`：并发跑 941 规则 × 6 格；
- `run_rules.py`：单格规则评估；
- `run_models.py`：重评 30 个已保存模型（不训练）；
- `run_setpoint.py`：设定温度能耗；
- `analyze_freeze.py`：汇总/作图；
- `check_freeze.py`：回流核对 91 项数字（全部通过）；
- `check_final.py`：t6 的核对脚本（历史）。

## 复现命令

```powershell
$env:PYTHONUTF8 = '1'
$py = 'D:\Dev\Projects\Polaris\智能家居自适应决策系统\.venv\Scripts\python.exe'
$env:VARIANTS = 'freeze,freeze_c3off'
& $py scripts\run_all_rules.py
& $py scripts\run_models.py --variant freeze       --out out\models_freeze.json
& $py scripts\run_models.py --variant freeze_c3off --out out\models_freeze_c3off.json
& $py scripts\run_setpoint.py
& $py scripts\check_freeze.py
```

`harness.py` 里的 `REPO` 是绝对路径；换机器时改这一行。
脚本只读 `src/` 与 `models/`，不训练、不改模型。

## 口径

- 评估：种子 9100~9107、8 回合、7 天/回合、`occupancy_mode="semi_markov"`；
- 规则族：`scripts/gen7_unified_protocol.py` 的 `build_specs()`，941 条；
- 30 个模型：`models/gen7_tariff_six/{tag}_s{seed}/`，**旧能量模型训练**；
- 冻结模型：gross cooling 计费、循环平均功率、预测式 C3、C4 请求关机；
- 绝对电费是模型单位，不是真实账单。

## 校验

`SHA256SUMS.txt` 是本目录全部结果/脚本的 SHA-256；
`data/SHA256SUMS.txt` 是原始实测数据与 `timeline_final.csv` 的 SHA-256。
