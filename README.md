# 项目合集

个人项目代码仓库，涵盖数据分析、机器学习、算法实现等方向。

---

## 📁 项目列表

### 🔢 大数据岗位能力分析

Python 数据分析项目，对大数据岗位招聘数据进行采集、清洗、特征工程、评分体系和模型预测。

| 模块 | 说明 |
|---|---|
| `data_collection(数据采集)` | 招聘网站爬虫（BOSS 直聘、前程无忧、拉勾） |
| `data_expansion_analysis(数据扩充与分析)` | 样本扩充、数据分析与可视化 |
| `feature_engineering(特征工程)` | 岗位标签化、特征处理器 |
| `scoring_system(评分体系)` | 岗位能力评分规则与算法 |
| `model_application(模型应用)` | ML 管道（LR/RF/GB）、Web 应用、模型文件 |

**技术栈**: Python · pandas · scikit-learn · Flask · jieba

---

### 🏠 徐家汇房价对比预测

上海徐家汇地区链家二手房数据采集、特征工程与价格预测模型。

| 模块 | 说明 |
|---|---|
| `数据采集` | 链家/安居客爬虫，数据处理 |
| `特征工程` | 数据预处理、特征准备 |
| `模型应用` | 线性回归、模型对比、评估指标 |

**技术栈**: Python · pandas · scikit-learn · matplotlib · seaborn · geopy

---

### 🤖 智能家居自适应决策系统

基于强化学习与多目标优化的智能家居环境控制研究。将家庭环境控制问题形式化为
马尔可夫决策过程（MDP），用 PPO 训练自适应控制策略，并与规则控制、
恒温控制等多种基线在"舒适度—能耗"双目标下对比。

| 模块 | 说明 |
|---|---|
| `src/env/config.py` | 参数配置（25 维状态、36 动作、奖励、约束、分时电价） |
| `src/env/simulator.py` | 状态转移（热模型、湿度、光照、人员占用、约束屏蔽） |
| `src/env/reward.py` | 奖励函数（舒适度代价 + 场景化加权能耗 + 切换惩罚） |
| `src/env/home_env.py` | Gymnasium 标准接口，可直接接 stable-baselines3 |
| `scripts/` | 训练、统一评估、消融实验、参数敏感性、马尔可夫性检验等 13 个脚本 |
| `docs/` | MDP 定义（含马尔可夫性论证）、实验结果报告、项目状态总结 |
| `models/figures/` | 25 张实验图表 |

**主要结论**：无分时电价场景下，PPO 相对规则控制在**舒适度与控制平稳性**上显著更优
（不达标率 −45.6%、温度偏差 RMSE −92.5%、设备切换次数 −70%）；在舒适度基本持平的
前提下相对理想恒温控制**节能约 16%**。研究同时得到明确的否定性结论：
"奖励权重的时间场景化"机制在 75 样本/组的统计检验下**未带来可检测收益**。

**硬件**：ESP32-S3 + SHT31（温湿度）+ BH1750（光照）+ PIR×2 + LD2410C（毫米波雷达）

**技术栈**: Python · PyTorch · stable-baselines3 · Gymnasium · pandas · matplotlib · Arduino

---

## 🛠 技术栈总览

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![PyTorch](https://img.shields.io/badge/PyTorch-EE4C2C?style=flat&logo=pytorch&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?style=flat&logo=scikit-learn&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-150458?style=flat&logo=pandas&logoColor=white)
![matplotlib](https://img.shields.io/badge/matplotlib-11557C?style=flat&logo=python&logoColor=white)

---

## 🚀 快速开始

```bash
# 克隆仓库
git clone git@github.com:shixusongpolaris-droid/Polaris.git
cd Polaris

# ---------- 大数据岗位能力分析 ----------
cd 大数据岗位能力分析
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python main.py

# ---------- 智能家居自适应决策系统 ----------
cd 智能家居自适应决策系统
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

# 环境自检（6 项检查，确认环境可用）
python scripts\test_env.py
```

---

## 📝 开发说明

- 使用 `git add -A && git commit -m "..." && git push` 提交更新
- Python 项目请使用虚拟环境隔离依赖（`venv` 或 `.venv`）
- `.gitignore` 已配置排除 `__pycache__/`、`.idea/`、`*.zip` 等
- 注意：`.gitignore` 中的目录规则若写成裸目录名（如 `env/`），
  会匹配任意层级的同名目录。本项目需保留 `src/env/`，
  因此相关规则已加前导斜杠（`/env/`）限定为仅匹配仓库根目录

---

*最后更新: 2026-10*
