"""
文本处理模块
负责：文本清洗、分词、关键词提取、文本标准化
"""
import re
import json
from typing import Union

# 延迟加载 jieba，避免首次加载耗时
_jieba_loaded = False


def _ensure_jieba():
    global _jieba_loaded
    if not _jieba_loaded:
        import jieba
        jieba.setLogLevel(20)  # 抑制 jieba 日志
        _jieba_loaded = True


def clean_text(text: str) -> str:
    """清洗文本：去除多余空白、特殊字符、HTML标签"""
    if not text:
        return ""
    # 去除 HTML 标签
    text = re.sub(r"<[^>]+>", " ", text)
    # 去除 URL
    text = re.sub(r"https?://\S+", "", text)
    # 去除特殊 Unicode 空白
    text = re.sub(r"[ -‏ -  　]", " ", text)
    # 压缩多个空白字符
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_salary(salary_str: str) -> dict:
    """
    标准化薪资信息
    输入如 "1.5-2.5万/月", "15-25K·14薪", "20-40万/年"
    输出标准化字典
    """
    if not salary_str:
        return {"salary_min_k": None, "salary_max_k": None, "salary_unit": None, "salary_months": 12}

    salary_str = salary_str.strip()
    result = {"salary_min_k": None, "salary_max_k": None, "salary_unit": "月", "salary_months": 12}

    # 处理 14薪/15薪 等
    months_match = re.search(r"(\d+)\s*薪", salary_str)
    if months_match:
        result["salary_months"] = int(months_match.group(1))

    # 识别单位：万/月, K/月, 元/月, 万/年
    if "年" in salary_str:
        result["salary_unit"] = "年"
        multiplier = 1.0  # 万/年
    elif "万/月" in salary_str or "万/月" in salary_str:
        multiplier = 1.0
    elif "千/月" in salary_str:
        multiplier = 0.1  # 千转万
    elif "K" in salary_str.upper() or "k" in salary_str:
        multiplier = 0.1  # K = 千 → 万
    elif "元/月" in salary_str or "元/天" in salary_str:
        multiplier = 0.0001  # 元转万
    else:
        multiplier = 0.1 if any(c in salary_str.upper() for c in ["K"]) else 1.0

    # 提取数字范围
    numbers = re.findall(r"(\d+\.?\d*)", salary_str)
    numbers = [float(n) for n in numbers if float(n) < 1000]  # 排除年份等大数

    if len(numbers) >= 2:
        result["salary_min_k"] = round(numbers[0] * multiplier, 1)
        result["salary_max_k"] = round(numbers[1] * multiplier, 1)
    elif len(numbers) == 1:
        result["salary_min_k"] = round(numbers[0] * multiplier, 1)
        result["salary_max_k"] = result["salary_min_k"]
    else:
        # 尝试匹配"面议"/"薪资open"等
        if "面议" in salary_str:
            result["salary_min_k"] = 0
            result["salary_max_k"] = 0

    return result


def normalize_experience(exp_str: str) -> Union[str, None]:
    """标准化经验要求"""
    if not exp_str:
        return None
    exp_str = exp_str.strip()
    patterns = [
        (r"(应届|无经验|在校|实习)", "应届/无经验"),
        (r"(\d+)[\s-]*(\d+)\s*年", lambda m: f"{m.group(1)}-{m.group(2)}年"),
        (r"(\d+)\s*年(以上|以下)?", lambda m: f"{m.group(1)}年以上" if m.group(2) else f"{m.group(1)}年"),
        (r"(不限)", "经验不限"),
    ]
    for pattern, target in patterns:
        match = re.search(pattern, exp_str)
        if match:
            if callable(target):
                return target(match)
            return target
    return exp_str


def normalize_education(edu_str: str) -> Union[str, None]:
    """标准化学历要求"""
    if not edu_str:
        return None
    edu_str = edu_str.strip()
    edu_map = {
        "博士": ["博士", "博士研究生"],
        "硕士": ["硕士", "硕士研究生", "研究生"],
        "本科": ["本科", "大学本科", "学士"],
        "大专": ["大专", "专科"],
        "高中": ["高中", "中专", "中技"],
        "不限": ["不限", "学历不限"],
    }
    for std, keywords in edu_map.items():
        for kw in keywords:
            if kw in edu_str:
                return std
    return edu_str


def tokenize(text: str) -> list[str]:
    """中文分词（去停用词）"""
    _ensure_jieba()
    import jieba

    STOP_WORDS = set([
        "的", "了", "在", "是", "我", "有", "和", "就", "不", "人", "都", "一",
        "一个", "上", "也", "很", "到", "说", "要", "去", "你", "会", "着",
        "没有", "看", "好", "自己", "这", "他", "她", "它", "们", "那", "些",
        "所", "为", "所以", "因为", "但是", "然而", "而且", "或", "或者",
        "并", "并且", "当", "于", "及", "与", "从", "对", "以", "之", "将",
        "能", "可以", "需要", "如果", "虽然", "吧", "啊", "呢", "吗", "哦",
        "进行", "负责", "参与", "相关", "岗位", "任职", "要求", "职责",
        "以上", "以下", "具有", "具备", "优先", "熟悉", "了解", "掌握",
    ])

    words = jieba.lcut(text)
    return [w.strip() for w in words if w.strip() and w.strip() not in STOP_WORDS and len(w.strip()) > 1]


def extract_keywords(text: str, topk: int = 20) -> list[tuple[str, float]]:
    """基于 TF-IDF 提取关键词"""
    _ensure_jieba()
    import jieba.analyse
    return jieba.analyse.extract_tags(text, topK=topk, withWeight=True)


def process_job_text(job: dict) -> dict:
    """
    处理单条岗位数据的文本字段
    返回增强后的岗位字典
    """
    # 清洗文本字段
    job["title_clean"] = clean_text(job.get("title", ""))
    job["job_desc_clean"] = clean_text(job.get("job_desc", ""))
    job["company_clean"] = clean_text(job.get("company", ""))

    # 薪资标准化
    salary_info = normalize_salary(job.get("salary", ""))
    job["salary_min_k"] = salary_info["salary_min_k"]
    job["salary_max_k"] = salary_info["salary_max_k"]
    job["salary_unit"] = salary_info["salary_unit"]
    job["salary_months"] = salary_info["salary_months"]

    # 经验/学历标准化
    job["experience_std"] = normalize_experience(job.get("experience", ""))
    job["education_std"] = normalize_education(job.get("education", ""))

    # 对职位描述进行分词
    job["tokens"] = tokenize(job["job_desc_clean"])

    # 提取关键词
    job["keywords"] = [(kw, round(w, 4)) for kw, w in extract_keywords(job["job_desc_clean"], topk=15)]

    # 全文（拼接标题+描述）
    full_text = f"{job.get('title','')} {job.get('job_desc','')}"
    job["full_tokens"] = tokenize(clean_text(full_text))

    return job


def process_all(jobs: list[dict]) -> list[dict]:
    """批量处理所有岗位数据"""
    print(f"\n正在处理 {len(jobs)} 条岗位文本...")
    processed = []
    for i, job in enumerate(jobs):
        if (i + 1) % 50 == 0:
            print(f"  已处理 {i+1}/{len(jobs)} 条...")
        processed.append(process_job_text(job))
    print(f"文本处理完成，共处理 {len(processed)} 条")
    return processed


def save_processed(jobs: list[dict], filepath: str = "data/processed_jobs.json"):
    """保存处理后的数据"""
    import os
    os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
    # 移除 tokens 字段以减少文件大小
    savable = []
    for job in jobs:
        j = {k: v for k, v in job.items() if k not in ("tokens", "full_tokens")}
        j["token_count"] = len(job.get("tokens", []))
        j["keyword_list"] = [kw[0] for kw in job.get("keywords", [])]
        savable.append(j)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(savable, f, ensure_ascii=False, indent=2)
    print(f"处理后数据已保存至 {filepath}")
    return filepath
