#!/usr/bin/env python3
"""
模块3: 技能与岗位评分引擎 (Scoring Engine)
- 技能需求分 (Skill Demand Score)
- 岗位质量分 (Job Quality Score)
- 技能溢价指数 (Skill Premium Index)
- 类别匹配分 (Category Match Score)
所有权重由 config/scoring_rules.yaml 驱动
"""
import json
import os
import sys
from collections import Counter, defaultdict
from typing import Optional

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ============================================================
# Data Loading
# ============================================================
def load_labeled_data(filepath: str = "data/labeled_jobs.json") -> list[dict]:
    """加载标注后的岗位数据"""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "scoring_rules.yaml")
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("jobs", data) if isinstance(data, dict) else data


def load_scoring_config(filepath: str = None) -> dict:
    """加载评分规则配置"""
    if filepath is None:
        filepath = os.path.join(os.path.dirname(__file__), "scoring_rules.yaml")
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# ============================================================
# 1. 技能需求分 (Skill Demand Score, 0-100)
# ============================================================
def _collect_all_skills(jobs: list[dict]) -> list[str]:
    """收集所有技能（技术栈 + 编程语言）"""
    all_skills = set()
    for j in jobs:
        for tech in j.get("tech_stack", []):
            all_skills.add(tech)
        for lang in j.get("languages", []):
            all_skills.add(lang)
    # 合并已知关键词
    for j in jobs:
        for kw in (j.get("keyword_list", []) or []):
            if len(kw) >= 2:
                all_skills.add(kw)
    return list(all_skills)


def compute_skill_frequency(jobs: list[dict]) -> dict[str, float]:
    """计算每个技能的出现频率并归一化到0-100"""
    counter = Counter()
    total = len(jobs)
    for j in jobs:
        skills = set(j.get("tech_stack", []) + j.get("languages", []))
        for s in skills:
            counter[s] += 1

    if not counter:
        return {}

    max_cnt = max(counter.values())
    return {s: round(cnt / max_cnt * 100, 1) for s, cnt in counter.items()}


def compute_skill_salary_correlation(jobs: list[dict]) -> dict[str, float]:
    """计算每个技能与薪资的关联度（有该技能岗位的平均薪资百分位）"""
    jobs_with_salary = [j for j in jobs
                        if j.get("salary_avg_wan_month") or
                        (j.get("salary_min_k") and j.get("salary_max_k") and
                         j["salary_min_k"] > 0)]

    if not jobs_with_salary:
        return {}

    # 所有有薪资岗位的平均薪资
    all_avgs = []
    for j in jobs_with_salary:
        avg = j.get("salary_avg_wan_month") or (j["salary_min_k"] + j["salary_max_k"]) / 2
        all_avgs.append(avg)

    # 计算各技能的平均薪资
    skill_salaries = defaultdict(list)
    for j in jobs_with_salary:
        skills = set(j.get("tech_stack", []) + j.get("languages", []))
        avg = j.get("salary_avg_wan_month") or (j["salary_min_k"] + j["salary_max_k"]) / 2
        for s in skills:
            skill_salaries[s].append(avg)

    # 百分位排名
    sorted_all = sorted(all_avgs)
    result = {}
    for skill, salaries in skill_salaries.items():
        avg_sal = sum(salaries) / len(salaries)
        # 计算 avg_sal 在 all_avgs 中的百分位
        pct = sum(1 for x in sorted_all if x < avg_sal) / len(sorted_all) * 100
        result[skill] = round(min(pct, 100), 1)

    return result


def compute_skill_growth_trend(jobs: list[dict], config: dict) -> dict[str, float]:
    """估算技能增长趋势（类别基准 + 外部信号乘数）"""
    signals = config["skill_demand_score"]["external_demand_signals"]
    cat_baseline = config["skill_demand_score"]["category_growth_baseline"]

    # 收集技能所属类别
    skill_categories = defaultdict(set)
    for j in jobs:
        cat = j.get("main_category", "")
        for s in j.get("tech_stack", []) + j.get("languages", []):
            if cat:
                skill_categories[s].add(cat)

    result = {}
    for skill, cats in skill_categories.items():
        # 基准分：取最高类别分
        base = max((cat_baseline.get(c, 50) for c in cats), default=50)
        # 乘数
        multiplier = signals.get(skill, 1.0)
        score = min(base * multiplier, 100)
        result[skill] = round(score, 1)

    return result


def compute_skill_demand_scores(jobs: list[dict], config: dict) -> dict[str, float]:
    """综合技能需求分 = 频率*w1 + 薪资关联*w2 + 增长趋势*w3"""
    cfg = config["skill_demand_score"]
    w_freq = cfg["frequency_weight"]
    w_sal = cfg["salary_correlation_weight"]
    w_growth = cfg["growth_trend_weight"]

    freq = compute_skill_frequency(jobs)
    sal_corr = compute_skill_salary_correlation(jobs)
    growth = compute_skill_growth_trend(jobs, config)

    all_skills = set(freq.keys()) | set(sal_corr.keys()) | set(growth.keys())
    scores = {}
    for skill in all_skills:
        score = (freq.get(skill, 0) * w_freq +
                 sal_corr.get(skill, 0) * w_sal +
                 growth.get(skill, 0) * w_growth)
        scores[skill] = round(min(score, 100), 1)

    return dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))


# ============================================================
# 2. 岗位质量分 (Job Quality Score, 0-100)
# ============================================================
def _get_salary_avg(job: dict) -> Optional[float]:
    """获取岗位平均月薪(万)"""
    if job.get("salary_avg_wan_month"):
        return job["salary_avg_wan_month"]
    smin = job.get("salary_min_k")
    smax = job.get("salary_max_k")
    if smin and smax and smin > 0:
        return (smin + smax) / 2
    return None


def compute_job_quality_scores(jobs: list[dict], config: dict) -> list[dict]:
    """计算所有岗位的质量分 0-100"""
    cfg = config["job_quality_score"]
    prestige_map = cfg["company_prestige_map"]
    exp_map = cfg["experience_score_map"]
    edu_map = cfg["education_score_map"]

    # 薪资百分位排名
    all_salaries = []
    for j in jobs:
        avg = _get_salary_avg(j)
        if avg and avg > 0:
            all_salaries.append(avg)
    sorted_sal = sorted(all_salaries) if all_salaries else []

    def salary_pct(avg: Optional[float]) -> float:
        if not avg or avg <= 0 or not sorted_sal:
            return 50.0  # 无薪资数据给中等分
        return sum(1 for x in sorted_sal if x < avg) / len(sorted_sal) * 100

    def skill_breadth_score(job: dict) -> float:
        n = len(job.get("tech_stack", [])) + len(job.get("languages", []))
        return min(n / 10 * 100, 100)

    for j in jobs:
        company_type = j.get("company_type", "")
        # 尝试匹配公司类型
        prestige = 50.0
        for key, val in prestige_map.items():
            if key in str(company_type):
                prestige = float(val)
                break

        salary = salary_pct(_get_salary_avg(j))
        exp = exp_map.get(j.get("job_level", "未标注"), 45)
        skill_b = skill_breadth_score(j)
        edu = edu_map.get(j.get("education_std", "本科"), 70)

        quality = (salary * cfg["salary_weight"] +
                   prestige * cfg["company_prestige_weight"] +
                   exp * cfg["experience_level_weight"] +
                   skill_b * cfg["skill_breadth_weight"] +
                   edu * cfg["education_weight"])
        j["quality_score"] = round(quality, 1)

    return jobs


# ============================================================
# 3. 技能溢价指数 (Skill Premium Index)
# ============================================================
def compute_skill_premium(jobs: list[dict], config: dict) -> dict[str, dict]:
    """计算每个技能的薪资溢价百分比"""
    cfg = config["skill_premium_index"]
    min_n = cfg["min_sample_size"]
    baseline_pct = cfg["baseline_percentile"]

    # 只取有薪资的岗位
    jobs_sal = []
    for j in jobs:
        avg = _get_salary_avg(j)
        if avg and avg > 0:
            jc = j.copy()
            jc["_salary_avg"] = avg
            jobs_sal.append(jc)

    if not jobs_sal:
        return {}

    # 按类别和等级分组计算基线
    groups = defaultdict(list)
    for j in jobs_sal:
        cat = j.get("main_category", "其他")
        lv = j.get("job_level", "未标注")
        groups[(cat, lv)].append(j["_salary_avg"])

    group_baselines = {}
    for key, salaries in groups.items():
        sorted_s = sorted(salaries)
        idx = max(0, int(len(sorted_s) * baseline_pct / 100))
        group_baselines[key] = sorted_s[idx]

    # 计算各技能溢价
    skill_salaries = defaultdict(list)
    skill_groups = defaultdict(set)
    for j in jobs_sal:
        cat = j.get("main_category", "其他")
        lv = j.get("job_level", "未标注")
        group_key = (cat, lv)
        for s in set(j.get("tech_stack", []) + j.get("languages", [])):
            skill_salaries[s].append(j["_salary_avg"])
            skill_groups[s].add(group_key)

    premium = {}
    for skill, salaries in skill_salaries.items():
        if len(salaries) < min_n:
            continue
        avg_sal = sum(salaries) / len(salaries)
        # 取所有相关组的平均基线
        baselines = [group_baselines.get(g, avg_sal) for g in skill_groups[skill]]
        baseline = sum(baselines) / len(baselines) if baselines else avg_sal * 0.7

        pct = (avg_sal - baseline) / baseline * 100 if baseline > 0 else 0
        premium[skill] = {
            "premium_pct": round(pct, 1),
            "avg_salary_wan": round(avg_sal, 1),
            "baseline_salary_wan": round(baseline, 1),
            "sample_size": len(salaries),
        }

    return dict(sorted(premium.items(),
                       key=lambda x: x[1]["premium_pct"], reverse=True))


# ============================================================
# 4. 类别匹配分 (Category Match Score, 0-100)
# ============================================================
def compute_all_category_match_scores(jobs: list[dict], config: dict) -> list[dict]:
    """计算每个岗位与其指定类别的匹配度"""
    cfg = config["category_match_score"]
    profiles = config.get("canonical_profiles", {})

    for j in jobs:
        cat = j.get("main_category", "")
        profile = profiles.get(cat, {})
        if not profile:
            j["category_match_score"] = 50.0  # 无画像时给中等分
            continue

        # 技术栈重叠
        job_tech = set(j.get("tech_stack", []))
        ideal_tech = set(profile.get("ideal_tech", []))
        tech_overlap = (len(job_tech & ideal_tech) / len(ideal_tech) * 100
                        if ideal_tech else 50)

        # 编程语言重叠
        job_langs = set(j.get("languages", []))
        ideal_langs = set(profile.get("ideal_langs", []))
        lang_overlap = (len(job_langs & ideal_langs) / len(ideal_langs) * 100
                        if ideal_langs else 50)

        # 标题关键词匹配
        ideal_kws = set()
        import sys
        import os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'feature_engineering(特征工程)'))
        from labeler import MAIN_CATEGORIES
        if cat in MAIN_CATEGORIES:
            ideal_kws = set(MAIN_CATEGORIES[cat]["keywords"])
        title_lower = j.get("title", "").lower()
        title_overlap = (sum(1 for kw in ideal_kws if kw.lower() in title_lower) /
                         max(len(ideal_kws), 1) * 100)

        # 行业匹配
        job_ind = set(j.get("industry_tags", []))
        ideal_ind = set(profile.get("ideal_industries", []))
        ind_overlap = (len(job_ind & ideal_ind) / len(ideal_ind) * 100
                       if ideal_ind else 50)

        score = (title_overlap * cfg["title_weight"] +
                 tech_overlap * cfg["tech_stack_weight"] +
                 lang_overlap * cfg["language_weight"] +
                 ind_overlap * cfg["industry_weight"])
        j["category_match_score"] = round(min(score, 100), 1)

    return jobs


# ============================================================
# Orchestration
# ============================================================
def compute_all_scores(jobs: list[dict], config: dict) -> dict:
    """运行全部4个评分维度"""
    print("  [1/4] 计算技能需求分...")
    skill_demand = compute_skill_demand_scores(jobs, config)

    print("  [2/4] 计算岗位质量分...")
    jobs_with_quality = compute_job_quality_scores(jobs, config)

    print("  [3/4] 计算技能溢价指数...")
    skill_premium = compute_skill_premium(jobs, config)

    print("  [4/4] 计算类别匹配分...")
    jobs_with_all = compute_all_category_match_scores(jobs_with_quality, config)

    return {
        "skill_demand": skill_demand,
        "skill_premium": skill_premium,
        "jobs_with_scores": jobs_with_all,
        "metadata": {
            "total_jobs": len(jobs),
            "scoring_date": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "config_version": "scoring_rules.yaml",
        },
    }


def print_ranking_report(ratings: dict):
    """格式化输出评分排名报告"""
    skill_demand = ratings.get("skill_demand", {})
    skill_premium = ratings.get("skill_premium", {})
    jobs = ratings.get("jobs_with_scores", [])

    print("\n" + "=" * 60)
    print("  【技能需求分 Top 20】")
    print("=" * 60)
    for i, (skill, score) in enumerate(list(skill_demand.items())[:20], 1):
        bar = "█" * max(1, int(score / 4))
        print(f"  {i:>2}. {skill:<22} {score:>5.1f}  {bar}")

    print("\n" + "=" * 60)
    print("  【技能溢价指数 Top 15】")
    print("=" * 60)
    print(f"  {'技能':<22} {'溢价%':>7} {'平均薪资':>8} {'基线薪资':>8} {'样本':>5}")
    print(f"  {'-'*52}")
    for i, (skill, info) in enumerate(list(skill_premium.items())[:15], 1):
        pm = info["premium_pct"]
        sign = "+" if pm >= 0 else ""
        print(f"  {i:>2}. {skill:<20} {sign}{pm:>6.1f}% "
              f"{info['avg_salary_wan']:>6.1f}万 {info['baseline_salary_wan']:>6.1f}万 "
              f"{info['sample_size']:>4}")

    print("\n" + "=" * 60)
    print("  【岗位质量分 Top 10】")
    print("=" * 60)
    sorted_jobs = sorted(jobs, key=lambda j: j.get("quality_score", 0), reverse=True)
    for i, j in enumerate(sorted_jobs[:10], 1):
        avg_sal = _get_salary_avg(j) or 0
        print(f"  {i:>2}. [{j.get('main_category', '?'):<10}] {j.get('title', '')[:30]:<30} "
              f"质量分: {j.get('quality_score', 0):.0f} "
              f"薪资: {avg_sal:.1f}万/月")

    print("\n" + "=" * 60)
    print("  【类别匹配分 Bottom 10】 (可能分类不准确)")
    print("=" * 60)
    sorted_match = sorted(jobs, key=lambda j: j.get("category_match_score", 100))
    for i, j in enumerate(sorted_match[:10], 1):
        print(f"  {i:>2}. [{j.get('main_category', '?'):<10}] {j.get('title', '')[:35]:<35} "
              f"匹配分: {j.get('category_match_score', 0):.0f}")


def save_ratings(ratings: dict, filepath: str = "data/ratings.json"):
    """保存评分数据"""
    # 精简保存：只保留关键评分字段
    output = {
        "metadata": ratings["metadata"],
        "skill_demand": ratings["skill_demand"],
        "skill_premium": ratings["skill_premium"],
        "top_jobs_by_quality": [],
        "bottom_jobs_by_match": [],
    }

    jobs = ratings["jobs_with_scores"]
    # Top 20 质量分
    for j in sorted(jobs, key=lambda x: x.get("quality_score", 0), reverse=True)[:20]:
        output["top_jobs_by_quality"].append({
            "title": j.get("title"),
            "company": j.get("company"),
            "main_category": j.get("main_category"),
            "quality_score": j.get("quality_score"),
            "category_match_score": j.get("category_match_score"),
        })

    # Bottom 20 匹配分
    for j in sorted(jobs, key=lambda x: x.get("category_match_score", 100))[:20]:
        output["bottom_jobs_by_match"].append({
            "title": j.get("title"),
            "company": j.get("company"),
            "main_category": j.get("main_category"),
            "quality_score": j.get("quality_score"),
            "category_match_score": j.get("category_match_score"),
        })

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n评分数据已保存至 {filepath}")

    # 同时保存完整评分版本（供其他模块使用）
    full_path = filepath.replace(".json", "_full.json")
    with open(full_path, "w", encoding="utf-8") as f:
        json.dump(ratings, f, ensure_ascii=False, indent=2)


def main():
    print("=" * 60)
    print("  大数据岗位评分体系 (Scoring Engine)")
    print("=" * 60)

    jobs = load_labeled_data()
    print(f"加载 {len(jobs)} 条岗位数据")

    config = load_scoring_config()
    print("加载评分规则配置")

    ratings = compute_all_scores(jobs, config)
    print_ranking_report(ratings)
    save_ratings(ratings)


if __name__ == "__main__":
    main()
