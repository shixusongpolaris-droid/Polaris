#!/usr/bin/env python3
"""
模块4: 衍生数据分析与扩充 (Derived Analytics)
产出:
1. 技能热度排名 (JSON + Excel)
2. 薪资多维度分布 (category×level/city/industry)
3. 公司排行榜
4. 岗位类别画像卡片
5. 汇总Excel多Sheet工作簿
"""
import json
import os
import sys
from collections import Counter, defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# Data Loading
# ============================================================
def load_labeled_data(filepath: str = "data/labeled_jobs.json") -> list[dict]:
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("jobs", data) if isinstance(data, dict) else data


def load_ratings(filepath: str = "data/ratings_full.json") -> dict:
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    # Try alternative path
    alt = "data/ratings.json"
    if os.path.exists(alt):
        with open(alt, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


# ============================================================
# Salary helper (same pattern as scoring.py)
# ============================================================
def _salary_avg(job: dict) -> float:
    """获取岗位平均月薪(万)，无薪资返回0"""
    if job.get("salary_avg_wan_month"):
        return job["salary_avg_wan_month"]
    smin = job.get("salary_min_k")
    smax = job.get("salary_max_k")
    if smin and smax and smin > 0:
        return (smin + smax) / 2
    return 0.0


def _salary_stats(jobs: list[dict]) -> dict:
    """计算岗位组的薪资统计"""
    avgs = [_salary_avg(j) for j in jobs if _salary_avg(j) > 0]
    if not avgs:
        return {}
    s = sorted(avgs)
    n = len(s)
    return {
        "count": n,
        "avg_min_wan": round(min(avgs), 1),
        "avg_max_wan": round(max(avgs), 1),
        "mean_wan": round(sum(avgs) / n, 1),
        "median_wan": round(s[n // 2], 1),
        "p25_wan": round(s[max(0, n // 4)], 1),
        "p75_wan": round(s[min(n - 1, n * 3 // 4)], 1),
    }


# ============================================================
# 1. 技能热度排名
# ============================================================
def compute_skill_heat_ranking(jobs: list[dict], ratings: dict = None) -> list[dict]:
    """
    Top 30技能多列排名:
    - frequency: 出现次数
    - frequency_pct: 占比
    - avg_salary: 有该技能的岗位平均薪资
    - salary_premium_pct: 薪资溢价（从ratings获取）
    - growth_indicator: 增长标签
    - category_count: 覆盖类别数
    """
    if ratings is None:
        ratings = {}

    total = len(jobs)
    skill_freq = Counter()
    skill_salaries = defaultdict(list)
    skill_cats = defaultdict(set)

    for j in jobs:
        for s in set(j.get("tech_stack", []) + j.get("languages", [])):
            skill_freq[s] += 1
            skill_cats[s].add(j.get("main_category", ""))
            avg = _salary_avg(j)
            if avg > 0:
                skill_salaries[s].append(avg)

    premium_data = ratings.get("skill_premium", {})

    ranking = []
    for skill, freq in skill_freq.most_common(50):
        avg_sal = (sum(skill_salaries[skill]) / len(skill_salaries[skill])
                   if skill_salaries[skill] else 0)
        premium = premium_data.get(skill, {}).get("premium_pct", 0)
        cats = skill_cats[skill]

        ranking.append({
            "skill": skill,
            "frequency": freq,
            "frequency_pct": round(freq / total * 100, 1),
            "avg_salary_wan": round(avg_sal, 1),
            "salary_premium_pct": premium,
            "category_count": len(cats),
            "categories": "、".join(sorted(c for c in cats if c)),
            "growth_indicator": "高" if premium > 10 else ("中" if premium > 0 else "低"),
        })

    return ranking[:30]


# ============================================================
# 2. 薪资多维度分布
# ============================================================
def compute_salary_by_category_level(jobs: list[dict]) -> dict:
    """category × level 交叉薪资"""
    groups = defaultdict(list)
    for j in jobs:
        cat = j.get("main_category", "其他")
        lv = j.get("job_level", "未标注")
        if cat and lv:
            groups[(cat, lv)].append(j)

    result = {}
    for (cat, lv), group in groups.items():
        stats = _salary_stats(group)
        if stats:
            if cat not in result:
                result[cat] = {}
            result[cat][lv] = stats

    return result


def compute_salary_by_category_city(jobs: list[dict]) -> dict:
    """category × city 交叉薪资"""
    city_freq = Counter(j.get("location", "") for j in jobs)
    top_cities = {c for c, _ in city_freq.most_common(8)}

    groups = defaultdict(list)
    for j in jobs:
        cat = j.get("main_category", "")
        loc = j.get("location", "")
        city = loc.split("-")[0] if "-" in loc else loc
        if city in top_cities:
            groups[(cat, city)].append(j)

    result = {}
    for (cat, city), group in groups.items():
        stats = _salary_stats(group)
        if stats and stats["count"] >= 2:
            if cat not in result:
                result[cat] = {}
            result[cat][city] = stats

    return result


def compute_salary_by_category_industry(jobs: list[dict]) -> dict:
    """category × industry 交叉薪资"""
    groups = defaultdict(list)
    for j in jobs:
        cat = j.get("main_category", "")
        for ind in j.get("industry_tags", []):
            groups[(cat, ind)].append(j)

    result = {}
    for (cat, ind), group in groups.items():
        stats = _salary_stats(group)
        if stats and stats["count"] >= 2:
            if cat not in result:
                result[cat] = {}
            result[cat][ind] = stats

    return result


# ============================================================
# 3. 公司排行榜
# ============================================================
def compute_company_leaderboard(jobs: list[dict]) -> list[dict]:
    """按平均薪资/发布量/技能广度排名的公司Top20"""
    companies = defaultdict(list)
    for j in jobs:
        companies[j.get("company", "")].append(j)

    leaderboard = []
    for company, comp_jobs in companies.items():
        if len(comp_jobs) < 3 or not company:
            continue
        avgs = [_salary_avg(j) for j in comp_jobs if _salary_avg(j) > 0]
        cats = set(j.get("main_category", "") for j in comp_jobs)
        skills = set()
        for j in comp_jobs:
            skills.update(j.get("tech_stack", []))
            skills.update(j.get("languages", []))

        leaderboard.append({
            "company": company,
            "posting_count": len(comp_jobs),
            "avg_salary_wan": round(sum(avgs) / len(avgs), 1) if avgs else 0,
            "salary_sample": len(avgs),
            "avg_skill_breadth": round(len(skills) / len(comp_jobs), 1),
            "hiring_categories": "、".join(sorted(c for c in cats if c)),
            "category_count": len(cats),
            "total_skills": len(skills),
        })

    return sorted(leaderboard, key=lambda x: x["avg_salary_wan"], reverse=True)[:20]


# ============================================================
# 4. 岗位类别画像卡片
# ============================================================
def compute_category_profile_cards(jobs: list[dict]) -> dict:
    """为每个大类生成结构化的画像卡片"""
    import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'feature_engineering(特征工程)'))
from labeler import MAIN_CATEGORIES

    cards = {}
    # 增长标签
    growth_map = {"AI/机器学习": "高", "流计算": "高", "数据挖掘": "中",
                  "数据开发": "中", "数据分析": "中", "数据架构": "中",
                  "数据产品": "中", "数据运维": "低", "测试/质量": "低"}

    for cat_name in MAIN_CATEGORIES:
        cat_jobs = [j for j in jobs if j.get("main_category") == cat_name]
        if not cat_jobs:
            continue

        # 技能Top5
        skill_counter = Counter()
        for j in cat_jobs:
            for s in j.get("tech_stack", []):
                skill_counter[s] += 1

        # 公司Top3
        company_counter = Counter(j.get("company", "") for j in cat_jobs)

        # 经验学历
        exp_counter = Counter(j.get("job_level", "") for j in cat_jobs)
        edu_counter = Counter(j.get("education_std", "") for j in cat_jobs)

        cards[cat_name] = {
            "category": cat_name,
            "job_count": len(cat_jobs),
            "description": MAIN_CATEGORIES.get(cat_name, {}).get("description", ""),
            "salary_stats": _salary_stats(cat_jobs),
            "top_5_skills": [{"skill": s, "count": c,
                              "pct": round(c / len(cat_jobs) * 100, 1)}
                             for s, c in skill_counter.most_common(5)],
            "top_3_companies": [{"company": c, "count": cnt}
                                for c, cnt in company_counter.most_common(3)],
            "typical_experience": exp_counter.most_common(1)[0][0] if exp_counter else "中级",
            "typical_education": edu_counter.most_common(1)[0][0] if edu_counter else "本科",
            "experience_distribution": dict(exp_counter),
            "education_distribution": dict(edu_counter),
            "growth_indicator": growth_map.get(cat_name, "中"),
            "pct_of_total": round(len(cat_jobs) / len(jobs) * 100, 1),
        }

    return cards


# ============================================================
# 5. Excel汇总导出
# ============================================================
def export_derived_summary_xlsx(outputs: dict,
                                 filepath: str = "data/derived/derived_summary.xlsx"):
    """导出多Sheet的Excel工作簿"""
    import pandas as pd

    with pd.ExcelWriter(filepath, engine="openpyxl") as writer:
        # Sheet 1: 技能热度排名
        if outputs.get("ranking"):
            df = pd.DataFrame(outputs["ranking"])
            df.to_excel(writer, sheet_name="技能热度排名", index=False)

        # Sheet 2-4: 薪资分布 (扁平化)
        for key, label in [("category_level", "类别x等级"),
                           ("category_city", "类别x城市"),
                           ("category_industry", "类别x行业")]:
            dist = outputs.get("distributions", {}).get(key, {})
            if dist:
                rows = []
                for cat, sub in dist.items():
                    for dim, stats in sub.items():
                        rows.append({"类别": cat, "维度": dim, **stats})
                if rows:
                    df = pd.DataFrame(rows)
                    df.to_excel(writer, sheet_name=f"薪资_{label}", index=False)

        # Sheet 5: 公司排行榜
        if outputs.get("leaderboard"):
            df = pd.DataFrame(outputs["leaderboard"])
            df.to_excel(writer, sheet_name="公司排行榜", index=False)

        # Sheet 6: 类别画像
        if outputs.get("profile_cards"):
            rows = []
            for cat, card in outputs["profile_cards"].items():
                row = {
                    "类别": cat,
                    "岗位数": card["job_count"],
                    "占比%": card["pct_of_total"],
                    "增长指标": card["growth_indicator"],
                    "典型经验": card["typical_experience"],
                    "典型学历": card["typical_education"],
                    "Top3技能": "、".join(s["skill"] for s in card.get("top_5_skills", [])[:3]),
                    "Top3公司": "、".join(c["company"] for c in card.get("top_3_companies", [])),
                    "薪资中位(万/月)": card["salary_stats"].get("median_wan", 0) if card["salary_stats"] else 0,
                }
                rows.append(row)
            df = pd.DataFrame(rows)
            df.to_excel(writer, sheet_name="类别画像卡片", index=False)

    print(f"\n汇总Excel已保存至 {filepath}")


# ============================================================
# Main
# ============================================================
def main():
    print("=" * 60)
    print("  数据扩充与再分析 (Derived Analytics)")
    print("=" * 60)

    os.makedirs("data/derived", exist_ok=True)

    jobs = load_labeled_data()
    print(f"加载 {len(jobs)} 条岗位数据")

    ratings = load_ratings()
    if ratings:
        print("已加载评分数据 (来自 scoring.py)")

    # 1. 技能热度排名
    print("\n[1/4] 生成技能热度排名...")
    ranking = compute_skill_heat_ranking(jobs, ratings)

    with open("data/derived/skill_heat_ranking.json", "w", encoding="utf-8") as f:
        json.dump(ranking, f, ensure_ascii=False, indent=2)

    # 也保存为Excel
    import pandas as pd
    pd.DataFrame(ranking).to_excel(
        "data/derived/skill_heat_ranking.xlsx", index=False)
    print(f"  已保存 Top {len(ranking)} 技能")

    # 2. 薪资分布
    print("\n[2/4] 生成薪资分布数据...")
    distributions = {
        "category_level": compute_salary_by_category_level(jobs),
        "category_city": compute_salary_by_category_city(jobs),
        "category_industry": compute_salary_by_category_industry(jobs),
    }
    for name, dist in distributions.items():
        path = f"data/derived/salary_by_{name}.json"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dist, f, ensure_ascii=False, indent=2)
        groups = sum(len(sub) for sub in dist.values())
        print(f"  {name}: {groups} 个交叉组")

    # 3. 公司排行榜
    print("\n[3/4] 生成公司排行榜...")
    leaderboard = compute_company_leaderboard(jobs)
    with open("data/derived/company_leaderboard.json", "w", encoding="utf-8") as f:
        json.dump(leaderboard, f, ensure_ascii=False, indent=2)

    print(f"\n  Top 5 公司:")
    for i, c in enumerate(leaderboard[:5], 1):
        print(f"  {i}. {c['company']:<15} "
              f"平均薪资: {c['avg_salary_wan']}万/月 "
              f"岗位数: {c['posting_count']}")

    # 4. 类别画像卡片
    print("\n[4/4] 生成岗位类别画像卡片...")
    profile_cards = compute_category_profile_cards(jobs)
    with open("data/derived/category_profile_cards.json", "w", encoding="utf-8") as f:
        json.dump(profile_cards, f, ensure_ascii=False, indent=2)

    for cat, card in profile_cards.items():
        median = card["salary_stats"].get("median_wan", 0) if card["salary_stats"] else 0
        print(f"  {cat:<12} {card['job_count']:>3}岗  "
              f"薪资中位: {median:.1f}万/月  "
              f"增长: {card['growth_indicator']}")

    # 5. 汇总Excel
    print("\n生成汇总Excel...")
    export_derived_summary_xlsx({
        "ranking": ranking,
        "distributions": distributions,
        "leaderboard": leaderboard,
        "profile_cards": profile_cards,
    })

    print(f"\n所有衍生数据已保存至 data/derived/")
    print(f"  skill_heat_ranking.json / .xlsx")
    print(f"  salary_by_category_level.json")
    print(f"  salary_by_category_city.json")
    print(f"  salary_by_category_industry.json")
    print(f"  company_leaderboard.json")
    print(f"  category_profile_cards.json")
    print(f"  derived_summary.xlsx (6 sheets)")


if __name__ == "__main__":
    main()
