#!/usr/bin/env python3
"""
模块5扩展: Web Dashboard
Flask应用，展示模型结果、评分排名、数据分布
"""
import json
import os
import base64
import sys
from io import BytesIO

from flask import Flask, render_template, jsonify, send_file

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

app = Flask(__name__)


def load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ============================================================
# Routes
# ============================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/summary")
def api_summary():
    """汇总数据"""
    jobs = load_json("data/labeled_jobs.json")
    ratings = load_json("data/ratings.json")
    model_report = load_json("model_application/models/model_report.json")
    derived = {}
    for f in ["skill_heat_ranking", "category_profile_cards"]:
        p = f"data/derived/{f}.json"
        if os.path.exists(p):
            derived[f] = load_json(p)

    total = jobs.get("metadata", {}).get("total", len(jobs.get("jobs", [])))
    cats = jobs.get("category_distribution", {})

    return jsonify({
        "total_jobs": total,
        "categories": cats,
        "model_report": model_report,
        "top_skills": list(ratings.get("skill_demand", {}).items())[:10],
        "top_premium": list(ratings.get("skill_premium", {}).items())[:10],
    })


@app.route("/api/category_distribution")
def api_category_distribution():
    """类别分布（用于饼图）"""
    jobs = load_json("data/labeled_jobs.json")
    cats = jobs.get("category_distribution", {})
    # Remove null
    result = [{"name": k, "value": v} for k, v in cats.items() if k]
    return jsonify(result)


@app.route("/api/skill_ranking")
def api_skill_ranking():
    """技能排名数据"""
    derived = load_json("data/derived/skill_heat_ranking.json")
    if isinstance(derived, list):
        return jsonify(derived[:15])
    return jsonify([])


@app.route("/api/model_metrics")
def api_model_metrics():
    """模型对比指标"""
    report = load_json("model_application/models/model_report.json")
    return jsonify(report)


@app.route("/api/salary_by_category")
def api_salary_by_category():
    """各类别薪资统计"""
    cards = load_json("data/derived/category_profile_cards.json")
    result = []
    for cat, info in cards.items():
        ss = info.get("salary_stats", {})
        result.append({
            "category": cat,
            "count": info.get("job_count", 0),
            "median_salary": ss.get("median_wan", 0),
            "mean_salary": ss.get("mean_wan", 0),
            "growth": info.get("growth_indicator", ""),
        })
    return jsonify(result)


@app.route("/charts/<path:filename>")
def serve_chart(filename):
    """提供图表PNG"""
    path = os.path.join(os.path.dirname(__file__), "charts", filename)
    if os.path.exists(path):
        return send_file(path, mimetype="image/png")
    return "Not found", 404


def main():
    print("=" * 60)
    print("  大数据岗位分析 Web Dashboard")
    print("=" * 60)
    print(f"\n  访问: http://127.0.0.1:5005")
    print(f"  按 Ctrl+C 停止\n")
    app.run(debug=False, host="127.0.0.1", port=5005)


if __name__ == "__main__":
    main()
