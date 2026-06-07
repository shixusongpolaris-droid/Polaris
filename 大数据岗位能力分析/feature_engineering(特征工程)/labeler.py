"""
岗位信息标签与特征处理模块
提取：大类标签、细节标签、技能特征、经验特征、行业特征
"""
import re
import json
from collections import Counter
from typing import Optional


# ============================================================
# 标签体系定义
# ============================================================

# --- 大类标签 (Main Categories) ---
MAIN_CATEGORIES = {
    "数据开发": {
        "keywords": ["数据开发", "大数据开发", "ETL", "数据仓库", "数据平台",
                    "数据工程", "数据管道", "data warehouse", "数仓",
                    "OLAP", "MPP", "数据库内核", "数据湖", "数据集成",
                    "数据同步", "数据调度", "离线开发", "数据建设",
                    "hadoop", "hive开发", "数据存储", "数据采集",
                    "向量存储", "向量数据库", "存储研发", "存储引擎"],
        "description": "负责大数据平台建设、ETL流程、数据仓库开发"
    },
    "数据分析": {
        "keywords": ["数据分析", "商业分析", "BI", "业务分析", "经营分析",
                    "数据运营", "数据报告", "tableau", "finebi", "power bi",
                    "分析报告", "策略分析", "运营分析", "用户分析", "增长分析",
                    "市场分析", "行业分析", "财务分析"],
        "description": "负责数据提取、分析报告、BI报表、业务决策支持"
    },
    "数据挖掘": {
        "keywords": ["数据挖掘", "推荐算法", "用户画像", "特征工程", "数据建模",
                    "预测模型", "风控模型", "信用评分", "聚类", "分类",
                    "搜索算法", "排序模型", "召回模型", "CTR", "CVR",
                    "知识图谱", "图计算", "图数据库", "知识推理"],
        "description": "负责数据建模、算法开发、用户画像、推荐系统"
    },
    "数据架构": {
        "keywords": ["数据架构", "架构设计", "数据治理", "数据标准", "数据模型",
                    "数据中台", "技术选型", "系统设计", "高并发",
                    "数据资产", "数据目录", "元数据管理", "数据血缘"],
        "description": "负责数据系统架构设计、技术选型、数据治理"
    },
    "数据运维": {
        "keywords": ["运维", "DBA", "数据库管理", "集群运维", "监控",
                    "数据安全", "容灾", "备份", "SRE", "devops",
                    "k8s", "kubernetes", "部署", "发布", "CI/CD",
                    "服务治理", "云运维", "平台运维"],
        "description": "负责大数据集群运维、数据库管理、监控告警"
    },
    "AI/机器学习": {
        "keywords": ["机器学习", "深度学习", "NLP", "自然语言", "计算机视觉",
                    "CV", "LLM", "大模型", "AIGC", "神经网络", "transformer",
                    "Agent", "agent", "RAG", "大语言模型", "语言模型",
                    "SFT", "RLHF", "DPO", "Prompt", "prompt",
                    "图像识别", "语音识别", "语义理解", "文本分类",
                    "多模态", "强化学习", "生成式", "生成模型",
                    "扩散模型", "自监督", "预训练", "微调", "推理优化",
                    "PyTorch", "TensorFlow", "分布式训练", "模型压缩",
                    "向量检索", "向量索引", "embedding"],
        "description": "负责机器学习/深度学习模型开发、NLP/CV算法"
    },
    "数据产品": {
        "keywords": ["数据产品", "产品经理", "产品设计", "需求分析",
                    "数据工具", "数据可视化产品", "画像产品",
                    "BI产品", "数据平台产品", "数据治理产品"],
        "description": "负责数据产品规划、需求设计、产品迭代"
    },
    "流计算": {
        "keywords": ["流计算", "实时计算", "Flink", "Kafka Streams", "Storm",
                    "实时数仓", "实时处理", "流处理", "CEP",
                    "实时数据", "实时同步", "CDC", "流批一体"],
        "description": "负责实时计算、流处理、实时数据管道"
    },
    "测试/质量": {
        "keywords": ["数据测试", "质量", "自动化测试", "数据质量",
                    "数据校验", "数据核对", "QA", "测试开发",
                    "质量保障", "质量工程", "质量监控"],
        "description": "负责数据质量测试、数据校验、自动化测试"
    },
}

# --- 非数据类岗位过滤关键词（销售/BD/管理等）---
NON_DATA_KEYWORDS = [
    "销售经理", "销售代表", "销售总监", "客户经理", "商务经理",
    "商务拓展", "BD经理", "BD总监", "商务总监",
    "大客户销售", "渠道销售", "区域销售", "销售运营",
    "KA大客户", "KA销售", "公有云销售", "私有云销售",
    "招商", "加盟", "推广", "地推",
    "人力资源管理", "HRBP", "招聘专员", "行政",
    "财务经理", "出纳", "会计", "法务", "合规经理",
    "公关", "品牌经理", "市场推广", "活动策划",
]

# --- 英文标题到类别的映射 ---
ENGLISH_TITLE_MAP = {
    "data scientist": "数据挖掘",
    "senior data scientist": "数据挖掘",
    "data engineer": "数据开发",
    "senior data engineer": "数据开发",
    "big data engineer": "数据开发",
    "machine learning engineer": "AI/机器学习",
    "ml engineer": "AI/机器学习",
    "senior ml engineer": "AI/机器学习",
    "nlp engineer": "AI/机器学习",
    "computer vision engineer": "AI/机器学习",
    "cv engineer": "AI/机器学习",
    "llm engineer": "AI/机器学习",
    "ai engineer": "AI/机器学习",
    "data architect": "数据架构",
    "data analyst": "数据分析",
    "bi analyst": "数据分析",
    "business analyst": "数据分析",
    "mlops engineer": "数据运维",
    "devops engineer": "数据运维",
    "site reliability engineer": "数据运维",
    "sre": "数据运维",
    "dba": "数据运维",
    "database administrator": "数据运维",
}

# --- 技术栈标签 (Tech Stack) ---
TECH_STACK = {
    "Hadoop生态": {
        "keywords": ["hadoop", "hdfs", "yarn", "mapreduce", "zookeeper", "ambari", "cloudera"],
        "category": "计算框架"
    },
    "Spark": {
        "keywords": ["spark", "spark sql", "spark streaming", "pyspark", "spark mllib", "structured streaming"],
        "category": "计算引擎"
    },
    "Flink": {
        "keywords": ["flink", "flink sql", "flink cdc", "flink streaming"],
        "category": "流计算引擎"
    },
    "Hive": {
        "keywords": ["hive", "hive sql", "hql", "beeline"],
        "category": "数据仓库"
    },
    "HBase": {
        "keywords": ["hbase", "phoenix"],
        "category": "NoSQL数据库"
    },
    "Kafka": {
        "keywords": ["kafka", "消息队列", "mq"],
        "category": "消息中间件"
    },
    "ClickHouse": {
        "keywords": ["clickhouse", "ck"],
        "category": "OLAP数据库"
    },
    "Doris/StarRocks": {
        "keywords": ["doris", "starrocks", "apache doris"],
        "category": "OLAP数据库"
    },
    "ElasticSearch": {
        "keywords": ["elasticsearch", "es", "elk", "kibana", "logstash"],
        "category": "搜索引擎"
    },
    "MySQL": {
        "keywords": ["mysql", "innodb", "tidb"],
        "category": "关系型数据库"
    },
    "Redis": {
        "keywords": ["redis", "缓存"],
        "category": "缓存/KV存储"
    },
    "MongoDB": {
        "keywords": ["mongodb", "mongo"],
        "category": "文档数据库"
    },
    "Flink CDC": {
        "keywords": ["flink cdc", "cdc", "debezium", "canal", "datax"],
        "category": "数据同步"
    },
    "DolphinScheduler": {
        "keywords": ["dolphinscheduler", "azkaban", "oozie", "airflow", "任务调度"],
        "category": "调度系统"
    },
    "Kubernetes/Docker": {
        "keywords": ["kubernetes", "k8s", "docker", "容器", "容器化"],
        "category": "容器/编排"
    },
}

# --- 编程语言标签 ---
PROGRAMMING_LANGUAGES = {
    "Java": ["java", "jvm", "spring", "maven", "jdbc"],
    "Python": ["python", "pandas", "numpy", "scikit-learn", "pytorch", "tensorflow"],
    "Scala": ["scala", "akka", "play framework"],
    "SQL": ["sql", "hql", "spark sql", "presto sql"],
    "Go": ["golang", "go"],
    "Shell": ["shell", "bash", "linux"],
    "R": ["r语言", "r studio"],
}

# --- 云计算平台 ---
CLOUD_PLATFORMS = {
    "阿里云": ["阿里云", "aliyun", "maxcompute", "odps", "dataworks", "blink", "holo", "hologres"],
    "腾讯云": ["腾讯云", "tencent cloud"],
    "华为云": ["华为云", "huawei cloud", "fusioninsight", "mrs"],
    "AWS": ["aws", "amazon", "emr", "s3", "redshift", "glue", "athena"],
    "Azure": ["azure", "databricks", "synapse", "adf"],
    "GCP": ["gcp", "google cloud", "bigquery", "dataflow", "dataproc"],
}

# --- 行业领域标签 ---
INDUSTRY_TAGS = {
    "金融": ["金融", "银行", "保险", "证券", "基金", "风控", "信贷", "支付", "消费金融"],
    "互联网": ["互联网", "电商", "社交", "短视频", "直播", "o2o", "平台"],
    "游戏": ["游戏", "手游", "电竞", "游戏开发"],
    "教育": ["教育", "在线教育", "培训", "k12"],
    "医疗": ["医疗", "医药", "健康", "医院", "临床"],
    "汽车": ["汽车", "自动驾驶", "车联网", "新能源"],
    "通信": ["通信", "运营商", "5g", "电信"],
    "零售": ["零售", "新零售", "连锁", "快消"],
    "物流": ["物流", "快递", "供应链", "仓储"],
    "制造": ["制造", "工业", "智能制造", "iot", "物联网"],
}


def label_categories(title: str, desc: str) -> dict:
    """
    根据职位名称和描述，标注大类标签
    返回: { "main_category": "数据开发", "sub_category": "实时数据开发", ... }
    """
    text = f"{title} {desc}".lower()
    title_lower = title.lower()

    # 先检查是否是非数据类岗位（销售/BD/管理等）
    for kw in NON_DATA_KEYWORDS:
        if kw in title_lower or kw in text:
            # 但如果标题中同时包含技术关键词，则不判定为非数据
            tech_indicators = ["开发", "工程师", "算法", "数据", "研发", "技术",
                             "python", "java", "sql", "spark", "flink",
                             "测试", "运维", "架构"]
            if not any(ti in title_lower for ti in tech_indicators):
                return {
                    "main_category": None,
                    "main_category_score": -1,
                    "all_categories": [],
                }

    # 英文标题匹配（处理纯英文或中英混合标题）
    for eng_title, cat in ENGLISH_TITLE_MAP.items():
        if eng_title in title_lower:
            return {
                "main_category": cat,
                "main_category_score": 0,
                "all_categories": [{"name": cat, "score": 0}],
            }

    # 匹配大类
    scores = {}
    for cat_name, cat_info in MAIN_CATEGORIES.items():
        score = 0
        for kw in cat_info["keywords"]:
            if kw.lower() in text:
                score += 1
                # 标题中出现权重更高
                if kw.lower() in title_lower:
                    score += 3  # 提升标题权重
        if score > 0:
            scores[cat_name] = score

    result = {
        "main_category": None,
        "main_category_score": 0,
        "all_categories": [],
    }

    if scores:
        sorted_cats = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        result["main_category"] = sorted_cats[0][0]
        result["main_category_score"] = sorted_cats[0][1]
        result["all_categories"] = [{"name": name, "score": s} for name, s in sorted_cats[:3]]
    else:
        # 关键词匹配失败时，基于标题推断
        title_categories = {
            "数据开发": ["开发", "ETL", "仓库", "平台开发", "数据湖", "数据集成",
                       "OLAP", "存储", "向量", "hadoop", "spark", "hive"],
            "数据分析": ["分析", "商业", "BI", "运营", "策略", "增长", "经营"],
            "AI/机器学习": ["算法", "机器学习", "深度学习", "AI", "大模型",
                         "LLM", "NLP", "CV", "Agent", "RAG", "推理",
                         "自然语言", "计算机视觉", "语音", "推荐", "搜索"],
            "数据运维": ["运维", "DBA", "数据库管理", "SRE", "devops"],
            "数据架构": ["架构", "治理", "中台", "技术总监"],
            "数据产品": ["产品", "经理"],
            "流计算": ["实时", "Flink", "流"],
            "测试/质量": ["测试", "质量", "QA"],
        }
        for cat, kws in title_categories.items():
            if any(kw.lower() in title_lower for kw in kws):
                result["main_category"] = cat
                result["main_category_score"] = 0
                result["all_categories"] = [{"name": cat, "score": 0}]
                break

    return result


def label_tech_stack(title: str, desc: str) -> dict:
    """标注技术栈标签"""
    text = f"{title} {desc}".lower()
    tech_labels = []
    for tech_name, tech_info in TECH_STACK.items():
        for kw in tech_info["keywords"]:
            if kw.lower() in text:
                tech_labels.append({
                    "tech": tech_name,
                    "category": tech_info["category"]
                })
                break
    return {"tech_stack": [t["tech"] for t in tech_labels], "tech_detail": tech_labels}


def label_languages(title: str, desc: str) -> dict:
    """标注编程语言"""
    text = f"{title} {desc}".lower()
    langs = []
    for lang, keywords in PROGRAMMING_LANGUAGES.items():
        for kw in keywords:
            if kw.lower() in text:
                langs.append(lang)
                break
    return {"languages": langs}


def label_cloud(title: str, desc: str) -> dict:
    """标注云平台"""
    text = f"{title} {desc}".lower()
    clouds = []
    for platform, keywords in CLOUD_PLATFORMS.items():
        for kw in keywords:
            if kw.lower() in text:
                clouds.append(platform)
                break
    return {"cloud_platforms": clouds}


def label_industry(title: str, desc: str, company: str = "", raw_industry: str = "") -> list[str]:
    """标注行业领域"""
    text = f"{title} {desc} {company} {raw_industry}".lower()
    industries = []
    for ind, keywords in INDUSTRY_TAGS.items():
        for kw in keywords:
            if kw.lower() in text:
                industries.append(ind)
                break
    return industries


def label_experience_level(title: str, exp_std: str) -> dict:
    """标注经验等级"""
    result = {"level": None, "level_description": ""}

    junior_kw = ["初级", "应届", "实习", "助理", "junior"]
    mid_kw = ["中级", "1-3年", "2-5年", "3-5年"]
    senior_kw = ["高级", "资深", "专家", "5年以上", "senior", "架构师"]
    lead_kw = ["负责人", "leader", "经理", "主管", "总监", "head", "lead"]

    title_lower = title.lower()

    for kw in lead_kw:
        if kw.lower() in title_lower:
            result["level"] = "管理/负责人"
            result["level_description"] = "团队管理、技术负责人级别"
            return result

    for kw in senior_kw:
        if kw.lower() in title_lower or (exp_std and any(e in str(exp_std) for e in ["5", "8", "10"])):
            result["level"] = "高级"
            result["level_description"] = "资深技术专家，5年以上经验"
            return result

    for kw in mid_kw:
        if kw.lower() in title_lower or (exp_std and any(e in str(exp_std) for e in ["3", "4"])):
            result["level"] = "中级"
            result["level_description"] = "独立工作，2-5年经验"
            return result

    for kw in junior_kw:
        if kw.lower() in title_lower or (exp_std and any(e in str(exp_std) for e in ["1", "应届"])):
            result["level"] = "初级"
            result["level_description"] = "初级/应届，0-2年经验"
            return result

    result["level"] = "未标注"
    result["level_description"] = "需根据具体JD判断"
    return result


def label_job(job: dict) -> dict:
    """
    对单条岗位数据进行全面标注
    返回增强后的字典
    """
    title = job.get("title", "")
    desc = job.get("job_desc", "")
    company = job.get("company", "")
    raw_industry = job.get("industry", "")
    exp_std = job.get("experience_std", "")

    # 大类标注
    cat_result = label_categories(title, desc)
    job["main_category"] = cat_result["main_category"]
    job["category_score"] = cat_result["main_category_score"]
    job["all_categories"] = cat_result["all_categories"]

    # 技术栈
    tech_result = label_tech_stack(title, desc)
    job["tech_stack"] = tech_result["tech_stack"]
    job["tech_stack_detail"] = tech_result["tech_detail"]

    # 编程语言
    lang_result = label_languages(title, desc)
    job["languages"] = lang_result["languages"]

    # 云平台
    cloud_result = label_cloud(title, desc)
    job["cloud_platforms"] = cloud_result["cloud_platforms"]

    # 行业
    job["industry_tags"] = label_industry(title, desc, company, raw_industry)

    # 经验等级
    level_result = label_experience_level(title, exp_std)
    job["job_level"] = level_result["level"]
    job["job_level_desc"] = level_result["level_description"]

    # 综合标签（用于快速检索）
    job["combined_tags"] = list(set(
        [job.get("main_category", "")] +
        job.get("tech_stack", []) +
        job.get("languages", []) +
        job.get("cloud_platforms", []) +
        job.get("industry_tags", []) +
        [job.get("job_level", "")]
    ))
    # 过滤空值
    job["combined_tags"] = [t for t in job["combined_tags"] if t]

    return job


def label_all(jobs: list[dict]) -> list[dict]:
    """批量标注所有岗位"""
    print(f"\n正在标注 {len(jobs)} 条岗位信息...")
    labeled = []
    for i, job in enumerate(jobs):
        if (i + 1) % 50 == 0:
            print(f"  已标注 {i+1}/{len(jobs)} 条...")
        labeled.append(label_job(job))
    print(f"标注完成，共标注 {len(labeled)} 条")
    return labeled


def generate_statistics(jobs: list[dict]) -> dict:
    """生成标签统计信息"""
    print("\n" + "=" * 60)
    print("标签统计与数据分析")
    print("=" * 60)

    stats = {
        "total_jobs": len(jobs),
        "category_distribution": dict(Counter(
            j.get("main_category", "未分类") for j in jobs
        )),
        "level_distribution": dict(Counter(
            j.get("job_level", "未标注") for j in jobs
        )),
        "tech_stack_top20": Counter(),
        "language_distribution": Counter(),
        "cloud_platform_distribution": Counter(),
        "industry_distribution": Counter(),
        "salary_stats": {},
    }

    all_salary_min = []
    all_salary_max = []
    for job in jobs:
        for tech in job.get("tech_stack", []):
            stats["tech_stack_top20"][tech] += 1
        for lang in job.get("languages", []):
            stats["language_distribution"][lang] += 1
        for cloud in job.get("cloud_platforms", []):
            stats["cloud_platform_distribution"][cloud] += 1
        for ind in job.get("industry_tags", []):
            stats["industry_distribution"][ind] += 1
        smin = job.get("salary_min_k")
        smax = job.get("salary_max_k")
        if smin and smax and smin > 0:
            all_salary_min.append(smin)
            all_salary_max.append(smax)

    stats["tech_stack_top20"] = stats["tech_stack_top20"].most_common(20)
    stats["language_distribution"] = stats["language_distribution"].most_common(10)
    stats["cloud_platform_distribution"] = stats["cloud_platform_distribution"].most_common(10)
    stats["industry_distribution"] = stats["industry_distribution"].most_common(10)

    if all_salary_min:
        stats["salary_stats"] = {
            "avg_min_k": round(sum(all_salary_min) / len(all_salary_min), 1),
            "avg_max_k": round(sum(all_salary_max) / len(all_salary_max), 1),
            "median_min_k": round(sorted(all_salary_min)[len(all_salary_min) // 2], 1),
            "median_max_k": round(sorted(all_salary_max)[len(all_salary_max) // 2], 1),
        }

    # 打印统计
    print(f"\n--- 岗位总数：{stats['total_jobs']} ---")

    print("\n【大类分布】")
    cat_dist = {k: v for k, v in stats["category_distribution"].items() if k}
    max_cat = max(cat_dist.values()) if cat_dist else 1
    for cat, cnt in sorted(cat_dist.items(), key=lambda x: x[1], reverse=True):
        bar = "█" * max(1, cnt // max(1, max_cat // 30))
        print(f"  {cat:<12} {cnt:>4}  {bar}")

    print("\n【经验等级分布】")
    lv_dist = {k: v for k, v in stats["level_distribution"].items() if k}
    for lv, cnt in sorted(lv_dist.items(), key=lambda x: x[1], reverse=True):
        print(f"  {lv:<12} {cnt:>4}")

    print("\n【热门技术栈 Top 10】")
    for tech, cnt in stats["tech_stack_top20"][:10]:
        print(f"  {tech:<20} {cnt:>4}")

    print("\n【编程语言需求】")
    for lang, cnt in stats["language_distribution"]:
        print(f"  {lang:<10} {cnt:>4}")

    print("\n【云平台分布】")
    for cloud, cnt in stats["cloud_platform_distribution"]:
        print(f"  {cloud:<10} {cnt:>4}")

    if stats["salary_stats"]:
        print(f"\n【薪资统计】(万/月)")
        ss = stats["salary_stats"]
        print(f"  平均最低薪资: {ss['avg_min_k']}万/月")
        print(f"  平均最高薪资: {ss['avg_max_k']}万/月")
        print(f"  中位数最低薪资: {ss['median_min_k']}万/月")
        print(f"  中位数最高薪资: {ss['median_max_k']}万/月")

    return stats


def save_labeled(jobs: list[dict], stats: dict, filepath: str = "data/labeled_jobs.json"):
    """保存标注后的数据"""
    import os
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    # 准备输出数据（移除冗余字段）
    output = {
        "metadata": {
            "total": len(jobs),
            "processed_date": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        },
        "statistics": {
            k: v for k, v in stats.items()
            if k not in ("tech_stack_top20", "language_distribution",
                         "cloud_platform_distribution", "industry_distribution")
        },
        "category_distribution": dict(stats["category_distribution"]),
        "level_distribution": dict(stats["level_distribution"]),
        "tech_stack_top20": [{"tech": t, "count": c} for t, c in stats["tech_stack_top20"]],
        "language_distribution": [{"language": l, "count": c} for l, c in stats["language_distribution"]],
        "cloud_distribution": [{"platform": p, "count": c} for p, c in stats["cloud_platform_distribution"]],
        "industry_distribution": [{"industry": i, "count": c} for i, c in stats["industry_distribution"]],
        "jobs": [],
    }

    for job in jobs:
        # 每个岗位只保存关键字段
        j = {
            "title": job.get("title", ""),
            "company": job.get("company", ""),
            "location": job.get("location", ""),
            "salary": job.get("salary", ""),
            "salary_min_k": job.get("salary_min_k"),
            "salary_max_k": job.get("salary_max_k"),
            "experience_std": job.get("experience_std"),
            "education_std": job.get("education_std"),
            "publish_date": job.get("publish_date"),
            "main_category": job.get("main_category"),
            "all_categories": job.get("all_categories"),
            "job_level": job.get("job_level"),
            "tech_stack": job.get("tech_stack"),
            "languages": job.get("languages"),
            "cloud_platforms": job.get("cloud_platforms"),
            "industry_tags": job.get("industry_tags"),
            "combined_tags": job.get("combined_tags"),
            "keywords": job.get("keyword_list", job.get("keywords", [])),
            "job_desc_preview": (job.get("job_desc_clean", job.get("job_desc", "")))[:300],
        }
        output["jobs"].append(j)

    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n标注数据已保存至 {filepath}")
    return filepath


def export_excel(jobs: list[dict], filepath: str = "data/bigdata_jobs_labeled.xlsx"):
    """导出为 Excel 文件"""
    import os
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)

    try:
        import pandas as pd
    except ImportError:
        print("请安装 pandas / openpyxl: pip install pandas openpyxl")
        return None

    rows = []
    for job in jobs:
        rows.append({
            "职位名称": job.get("title", ""),
            "公司": job.get("company", ""),
            "工作地点": job.get("location", ""),
            "薪资": job.get("salary", ""),
            "最低薪资(万/月)": job.get("salary_min_k"),
            "最高薪资(万/月)": job.get("salary_max_k"),
            "经验要求": job.get("experience_std", ""),
            "学历要求": job.get("education_std", ""),
            "岗位大类": job.get("main_category", ""),
            "经验等级": job.get("job_level", ""),
            "技术栈": "、".join(job.get("tech_stack", [])),
            "编程语言": "、".join(job.get("languages", [])),
            "云平台": "、".join(job.get("cloud_platforms", [])),
            "行业标签": "、".join(job.get("industry_tags", [])),
            "综合标签": "、".join(job.get("combined_tags", [])),
            "职位描述(前200字)": (job.get("job_desc_clean", job.get("job_desc", "")))[:200],
        })

    df = pd.DataFrame(rows)
    df.to_excel(filepath, index=False, engine="openpyxl")
    print(f"Excel 文件已保存至 {filepath}")
    return filepath
