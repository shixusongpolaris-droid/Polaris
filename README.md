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

### ⚙️ hello

C++20 入门项目，CMake 构建。

---

### 🔤 kmp

KMP 字符串匹配算法的 C/C++ 实现。

---

### 📄 untitled

C11 基础项目，CMake 构建。

---

## 🛠 技术栈总览

![Python](https://img.shields.io/badge/Python-3776AB?style=flat&logo=python&logoColor=white)
![C++](https://img.shields.io/badge/C++-00599C?style=flat&logo=c%2B%2B&logoColor=white)
![C](https://img.shields.io/badge/C-A8B9CC?style=flat&logo=c&logoColor=white)
![CMake](https://img.shields.io/badge/CMake-064F8C?style=flat&logo=cmake&logoColor=white)
![scikit-learn](https://img.shields.io/badge/scikit--learn-F7931E?style=flat&logo=scikit-learn&logoColor=white)
![pandas](https://img.shields.io/badge/pandas-150458?style=flat&logo=pandas&logoColor=white)

---

## 🚀 快速开始

```bash
# 克隆仓库
git clone git@github.com:shixusongpolaris-droid/Polaris.git
cd Polaris

# Python 项目（以大数据岗位能力分析为例）
cd 大数据岗位能力分析
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## 📝 开发说明

- 使用 `git add -A && git commit -m "..." && git push` 提交更新
- Python 项目请使用 `venv` 隔离依赖
- `.gitignore` 已配置排除 `venv/`、`.idea/`、`__pycache__/` 等

---

*最后更新: 2026-06*
