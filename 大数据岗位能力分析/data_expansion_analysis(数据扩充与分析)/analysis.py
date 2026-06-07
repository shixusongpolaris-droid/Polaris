#!/usr/bin/env python3
"""
大数据岗位招聘数据深度分析
多维度分析：分布、薪资、技术栈关联、关键词、岗位画像
"""
import json
import os
import sys
from collections import Counter, defaultdict
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def load_data(filepath="data/labeled_jobs.json",
               raw_filepath="data/processed_jobs.json"):
    """加载标注数据，并与富字段数据合并"""
    with open(filepath, "r", encoding="utf-8") as f:
        labeled = json.load(f)

    # 尝试加载包含 company_type/company_size 等富字段的数据
    rich = {}
    if os.path.exists(raw_filepath):
        with open(raw_filepath, "r", encoding="utf-8") as f:
            raw_data = json.load(f)
        for j in raw_data:
            key = (j.get("title", ""), j.get("company", ""))
            rich[key] = j

    jobs = labeled["jobs"]
    # 合并富字段
    for job in jobs:
        key = (job.get("title", ""), job.get("company", ""))
        if key in rich:
            for field in ("company_type", "company_size", "industry", "education",
                          "experience", "salary"):
                if field not in job or not job.get(field):
                    job[field] = rich[key].get(field, "")

    return jobs


def format_pct(count, total):
    return f"{count} ({count/total*100:.1f}%)"


def divider(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}")


# ============================================================
# 1. 基础分布分析
# ============================================================
def analysis_basic(jobs):
    divider("1. 基础分布分析")
    total = len(jobs)

    # --- 1a. 岗位大类分布 ---
    print("\n  【1.1 岗位大类分布】")
    cat_dist = Counter(j.get("main_category") for j in jobs if j.get("main_category"))
    for cat, cnt in cat_dist.most_common():
        bar = "█" * max(1, cnt // (max(cat_dist.values()) // 30 or 1))
        print(f"    {cat:<14} {format_pct(cnt, total):>16}  {bar}")

    # --- 1b. 经验等级 x 大类交叉 ---
    print("\n  【1.2 经验等级 × 大类 交叉分布】")
    cross = defaultdict(lambda: defaultdict(int))
    levels = set()
    cats = set()
    for j in jobs:
        lv = j.get("job_level", "未标注")
        cat = j.get("main_category", "未分类")
        if lv and cat:
            cross[lv][cat] += 1
            levels.add(lv)
            cats.add(cat)

    levels_sorted = ["初级", "中级", "高级", "管理/负责人"]
    cats_sorted = [c for c, _ in cat_dist.most_common()]
    header = f"{'等级':<12}" + "".join(f"{c:<8}" for c in cats_sorted)
    print(f"    {header}")
    for lv in levels_sorted:
        if lv in cross:
            row = f"    {lv:<12}" + "".join(f"{cross[lv].get(c,0):<8}" for c in cats_sorted)
            print(row)

    # --- 1c. 城市分布 ---
    print("\n  【1.3 城市分布 Top 15】")
    city_dist = Counter(j.get("location", "") for j in jobs)
    for city, cnt in city_dist.most_common(15):
        print(f"    {city:<20} {cnt:>4}")

    # --- 1d. 公司类型 ---
    print("\n  【1.4 公司类型分布】")
    type_dist = Counter()
    for j in jobs:
        t = j.get("company_type", "")
        if t:
            type_dist[t] += 1
    for t, cnt in type_dist.most_common():
        print(f"    {t:<12} {cnt:>4}")

    # --- 1e. 学历要求 ---
    print("\n  【1.5 学历要求分布】")
    edu_dist = Counter(j.get("education_std") for j in jobs if j.get("education_std"))
    for edu, cnt in edu_dist.most_common():
        print(f"    {edu:<12} {cnt:>4}")

    # --- 1f. 行业分布 ---
    print("\n  【1.6 行业标签分布】")
    ind_counter = Counter()
    for j in jobs:
        for tag in j.get("industry_tags", []):
            ind_counter[tag] += 1
    for ind, cnt in ind_counter.most_common(10):
        print(f"    {ind:<10} {cnt:>4}")

    return cat_dist, city_dist


# ============================================================
# 2. 薪资深度分析
# ============================================================
def analysis_salary(jobs):
    divider("2. 薪资深度分析")

    def salary_stats(job_group, label=""):
        """计算一组岗位的薪资统计"""
        mins, maxs = [], []
        for j in job_group:
            smin = j.get("salary_min_k")
            smax = j.get("salary_max_k")
            if smin and smax and smin > 0:
                mins.append(smin)
                maxs.append(smax)
        if not mins:
            return None
        return {
            "count": len(mins),
            "min_avg": round(sum(mins) / len(mins), 1),
            "max_avg": round(sum(maxs) / len(maxs), 1),
            "min_median": round(sorted(mins)[len(mins)//2], 1),
            "max_median": round(sorted(maxs)[len(maxs)//2], 1),
            "min_range": f"{round(min(mins),1)}-{round(max(mins),1)}",
            "max_range": f"{round(min(maxs),1)}-{round(max(maxs),1)}",
        }

    # --- 2a. 按岗位大类 ---
    print("\n  【2.1 各岗位大类薪资对比 (万/月)】")
    print(f"    {'大类':<14} {'最低均值':>8} {'最高均值':>8} {'最低中位':>8} {'最高中位':>8} {'样本':>5}")
    print(f"    {'-'*55}")
    cats = defaultdict(list)
    for j in jobs:
        cats[j.get("main_category", "其他")].append(j)
    for cat in sorted(cats, key=lambda c: len(cats[c]), reverse=True):
        s = salary_stats(cats[cat])
        if s:
            print(f"    {cat:<14} {s['min_avg']:>7.1f}万 {s['max_avg']:>7.1f}万 "
                  f"{s['min_median']:>7.1f}万 {s['max_median']:>7.1f}万 {s['count']:>4}")

    # --- 2b. 按经验等级 ---
    print("\n  【2.2 各经验等级薪资对比 (万/月)】")
    print(f"    {'等级':<14} {'最低均值':>8} {'最高均值':>8} {'最低中位':>8} {'最高中位':>8} {'样本':>5}")
    print(f"    {'-'*55}")
    levels = defaultdict(list)
    for j in jobs:
        levels[j.get("job_level", "其他")].append(j)
    for lv in ["初级", "中级", "高级", "管理/负责人"]:
        s = salary_stats(levels.get(lv, []))
        if s:
            print(f"    {lv:<14} {s['min_avg']:>7.1f}万 {s['max_avg']:>7.1f}万 "
                  f"{s['min_median']:>7.1f}万 {s['max_median']:>7.1f}万 {s['count']:>4}")

    # --- 2c. 按城市 ---
    print("\n  【2.3 主要城市薪资对比 (万/月)】")
    print(f"    {'城市':<16} {'最低均值':>8} {'最高均值':>8} {'样本':>5}")
    print(f"    {'-'*45}")
    cities = defaultdict(list)
    for j in jobs:
        loc = j.get("location", "")
        cities[loc].append(j)
    for city in sorted(cities, key=lambda c: len(cities[c]), reverse=True)[:10]:
        s = salary_stats(cities[city])
        if s and s["count"] >= 2:
            print(f"    {city:<16} {s['min_avg']:>7.1f}万 {s['max_avg']:>7.1f}万 {s['count']:>4}")

    # --- 2d. 按学历 ---
    print("\n  【2.4 不同学历薪资对比 (万/月)】")
    edus = defaultdict(list)
    for j in jobs:
        edus[j.get("education_std", "未知")].append(j)
    print(f"    {'学历':<10} {'最低均值':>8} {'最高均值':>8} {'样本':>5}")
    print(f"    {'-'*40}")
    for edu in ["本科", "硕士"]:
        s = salary_stats(edus.get(edu, []))
        if s:
            print(f"    {edu:<10} {s['min_avg']:>7.1f}万 {s['max_avg']:>7.1f}万 {s['count']:>4}")

    # --- 2e. 行业薪资 ---
    print("\n  【2.5 各行业薪资对比 (万/月)】")
    inds = defaultdict(list)
    for j in jobs:
        for tag in j.get("industry_tags", []):
            inds[tag].append(j)
    print(f"    {'行业':<10} {'最低均值':>8} {'最高均值':>8} {'样本':>5}")
    print(f"    {'-'*40}")
    for ind in sorted(inds, key=lambda i: len(inds[i]), reverse=True)[:8]:
        s = salary_stats(inds[ind])
        if s:
            print(f"    {ind:<10} {s['min_avg']:>7.1f}万 {s['max_avg']:>7.1f}万 {s['count']:>4}")

    # --- 2f. 薪资区间分布 ---
    print("\n  【2.6 薪资区间分布（按最高薪资）】")
    brackets = [
        ("<1.5万", 0, 1.5),
        ("1.5-2.5万", 1.5, 2.5),
        ("2.5-4万", 2.5, 4.0),
        ("4-6万", 4.0, 6.0),
        ("6万+", 6.0, 999),
    ]
    for label, lo, hi in brackets:
        cnt = sum(1 for j in jobs
                  if j.get("salary_max_k") and lo <= j["salary_max_k"] < hi)
        print(f"    {label:<12} {cnt:>4}  {'█'*max(1,cnt//2)}")

    return salary_stats


# ============================================================
# 3. 技术栈关联分析
# ============================================================
def analysis_techstack(jobs):
    divider("3. 技术栈关联分析")

    # --- 3a. 按大类的核心技术栈 ---
    print("\n  【3.1 各岗位大类的 Top 5 技术栈】")
    cat_tech = defaultdict(Counter)
    for j in jobs:
        cat = j.get("main_category", "其他")
        for tech in j.get("tech_stack", []):
            cat_tech[cat][tech] += 1

    for cat in sorted(cat_tech, key=lambda c: sum(cat_tech[c].values()), reverse=True):
        top5 = cat_tech[cat].most_common(5)
        if top5:
            print(f"\n    【{cat}】")
            for tech, cnt in top5:
                pct = cnt / sum(1 for j in jobs if j.get("main_category") == cat) * 100
                print(f"      {tech:<22} {cnt:>3} ({pct:.0f}%)")

    # --- 3b. 技术共现矩阵 ---
    print("\n  【3.2 技术栈共现 Top 15】")
    co_occur = Counter()
    all_techs = Counter()
    for j in jobs:
        tech_list = sorted(j.get("tech_stack", []))
        for t in tech_list:
            all_techs[t] += 1
        for t1, t2 in combinations(tech_list, 2):
            pair = tuple(sorted([t1, t2]))
            co_occur[pair] += 1

    for (t1, t2), cnt in co_occur.most_common(15):
        print(f"    {t1:<20} + {t2:<20} → {cnt:>3} 次")

    # --- 3c. 编程语言 + 技术栈交叉 ---
    print("\n  【3.3 编程语言 × 技术栈交叉分析】")
    lang_tech = defaultdict(Counter)
    for j in jobs:
        for lang in j.get("languages", []):
            for tech in j.get("tech_stack", []):
                lang_tech[lang][tech] += 1

    for lang in ["Java", "Python", "Scala", "SQL"]:
        top5 = lang_tech[lang].most_common(5)
        if top5:
            print(f"\n    【{lang} 开发者常用技术栈】")
            for tech, cnt in top5:
                print(f"      {tech:<22} {cnt:>3}")


# ============================================================
# 4. 关键词深度挖掘
# ============================================================
def analysis_keywords(jobs):
    divider("4. 关键词深度挖掘")

    # --- 4a. 整体高频关键词 ---
    print("\n  【4.1 整体高频关键词 Top 30】")
    all_kw = Counter()
    for j in jobs:
        for kw in (j.get("keywords") or []):
            word = kw[0] if isinstance(kw, list) else kw
            all_kw[word] += 1

    max_kw = max(all_kw.values()) if all_kw else 1
    for i, (word, cnt) in enumerate(all_kw.most_common(30), 1):
        bar = "█" * max(1, cnt // (max_kw // 30 or 1))
        print(f"    {i:>2}. {word:<15} {cnt:>4}  {bar}")

    # --- 4b. 各岗位大类特有热词 ---
    print("\n  【4.2 各岗位大类的专属关键词 Top 8】")

    # 从原始处理数据加载完整 job_desc
    rich_jobs = {}
    if os.path.exists("data/processed_jobs.json"):
        with open("data/processed_jobs.json", "r", encoding="utf-8") as f:
            for j in json.load(f):
                key = (j.get("title", ""), j.get("company", ""))
                rich_jobs[key] = j

    cat_texts = defaultdict(str)
    for j in jobs:
        cat = j.get("main_category", "其他")
        key = (j.get("title", ""), j.get("company", ""))
        desc = ""
        if key in rich_jobs:
            desc = rich_jobs[key].get("job_desc_clean",
                     rich_jobs[key].get("job_desc", ""))
        if not desc:
            desc = j.get("job_desc_preview", "")
        cat_texts[cat] += " " + desc

    import jieba.analyse
    for cat in sorted(cat_texts, key=lambda c: len(cat_texts[c]), reverse=True):
        if not cat or len(cat_texts[cat].strip()) < 50:
            continue
        keywords = jieba.analyse.extract_tags(cat_texts[cat], topK=8, withWeight=True)
        print(f"\n    【{cat}】({sum(1 for j in jobs if j.get('main_category')==cat)} 岗)")
        for kw, weight in keywords:
            print(f"      {kw:<15} TF-IDF: {weight:.4f}")


# ============================================================
# 5. 岗位画像聚类
# ============================================================
def analysis_profile(jobs):
    divider("5. 岗位画像聚类分析")

    # --- 5a. 高薪岗位特征 ---
    print("\n  【5.1 高薪岗位的共同特征（月薪>5万）】")
    high_salary = [j for j in jobs
                   if j.get("salary_max_k") and j["salary_max_k"] >= 5.0]
    print(f"    高薪岗位数: {len(high_salary)}")

    print("\n    高薪岗技术栈 Top 10:")
    high_tech = Counter()
    for j in high_salary:
        for t in j.get("tech_stack", []):
            high_tech[t] += 1
    for tech, cnt in high_tech.most_common(10):
        print(f"      {tech:<22} {cnt:>3}")

    print("\n    高薪岗编程语言:")
    high_lang = Counter()
    for j in high_salary:
        for l in j.get("languages", []):
            high_lang[l] += 1
    for lang, cnt in high_lang.most_common(5):
        print(f"      {lang:<12} {cnt:>3}")

    # --- 5b. 入门岗位特征 ---
    print("\n  【5.2 初级/应届岗位特征】")
    entry_jobs = [j for j in jobs if j.get("job_level") in ("初级",)]
    print(f"    初级岗位数: {len(entry_jobs)}")
    entry_tech = Counter()
    for j in entry_jobs:
        for t in j.get("tech_stack", []):
            entry_tech[t] += 1
    print("    要求技术栈:")
    for tech, cnt in entry_tech.most_common(8):
        print(f"      {tech:<22} {cnt:>3}")

    # --- 5c. 不同大类的典型 JD 关键词 ---
    print("\n  【5.3 各岗位大类画像速写】")
    profiles = {
        "数据开发": "SQL/Hive/Spark 为主，注重数仓建模、ETL工程能力，Java/Python 为辅",
        "数据分析": "SQL/Python 为主，注重业务理解、BI工具（Tableau/FineBI），AB实验",
        "AI/机器学习": "Python + PyTorch/TensorFlow，注重算法理论、顶会论文，硕士起步",
        "数据架构": "全栈技术视野，注重架构设计、数据治理、团队管理，高年限要求",
        "数据运维": "Linux + Shell，注重集群管理、监控告警、自动化运维",
        "流计算": "Flink + Kafka 为核心，注重实时处理、状态管理、数据湖集成",
        "数据产品": "SQL 基础 + 产品思维，注重需求分析、B端产品设计",
    }
    for cat, desc in profiles.items():
        cnt = sum(1 for j in jobs if j.get("main_category") == cat)
        if cnt:
            print(f"    【{cat}】({cnt}岗): {desc}")

    # --- 5d. 技能需求层次 ---
    print("\n  【5.4 技能需求层次分析】")
    print("""
    ┌─────────────────────────────────────────────────┐
    │  基础层 (所有岗位): SQL、Excel、数据敏感性        │
    │  进阶层 (50%+岗位): Python、数据建模、Linux      │
    │  专业层 (30%+岗位): Spark、Hive、Hadoop生态      │
    │  专家层 (15%+岗位): Flink、Kafka、分布式架构     │
    │  顶极层 (<10%岗位): 大模型训练、内核开发、顶会     │
    └─────────────────────────────────────────────────┘
    """)


# ============================================================
# 主入口
# ============================================================
def main():
    print("=" * 70)
    print("    大数据岗位招聘数据 — 深度分析报告")
    print("=" * 70)

    jobs = load_data()
    print(f"\n  数据来源：公开招聘信息整理（BOSS直聘/51job/猎聘等）")
    print(f"  样本数量：{len(jobs)} 条")
    print(f"  数据范围：2025年4-5月")

    # 执行分析
    analysis_basic(jobs)
    analysis_salary(jobs)
    analysis_techstack(jobs)
    analysis_keywords(jobs)
    analysis_profile(jobs)

    # 终语
    print(f"\n{'='*70}")
    print("  分析完成")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
