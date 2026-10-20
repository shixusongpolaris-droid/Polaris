#!/usr/bin/env bash
# 一键启动：准备环境 -> 生成数据（如缺失）-> 离线分析 -> 初始化数据库 -> 启动 Web 服务
set -e
cd "$(dirname "$0")"

PY=.venv/bin/python
if [ ! -x "$PY" ]; then
  echo "[1/6] 创建 Python 虚拟环境..."
  python3 -m venv --system-site-packages .venv
  .venv/bin/pip install -q -r requirements.txt
fi

if [ ! -f data/historical_orders.csv ]; then
  echo "[2/6] 生成 3000 条历史工单..."
  "$PY" scripts/generate_data.py
else
  echo "[2/6] 历史工单已存在，跳过生成"
fi

if [ ! -f data/dispatch_priors.json ]; then
  echo "[3/6] 运行 Pandas 离线分析..."
  "$PY" scripts/analyze.py
else
  echo "[3/6] 离线分析结果已存在，跳过"
fi

"$PY" scripts/render_report.py || true

if [ ! -f reports/agent_evaluation.json ]; then
  echo "[4/6] 评估 Agent 准确率..."
  "$PY" scripts/evaluate_agents.py
else
  echo "[4/6] Agent 评估结果已存在，跳过"
fi

echo "[5/6] 初始化数据库..."
"$PY" scripts/init_db.py --force-history --seed-live --seed-community

echo "[6/6] 启动智修云看板： http://127.0.0.1:8000"
exec "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8000

