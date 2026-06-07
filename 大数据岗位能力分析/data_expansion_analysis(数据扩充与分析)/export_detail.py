#!/usr/bin/env python3
"""
生成精细化岗位特征表
- 薪资拆分为多个独立字段（月薪/年薪/区间/公积金等）
- 岗位描述拆解为 20 个结构化特征元素
"""
import json
import re
import os


def load_data():
    with open("data/labeled_jobs.json", "r", encoding="utf-8") as f:
        labeled = json.load(f)["jobs"]

    # 合并 processed 数据中的完整 job_desc
    if os.path.exists("data/processed_jobs.json"):
        with open("data/processed_jobs.json", "r", encoding="utf-8") as f:
            processed = json.load(f)
        pidx = {(j.get("title", ""), j.get("company", "")): j for j in processed}
        for job in labeled:
            key = (job.get("title", ""), job.get("company", ""))
            if key in pidx:
                p = pidx[key]
                job["_desc_full"] = p.get("job_desc_clean") or p.get("job_desc", "")
                job["_tokens"] = p.get("tokens", [])
                job["_exp_raw"] = job.get("experience_std") or p.get("experience", "")
                job["_edu_raw"] = job.get("education_std") or p.get("education", "")
                job["_welfare"] = p.get("welfare", "")
                job["_company_type"] = job.get("company_type") or p.get("company_type", "")
                job["_company_size"] = job.get("company_size") or p.get("company_size", "")
                if not job.get("salary_months"):
                    job["salary_months"] = p.get("salary_months")

    return labeled


def split_salary(job):
    """薪资拆分为多个独立字段"""
    smin = job.get("salary_min_k")
    smax = job.get("salary_max_k")
    unit = job.get("salary_unit", "月")
    months = job.get("salary_months", 12) or 12
    raw = job.get("salary", "")

    fields = {}

    # 月薪
    fields["salary_min_wan_month"] = smin  # 最低月薪(万)
    fields["salary_max_wan_month"] = smax  # 最高月薪(万)

    # 均值
    if smin and smax:
        fields["salary_avg_wan_month"] = round((smin + smax) / 2, 1)
    else:
        fields["salary_avg_wan_month"] = None

    # 年薪估算 (月薪 × 月份数)
    if smin and smax:
        fields["salary_min_wan_year"] = round(smin * months, 1)
        fields["salary_max_wan_year"] = round(smax * months, 1)
        fields["salary_avg_wan_year"] = round((smin + smax) / 2 * months, 1)
    else:
        fields["salary_min_wan_year"] = None
        fields["salary_max_wan_year"] = None
        fields["salary_avg_wan_year"] = None

    # 薪资月数
    fields["salary_months"] = months

    # 薪资跨度 (最高/最低比值)
    if smin and smax and smin > 0:
        fields["salary_range_ratio"] = round(smax / smin, 1)
    else:
        fields["salary_range_ratio"] = None

    # 薪资等级
    if smax:
        if smax < 2.0:
            fields["salary_tier"] = "低薪(<2万)"
        elif smax < 3.5:
            fields["salary_tier"] = "中等(2-3.5万)"
        elif smax < 5.5:
            fields["salary_tier"] = "高薪(3.5-5.5万)"
        else:
            fields["salary_tier"] = "超高薪(>5.5万)"
    else:
        fields["salary_tier"] = None

    # 原始薪资字符串
    fields["salary_raw"] = raw

    return fields


def extract_20_features(job):
    """将岗位信息拆解为 20 个结构化特征元素"""
    desc = job.get("_desc_full", job.get("job_desc_preview", ""))
    title = job.get("title", "")

    feats = {}

    # 01 岗位名称
    feats["F01_岗位名称"] = title

    # 02 公司名称
    feats["F02_公司名称"] = job.get("company", "")

    # 03 工作城市
    loc = job.get("location", "")
    city = loc.split("-")[0] if "-" in loc else loc
    district = loc.split("-")[1] if "-" in loc else ""
    feats["F03_城市"] = city
    feats["F03_区域"] = district

    # 04 岗位大类
    feats["F04_岗位大类"] = job.get("main_category", "")

    # 05 经验等级
    feats["F05_经验等级"] = job.get("job_level", "")

    # 06 经验要求(原始)
    feats["F06_经验要求"] = job.get("experience_std", "")

    # 07 学历要求
    feats["F07_学历要求"] = job.get("education_std", "")

    # 08 编程语言需求
    feats["F08_编程语言"] = "、".join(job.get("languages", []))

    # 09 核心技术栈
    feats["F09_技术栈"] = "、".join(job.get("tech_stack", []))

    # 10 大数据组件需求
    bigdata_components = [
        "Hadoop", "Spark", "Flink", "Hive", "HBase", "Kafka", "Zookeeper",
        "Presto", "ClickHouse", "Doris", "StarRocks", "Hudi", "Iceberg",
        "DataX", "DolphinScheduler", "Airflow", "Flink CDC", "ElasticSearch"
    ]
    found = [c for c in bigdata_components if c.lower() in desc.lower()]
    feats["F10_大数据组件"] = "、".join(found) if found else "未明确"

    # 11 数据库需求
    db_keywords = ["MySQL", "TiDB", "Redis", "MongoDB", "HBase", "PostgreSQL",
                   "Oracle", "SQL Server", "OceanBase", "Greenplum"]
    found_db = [d for d in db_keywords if d.lower() in desc.lower()]
    feats["F11_数据库"] = "、".join(found_db) if found_db else "未明确"

    # 12 云平台
    feats["F12_云平台"] = "、".join(job.get("cloud_platforms", [])) or "未明确"

    # 13 行业标签
    feats["F13_行业标签"] = "、".join(job.get("industry_tags", []))

    # 14 核心职责（从 JD 提取）
    duty_lines = []
    for line in desc.split("\n"):
        line = line.strip()
        if re.match(r"^\d+[\.\、\)）]", line) and "岗位职责" not in line and "任职要求" not in line:
            if len(duty_lines) < 3:
                duty_lines.append(re.sub(r"^\d+[\.\、\)）]\s*", "", line))
    # fallback: 从关键词推断
    if not duty_lines:
        kws = [kw[0] if isinstance(kw, list) else kw
               for kw in (job.get("keywords", []) or [])[:5]]
        duty_lines = kws
    feats["F14_核心职责(前3项)"] = " | ".join(duty_lines[:3])

    # 15 关键词 Top 5
    kws = [kw[0] if isinstance(kw, list) else kw
           for kw in (job.get("keywords", []) or [])[:5]]
    feats["F15_关键词Top5"] = "、".join(kws)

    # 16 公司类型
    feats["F16_公司类型"] = job.get("_company_type", "")

    # 17 公司规模
    feats["F17_公司规模"] = job.get("_company_size", "")

    # 18 公司行业
    feats["F18_公司行业"] = job.get("industry", "")

    # 19 福利待遇
    feats["F19_福利待遇"] = job.get("_welfare", "")

    # 20 综合标签
    feats["F20_综合标签"] = "、".join(job.get("combined_tags", []))

    return feats


def main():
    jobs = load_data()
    print(f"处理 {len(jobs)} 条岗位数据...")

    rows = []
    for i, job in enumerate(jobs):
        # 薪资拆解
        salary_fields = split_salary(job)
        # 20 特征提取
        feature_fields = extract_20_features(job)

        row = {}
        row.update(feature_fields)
        row.update(salary_fields)
        rows.append(row)

        if (i + 1) % 30 == 0:
            print(f"  已处理 {i+1}/{len(jobs)}")

    # 导出 Excel
    import pandas as pd

    # 定义列顺序
    columns = [
        # 基础信息 (4列)
        "F01_岗位名称", "F02_公司名称", "F03_城市", "F03_区域",
        # 分类信息 (4列)
        "F04_岗位大类", "F05_经验等级", "F06_经验要求", "F07_学历要求",
        # 薪资信息 (8列)
        "salary_raw", "salary_tier", "salary_min_wan_month", "salary_max_wan_month",
        "salary_avg_wan_month", "salary_months",
        "salary_min_wan_year", "salary_max_wan_year", "salary_avg_wan_year",
        "salary_range_ratio",
        # 技术需求 (5列)
        "F08_编程语言", "F09_技术栈", "F10_大数据组件", "F11_数据库", "F12_云平台",
        # 业务信息 (3列)
        "F13_行业标签", "F14_核心职责(前3项)", "F15_关键词Top5",
        # 公司信息 (3列)
        "F16_公司类型", "F17_公司规模", "F18_公司行业",
        # 其他 (2列)
        "F19_福利待遇", "F20_综合标签",
    ]
    # 确保所有列存在
    for c in columns:
        if c not in rows[0]:
            print(f"Warning: column {c} not found")

    df = pd.DataFrame(rows, columns=columns)

    # 列名简化（去掉前缀）
    rename_map = {c: c.replace("F0", "").replace("F1", "").replace("F2", "")
                  for c in columns}
    rename_map = {
        "F01_岗位名称": "岗位名称",
        "F02_公司名称": "公司名称",
        "F03_城市": "城市",
        "F03_区域": "区域",
        "F04_岗位大类": "岗位大类",
        "F05_经验等级": "经验等级",
        "F06_经验要求": "经验要求",
        "F07_学历要求": "学历要求",
        "F08_编程语言": "编程语言需求",
        "F09_技术栈": "核心技术栈",
        "F10_大数据组件": "大数据组件需求",
        "F11_数据库": "数据库需求",
        "F12_云平台": "云平台",
        "F13_行业标签": "行业标签",
        "F14_核心职责(前3项)": "核心职责",
        "F15_关键词Top5": "热词Top5",
        "F16_公司类型": "公司类型",
        "F17_公司规模": "公司规模",
        "F18_公司行业": "公司行业",
        "F19_福利待遇": "福利待遇",
        "F20_综合标签": "综合标签",
    }
    df = df.rename(columns=rename_map)

    os.makedirs("data", exist_ok=True)
    filepath = "data/bigdata_jobs_detail.xlsx"
    df.to_excel(filepath, index=False, engine="openpyxl")

    print(f"\n表格已生成: {filepath}")
    print(f"共 {len(df)} 行 × {len(df.columns)} 列")
    print(f"\n列结构:")
    print(f"  基础信息(4): 岗位名称 | 公司名称 | 城市 | 区域")
    print(f"  分类信息(4): 岗位大类 | 经验等级 | 经验要求 | 学历要求")
    print(f"  薪资拆解(9): 原始薪资 | 薪资等级 | 月薪min/max/avg | 月数 | 年薪min/max/avg | 薪资跨度")
    print(f"  技术需求(5): 编程语言 | 技术栈 | 大数据组件 | 数据库 | 云平台")
    print(f"  业务信息(3): 行业标签 | 核心职责 | 热词Top5")
    print(f"  公司信息(3): 公司类型 | 公司规模 | 公司行业")
    print(f"  其他(2): 福利待遇 | 综合标签")


if __name__ == "__main__":
    main()
