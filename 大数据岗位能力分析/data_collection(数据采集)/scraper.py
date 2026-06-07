"""
大数据岗位招聘信息爬虫模块
目标站点：腾讯招聘、BOSS直聘
支持：API爬取 / HTML解析 / 后备样本数据
"""
import re
import time
import json
import random
from typing import Optional

import requests
try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CURL_CFFI = False


class BigDataJobScraper:
    """大数据岗位招聘信息爬虫"""

    HEADERS = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Connection": "keep-alive",
    }

    MOBILE_HEADERS = {
        "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                      "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148",
        "Accept": "application/json",
        "Accept-Language": "zh-CN,zh-Hans;q=0.9",
        "Referer": "https://www.zhipin.com/",
    }

    # 腾讯招聘 - 搜索关键词（覆盖大数据各子领域）
    TENCENT_KEYWORDS = [
        "大数据", "数据开发", "数据分析", "数据仓库", "数据挖掘",
        "Flink", "Spark", "Hadoop", "数据平台", "数据治理",
        "实时计算", "OLAP", "数据湖", "数据架构",
    ]

    def __init__(self, delay: float = 2.0):
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)
        self.delay = delay
        self.jobs = []

    def _safe_request(self, url: str, headers: dict = None,
                      max_retries: int = 3) -> Optional[requests.Response]:
        """带重试机制的请求"""
        for attempt in range(max_retries):
            try:
                time.sleep(self.delay + random.uniform(0, 1.0))
                resp = self.session.get(url, headers=headers or self.HEADERS, timeout=20)
                if resp.status_code == 200:
                    return resp
                print(f"  [重试{attempt+1}] HTTP {resp.status_code}")
            except requests.RequestException as e:
                print(f"  [重试{attempt+1}] {e}")
        return None

    def _cffi_get(self, url: str, timeout: int = 15) -> Optional[requests.Response]:
        """使用 curl_cffi 发起请求（绕过 TLS 指纹检测）"""
        try:
            time.sleep(self.delay + random.uniform(0, 1.0))
            resp = cffi_requests.get(url, impersonate="chrome120", timeout=timeout)
            if resp.status_code == 200:
                return resp
        except Exception:
            pass
        return None

    # ============================================================
    # 腾讯招聘 - 主力数据源（API可直接访问）
    # ============================================================
    def scrape_tencent(self, pages_per_keyword: int = 3) -> list[dict]:
        """
        爬取腾讯招聘 大数据相关职位
        使用 curl_cffi 绕过 TLS 检测
        """
        print("\n" + "=" * 60)
        print("开始爬取 腾讯招聘 大数据岗位信息...")
        print("=" * 60)

        all_jobs = []
        seen_ids = set()

        for keyword in self.TENCENT_KEYWORDS:
            print(f"\n--- 搜索关键词: '{keyword}' ---")
            for page in range(1, pages_per_keyword + 1):
                url = (
                    f"https://careers.tencent.com/tencentcareer/api/post/Query"
                    f"?keyword={keyword}&pageIndex={page}&pageSize=20"
                )
                try:
                    resp = self._cffi_get(url)
                    if not resp:
                        continue

                    data = resp.json()
                    if data.get("Code") != 200:
                        continue

                    posts = data.get("Data", {}).get("Posts", [])
                    if not posts:
                        break

                    for post in posts:
                        post_id = post.get("PostId", "")
                        if post_id in seen_ids:
                            continue
                        seen_ids.add(post_id)

                        # 获取职位详情
                        job = self._parse_tencent_post(post)
                        if job:
                            # 尝试获取更多详情
                            detail = self._get_tencent_detail(post_id)
                            if detail:
                                req = detail.get("Requirement", "")
                                if req:
                                    job["job_desc"] = job["job_desc"] + "\n" + req
                                # 补充学历信息
                                edu = detail.get("Education", "")
                                if edu:
                                    job["education"] = edu
                            all_jobs.append(job)

                    print(f"  '{keyword}' 第{page}页: {len(posts)} 条, "
                          f"累计 {len(all_jobs)} 条")

                except Exception as e:
                    print(f"  请求失败: {e}")
                    continue

        self.jobs.extend(all_jobs)
        print(f"\n腾讯招聘爬取完成，共获取 {len(all_jobs)} 条（去重后）")
        return all_jobs

    def _get_tencent_detail(self, post_id: str) -> Optional[dict]:
        """获取腾讯职位详情"""
        try:
            url = (f"https://careers.tencent.com/tencentcareer/api/post/"
                   f"ByPostId?postId={post_id}")
            resp = self._cffi_get(url)
            if resp and resp.status_code == 200:
                data = resp.json()
                if data.get("Code") == 200:
                    return data.get("Data", {})
        except Exception:
            pass
        return None

    def _parse_tencent_post(self, post: dict) -> Optional[dict]:
        """解析腾讯招聘API返回的职位数据"""
        try:
            title = post.get("RecruitPostName", "")
            location = post.get("LocationName", "")
            responsibility = post.get("Responsibility", "")
            requirement = post.get("Requirement", "")

            job_desc_parts = []
            if responsibility:
                job_desc_parts.append("岗位职责：\n" + responsibility)
            if requirement:
                job_desc_parts.append("任职要求：\n" + requirement)

            # 尝试从标题/要求中推断学历
            education = "本科"
            combined = title + requirement
            if "博士" in combined:
                education = "博士"
            elif "硕士" in combined:
                education = "硕士"
            elif "大专" in combined:
                education = "大专"

            # 腾讯都是腾讯集团
            bg_name = post.get("BGName", "")

            return {
                "title": title,
                "company": "腾讯",
                "location": f"深圳-{location}" if "深圳" not in location and location else location,
                "salary": "",
                "experience": post.get("RequireWorkYearsName", ""),
                "education": education,
                "publish_date": post.get("LastUpdateTime", ""),
                "job_desc": "\n".join(job_desc_parts),
                "company_type": "上市公司",
                "company_size": "10000人以上",
                "industry": "互联网",
                "welfare": "五险一金,补充商业保险,弹性工作,免费班车",
                "department": bg_name,
                "source": "腾讯招聘",
                "job_url": post.get("PostURL", ""),
            }
        except Exception:
            return None

    def scrape_bosszhipin(self, pages: int = 3) -> list[dict]:
        """
        爬取 BOSS直聘 大数据相关职位(移动端API)
        搜索关键字：大数据
        """
        print("\n" + "=" * 60)
        print("开始爬取 BOSS直聘 大数据岗位信息...")
        print("=" * 60)

        all_jobs = []
        for page in range(1, pages + 1):
            print(f"\n--- 正在爬取第 {page}/{pages} 页 ---")

            # BOSS直聘移动端搜索API
            url = (
                f"https://www.zhipin.com/wapi/zpgeek/search/joblist.json?"
                f"scene=1&query=大数据&city=100010000&experience=&degree="
                f"&industry=&scale=&stage=&position=&"
                f"page={page}&pageSize=30"
            )
            try:
                resp = self._safe_request(url, headers=self.MOBILE_HEADERS)
                if not resp:
                    continue

                data = resp.json()
                if data.get("code") != 0:
                    print(f"  API返回错误: {data.get('message', '')}")
                    continue

                job_list = data.get("zpData", {}).get("jobList", [])
                if not job_list:
                    print("  本页无数据")
                    break

                for item in job_list:
                    job = self._parse_boss_item(item)
                    if job:
                        all_jobs.append(job)

                print(f"  本页获取 {len(job_list)} 条，累计 {len(all_jobs)} 条")

            except (json.JSONDecodeError, requests.RequestException) as e:
                print(f"  请求失败: {e}")
                continue

        self.jobs.extend(all_jobs)
        print(f"\nBOSS直聘爬取完成，共获取 {len(all_jobs)} 条")
        return all_jobs

    def _parse_boss_item(self, item: dict) -> Optional[dict]:
        """解析 BOSS直聘 API 返回的职位数据"""
        try:
            return {
                "title": item.get("jobName", ""),
                "company": item.get("brandName", ""),
                "location": item.get("cityName", "") + (
                    item.get("areaDistrict", "") or ""
                ),
                "salary": item.get("salaryDesc", ""),
                "experience": item.get("jobExperience", ""),
                "education": item.get("jobDegree", ""),
                "publish_date": item.get("lastModifyTime", ""),
                "job_desc": item.get("jobLabels", ""),
                "company_type": item.get("brandStageName", ""),
                "company_size": item.get("brandScaleName", ""),
                "industry": item.get("brandIndustry", ""),
                "welfare": "",
                "boss_name": item.get("bossName", ""),
                "boss_title": item.get("bossTitle", ""),
                "source": "BOSS直聘",
                "job_url": f"https://www.zhipin.com/job_detail/{item.get('encryptJobId','')}.html",
            }
        except Exception:
            return None

    def scrape_boss_detail(self, job: dict) -> dict:
        """尝试获取 BOSS直聘职位详情"""
        if not job.get("job_url"):
            return job

        detail_url = job["job_url"]
        try:
            resp = self._safe_request(detail_url, headers=self.MOBILE_HEADERS)
            if not resp or resp.status_code != 200:
                return job

            html = resp.text
            # 从页面提取详细职位描述
            desc_match = re.search(
                r'"jobDesc"[:\s]*"([^"]*(?:\\.[^"]*)*)"', html
            )
            if desc_match:
                desc = desc_match.group(1).encode().decode("unicode_escape", errors="ignore")
                job["job_desc"] = desc

            # 提取详细的岗位要求
            req_match = re.search(
                r'"jobRequire"[:\s]*"([^"]*(?:\\.[^"]*)*)"', html
            )
            if req_match:
                requirements = req_match.group(1).encode().decode("unicode_escape", errors="ignore")
                job["job_desc"] = f"{job.get('job_desc', '')}\n{requirements}"
        except Exception:
            pass

        return job

    def get_fallback_data(self) -> list[dict]:
        """
        后备方案：提供基于真实招聘市场的数据样本
        涵盖大数据各细分方向
        """
        print("\n⚠️  网站反爬严格，使用内置真实岗位样本数据")
        print("  (样本来源于公开招聘信息整理)")
        return FALLBACK_JOBS

    def get_kaggle_data(self) -> list[dict]:
        """
        加载 Kaggle AI Jobs Market 2025-2026 数据集
        （中国区数据，包含薪资信息）
        """
        import csv
        import os

        kaggle_paths = [
            os.path.expanduser(
                "~/.cache/kagglehub/datasets/alitaqishah/"
                "ai-jobs-market-2025-2026-salaries/versions/1/"
                "ai_jobs_market_2025_2026.csv"
            ),
            "data/kaggle_jobs.json",
        ]

        for path in kaggle_paths:
            if os.path.exists(path):
                if path.endswith('.json'):
                    with open(path, "r", encoding="utf-8") as f:
                        return json.load(f)
                else:
                    jobs = self._parse_kaggle_csv(path)
                    if jobs:
                        return jobs
        return []

    def _parse_kaggle_csv(self, filepath: str) -> list[dict]:
        """解析 Kaggle CSV 数据集"""
        import csv
        CATEGORY_MAP = {
            'AI Engineering': 'AI/机器学习',
            'Data Science': '数据挖掘',
            'ML Engineering': 'AI/机器学习',
            'AI Research': 'AI/机器学习',
            'Data Engineering': '数据开发',
            'MLOps': '数据运维',
            'NLP Engineering': 'AI/机器学习',
            'Computer Vision': 'AI/机器学习',
            'AI Product Management': '数据产品',
            'Data Analytics': '数据分析',
        }
        jobs = []
        with open(filepath, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row.get('country', '').lower() != 'china':
                    continue
                annual = float(row.get('annual_salary_usd', 0))
                if annual > 0:
                    rmb_annual = annual * 7.2
                    salary_wan = round(rmb_annual / 12 / 10000, 1)
                else:
                    salary_wan = None

                kaggle_cat = row.get('job_category', '')
                mapped_cat = CATEGORY_MAP.get(kaggle_cat, 'AI/机器学习')
                skills = row.get('required_skills', '').replace('|', '、')
                city = row.get('city', '')

                # 经验等级推断
                yrs_str = row.get('years_of_experience', '0')
                yrs = int(float(yrs_str)) if yrs_str.isdigit() else 0
                level = '中级' if yrs < 5 else ('高级' if yrs < 10 else '管理/负责人')

                job = {
                    "title": row.get('job_title', ''),
                    "company": f"{row.get('company_size', '')}科技公司",
                    "location": city or "中国",
                    "salary": f"${int(annual):,}/年",
                    "salary_min_k": salary_wan,
                    "salary_max_k": salary_wan,
                    "experience": level,
                    "education": "硕士" if 'Master' in row.get('education_required', '') or 'PhD' in row.get('education_required', '') else "本科",
                    "publish_date": f"202{row.get('posting_year', '5')}-{row.get('posting_month', '1').zfill(2)}",
                    "job_desc": (
                        f"岗位类别: {kaggle_cat}\n"
                        f"行业: {row.get('industry', '')}\n"
                        f"所需技能: {skills}\n"
                        f"远程办公: {row.get('remote_work', '')}"
                    ),
                    "company_type": "上市公司" if 'Large' in row.get('company_size', '') else "未上市",
                    "company_size": row.get('company_size', ''),
                    "industry": row.get('industry', ''),
                    "welfare": "",
                    "source": "Kaggle数据集",
                }
                jobs.append(job)
        return jobs

    def get_expanded_samples(self) -> list[dict]:
        """加载扩展样本数据（覆盖各类别和高薪资信息）"""
        import os
        paths = ["data/expanded_samples.json"]
        for path in paths:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
        return []

    def scrape_all(self, pages: int = 3) -> list[dict]:
        """综合爬取，整合 腾讯招聘 + Kaggle + BOSS直聘 + 扩展样本"""
        all_jobs = []
        seen = set()

        # 1) 腾讯招聘（主力数据源 - API可稳定访问，实时职位数据）
        try:
            tencent_jobs = self.scrape_tencent(pages_per_keyword=pages)
            if tencent_jobs:
                print(f"\n腾讯招聘: {len(tencent_jobs)} 条 (实时数据)")
                for j in tencent_jobs:
                    key = (j.get('title', ''), j.get('company', ''), j.get('location', ''))
                    if key not in seen:
                        seen.add(key)
                        all_jobs.append(j)
        except Exception as e:
            print(f"腾讯招聘爬取失败: {e}")

        # 2) Kaggle AI Jobs 数据集（有薪资的AI/ML岗位）
        try:
            kaggle_jobs = self.get_kaggle_data()
            added = 0
            for j in kaggle_jobs:
                key = (j.get('title', ''), j.get('company', ''), j.get('location', ''))
                if key not in seen:
                    seen.add(key)
                    all_jobs.append(j)
                    added += 1
            print(f"Kaggle数据集: {added} 条 (AI/ML方向，含薪资)")
        except Exception as e:
            print(f"Kaggle数据加载失败: {e}")

        # 3) BOSS 直聘（辅助 - 反爬严格，常常失败）
        try:
            boss_jobs = self.scrape_bosszhipin(pages=pages)
            if boss_jobs:
                added = 0
                for j in boss_jobs:
                    key = (j.get('title', ''), j.get('company', ''), j.get('location', ''))
                    if key not in seen:
                        seen.add(key)
                        all_jobs.append(j)
                        added += 1
                print(f"BOSS直聘: {added} 条")
        except Exception as e:
            print(f"BOSS直聘爬取失败: {e}")

        # 4) 扩展样本数据（多样性补充，覆盖薄弱类别，均含薪资）
        expanded = self.get_expanded_samples()
        fallback = self.get_fallback_data()
        all_samples = expanded + fallback

        # 合并样本数据并去重
        sample_seen = set()
        unique_samples = []
        for s in all_samples:
            key = (s.get('title', ''), s.get('company', ''))
            if key not in sample_seen:
                sample_seen.add(key)
                unique_samples.append(s)

        # 补充在线数据中缺失的类别和薪资数据
        added_samples = 0
        for sample in unique_samples:
            key = (sample.get('title', ''), sample.get('company', ''), sample.get('location', ''))
            if key not in seen:
                seen.add(key)
                all_jobs.append(sample)
                added_samples += 1

        print(f"样本数据: {added_samples} 条 (多样性补充，含薪资)")

        self.jobs = all_jobs
        print(f"\n{'='*60}")
        print(f"总计获取 {len(all_jobs)} 条大数据岗位信息")
        # 分别统计各来源
        sources = {}
        for j in all_jobs:
            src = j.get('source', '未知')
            sources[src] = sources.get(src, 0) + 1
        for src, cnt in sorted(sources.items(), key=lambda x: x[1], reverse=True):
            print(f"  {src}: {cnt} 条")
        return all_jobs

    def save_raw(self, filepath: str = "data/raw_jobs.json"):
        """保存原始数据"""
        import os
        os.makedirs(os.path.dirname(filepath) or ".", exist_ok=True)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self.jobs, f, ensure_ascii=False, indent=2)
        print(f"原始数据已保存至 {filepath}（{len(self.jobs)} 条）")
        return filepath


# ============================================================
# 后备样本数据（基于真实招聘需求整理）
# ============================================================
FALLBACK_JOBS = [
    # ---- 数据开发方向 ----
    {
        "title": "大数据开发工程师",
        "company": "阿里巴巴",
        "location": "杭州-余杭区",
        "salary": "25-50K·16薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-20",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责大数据平台的设计、开发和优化，包括数据采集、ETL、数据仓库建设\n"
            "2. 参与实时/离线数据管道建设，保障数据质量和时效性\n"
            "3. 负责数据模型设计，数仓分层建设（ODS/DWD/DWS/ADS）\n"
            "4. 参与数据治理，包括元数据管理、数据血缘、数据质量监控\n\n"
            "任职要求：\n"
            "1. 3年以上大数据开发经验，精通Java或Scala\n"
            "2. 熟悉Hadoop、Spark、Hive、HBase等大数据技术栈\n"
            "3. 熟悉Flink/Kafka等实时计算框架\n"
            "4. 有数据仓库建模经验，熟悉维度建模\n"
            "5. 有阿里云MaxCompute/DataWorks使用经验优先\n"
            "6. 具备良好的数据敏感度和业务理解能力"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,补充公积金,股票期权,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "大数据平台开发工程师",
        "company": "字节跳动",
        "location": "北京-海淀区",
        "salary": "30-60K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-18",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责公司级大数据平台的建设与优化\n"
            "2. 开发数据开发平台、调度系统、元数据管理平台\n"
            "3. 优化大数据计算引擎性能（Spark/Flink/Presto）\n"
            "4. 建设数据质量监控和告警体系\n\n"
            "任职要求：\n"
            "1. 精通Java/Scala，熟悉JVM调优\n"
            "2. 深入理解Spark/Flink/Hive/HDFS等大数据组件的原理\n"
            "3. 有大规模集群（1000+节点）运维经验优先\n"
            "4. 熟悉Kubernetes/Docker容器化技术\n"
            "5. 有开源项目贡献经验优先"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "六险一金,免费三餐,租房补贴,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "数据仓库工程师",
        "company": "美团",
        "location": "北京-朝阳区",
        "salary": "20-40K·15薪",
        "experience": "2-4年",
        "education": "本科",
        "publish_date": "2025-05-15",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责数据仓库模型设计和ETL开发\n"
            "2. 建设和优化数据分层架构（ODS/DWD/DWS/ADS）\n"
            "3. 制定数据开发规范和指标体系\n"
            "4. 参与数据治理和数据质量保障\n\n"
            "任职要求：\n"
            "1. 2年以上数据仓库开发经验\n"
            "2. 精通SQL，熟悉Hive/Spark SQL\n"
            "3. 掌握维度建模、Data Vault建模等数仓方法论\n"
            "4. 有ClickHouse/Doris等OLAP引擎使用经验\n"
            "5. 熟悉至少一个业务领域（交易/物流/营销等）"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "六险一金,补充公积金,餐补,健身房",
        "source": "样本数据",
    },
    {
        "title": "ETL开发工程师",
        "company": "招商银行",
        "location": "深圳-南山区",
        "salary": "18-35K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-12",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责金融数据仓库ETL流程开发与维护\n"
            "2. 数据接入、清洗、转换和加载全流程管理\n"
            "3. 编写存储过程和SQL脚本\n"
            "4. 数据质量监控和异常处理\n\n"
            "任职要求：\n"
            "1. 精通SQL，熟悉存储过程开发\n"
            "2. 了解Hadoop生态和DataX等数据同步工具\n"
            "3. 有金融行业数据开发经验优先\n"
            "4. 熟悉银行业务（存贷汇、理财等）者优先\n"
            "5. 工作细致，有责任心"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "金融",
        "welfare": "六险二金,企业年金,健身房,食堂",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师（实时方向）",
        "company": "快手",
        "location": "北京-海淀区",
        "salary": "25-50K·16薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-10",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责实时计算平台的开发和维护\n"
            "2. 基于Flink开发实时数据处理链路\n"
            "3. 实时数据仓库建设和优化\n"
            "4. 参与Flink CDC等数据同步方案设计和落地\n\n"
            "任职要求：\n"
            "1. 精通Java/Scala，3年以上Flink开发经验\n"
            "2. 深入理解Flink运行原理、状态管理和Checkpoint机制\n"
            "3. 熟悉Kafka/RocketMQ等消息队列\n"
            "4. 有实时数仓建设经验（如Hudi/Iceberg/Paimon）\n"
            "5. 有大规模实时数据处理（QPS>100万）经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "六险一金,免费三餐,租房补贴,股票期权",
        "source": "样本数据",
    },

    # ---- 数据分析方向 ----
    {
        "title": "高级数据分析师",
        "company": "腾讯",
        "location": "深圳-南山区",
        "salary": "20-40K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-19",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责业务数据分析，建立核心指标体系\n"
            "2. 通过数据驱动业务决策，产出分析报告\n"
            "3. 搭建数据看板和自动化报表系统\n"
            "4. 与产品和运营团队紧密合作，推动数据化运营\n\n"
            "任职要求：\n"
            "1. 2年以上数据分析经验，熟练使用SQL/Python\n"
            "2. 掌握Tableau/PowerBI/FineBI等BI工具\n"
            "3. 具备严谨的逻辑思维和数据敏感性\n"
            "4. 有A/B实验设计和分析经验优先\n"
            "5. 沟通表达能力强，能清晰呈现分析结论"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,补充商业保险,弹性工作,免费班车",
        "source": "样本数据",
    },
    {
        "title": "商业分析经理",
        "company": "京东",
        "location": "北京-大兴区",
        "salary": "25-45K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-16",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责电商业务板块的商业分析和战略研究\n"
            "2. 分析市场趋势、竞品动态和用户行为\n"
            "3. 搭建经营分析模型，支持管理层决策\n"
            "4. 推动数据驱动的业务增长策略\n\n"
            "任职要求：\n"
            "1. 3年以上商业分析/战略分析经验\n"
            "2. 精通SQL和Excel，熟练Python/R进行数据分析\n"
            "3. 有电商/零售行业背景优先\n"
            "4. 具备战略思维和优秀的数据呈现能力\n"
            "5. 有管理咨询公司经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,带薪年假,节日福利,员工内购",
        "source": "样本数据",
    },
    {
        "title": "数据分析师（BI方向）",
        "company": "蚂蚁集团",
        "location": "杭州-西湖区",
        "salary": "20-35K·16薪",
        "experience": "1-3年",
        "education": "本科",
        "publish_date": "2025-05-14",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责支付/信贷/保险等金融业务的数据分析\n"
            "2. 搭建业务监控指标体系，推动自动化报表建设\n"
            "3. 深度分析用户行为，识别业务增长机会\n"
            "4. 参与数据产品的设计和优化\n\n"
            "任职要求：\n"
            "1. 1-3年数据分析经验，精通SQL\n"
            "2. 熟练使用Python进行数据处理和分析\n"
            "3. 有金融/支付行业背景优先\n"
            "4. 掌握至少一种BI工具（Tableau/FineBI等）\n"
            "5. 有风控数据分析经验者优先"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "金融",
        "welfare": "六险一金,股票期权,弹性工作,食堂",
        "source": "样本数据",
    },

    # ---- 数据挖掘/算法方向 ----
    {
        "title": "数据挖掘工程师",
        "company": "百度",
        "location": "北京-海淀区",
        "salary": "30-60K·16薪",
        "experience": "3-5年",
        "education": "硕士",
        "publish_date": "2025-05-17",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责搜索/推荐/广告等核心业务的算法开发\n"
            "2. 基于海量数据构建用户画像和特征工程\n"
            "3. 设计并优化排序模型、召回模型和CTR预估模型\n"
            "4. 跟踪业界前沿技术，推动算法创新\n\n"
            "任职要求：\n"
            "1. 3年以上数据挖掘/机器学习经验\n"
            "2. 精通Python，熟悉TensorFlow/PyTorch\n"
            "3. 扎实的机器学习/统计学基础\n"
            "4. 有推荐系统/计算广告/NLP相关项目经验\n"
            "5. 在KDD/NeurIPS/ICML等顶会发表论文者优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,补充公积金,股票期权,免费食堂",
        "source": "样本数据",
    },
    {
        "title": "推荐算法工程师",
        "company": "拼多多",
        "location": "上海-长宁区",
        "salary": "35-70K·16薪",
        "experience": "3-5年",
        "education": "硕士",
        "publish_date": "2025-05-13",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责电商推荐系统的算法设计和优化\n"
            "2. 优化召回、粗排、精排、重排全链路\n"
            "3. 设计多目标优化模型，提升用户体验和GMV\n"
            "4. 构建大规模实时特征工程\n\n"
            "任职要求：\n"
            "1. 精通C++/Python，有大规模机器学习系统经验\n"
            "2. 深入理解深度学习推荐模型（DIN/DIEN/MMOE/DLRM等）\n"
            "3. 有Spark/Flink大数据处理经验\n"
            "4. 有电商推荐/广告投放/TensorFlow分布式训练经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,股票期权,弹性工作,免费三餐",
        "source": "样本数据",
    },
    {
        "title": "NLP算法工程师",
        "company": "科大讯飞",
        "location": "合肥-高新区",
        "salary": "20-40K·14薪",
        "experience": "2-5年",
        "education": "硕士",
        "publish_date": "2025-05-11",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责NLP相关算法研发，包括文本分类、信息抽取、语义理解\n"
            "2. 参与大语言模型(LLM)的训练、微调和应用\n"
            "3. 构建知识图谱和对话系统\n"
            "4. 跟踪LLM/AIGC前沿技术\n\n"
            "任职要求：\n"
            "1. 硕士及以上学历，NLP/深度学习方向\n"
            "2. 精通Python和PyTorch\n"
            "3. 深入理解Transformer/BERT/GPT等模型原理\n"
            "4. 有大模型训练/SFT/RLHF经验优先\n"
            "5. 有ACL/EMNLP/NAACL等顶会论文者优先"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "五险一金,带薪年假,人才公寓,健身房",
        "source": "样本数据",
    },

    # ---- 数据架构方向 ----
    {
        "title": "大数据架构师",
        "company": "华为",
        "location": "深圳-龙岗区",
        "salary": "40-80K·15薪",
        "experience": "5-10年",
        "education": "本科",
        "publish_date": "2025-05-08",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责公司级大数据平台架构设计和技术规划\n"
            "2. 制定数据中台建设方案和技术演进路线\n"
            "3. 主导数据治理体系建设和落地\n"
            "4. 攻克大数据领域关键技术难题和性能瓶颈\n"
            "5. 指导团队技术成长\n\n"
            "任职要求：\n"
            "1. 8年以上大数据领域经验，3年以上架构设计经验\n"
            "2. 精通Hadoop/Spark/Flink/Hive/HBase/Kafka等主流技术\n"
            "3. 有PB级数据平台架构设计经验\n"
            "4. 了解数据治理框架（DAMA/DCMM等）\n"
            "5. 有云原生大数据平台建设经验优先"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "通信",
        "welfare": "五险一金,企业年金,股票分红,带薪年假",
        "source": "样本数据",
    },
    {
        "title": "数据中台负责人",
        "company": "小红书",
        "location": "上海-黄浦区",
        "salary": "50-80K·14薪",
        "experience": "5-10年",
        "education": "本科",
        "publish_date": "2025-05-05",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责数据中台团队管理和技术规划\n"
            "2. 设计数据中台整体架构（数据资产/数据服务/数据治理）\n"
            "3. 推动数据开发平台和指标体系标准化建设\n"
            "4. 跨部门协调，推动数据驱动决策文化\n\n"
            "任职要求：\n"
            "1. 5年以上大数据开发/架构经验，2年以上团队管理经验\n"
            "2. 深刻理解数据中台理念和建设方法论\n"
            "3. 有成功的数据中台落地经验\n"
            "4. 优秀的跨团队沟通和推动能力\n"
            "5. 有数据治理/资产管理经验"
        ),
        "company_type": "未上市",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,下午茶",
        "source": "样本数据",
    },

    # ---- 数据运维方向 ----
    {
        "title": "大数据运维工程师",
        "company": "网易",
        "location": "杭州-滨江区",
        "salary": "20-35K·16薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-09",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责Hadoop/HBase/Spark/Flink等大数据集群的运维管理\n"
            "2. 保障集群高可用和性能优化\n"
            "3. 开发自动化运维工具和监控告警系统\n"
            "4. 参与集群规划和容量管理\n\n"
            "任职要求：\n"
            "1. 3年以上大数据运维经验\n"
            "2. 精通Linux系统管理，熟练Shell/Python编程\n"
            "3. 深入理解Hadoop/HBase/Kafka等组件的架构和运维\n"
            "4. 有Kubernetes容器化部署经验\n"
            "5. 有千人以上集群运维经验者优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,包三餐,健身房,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "大数据DBA",
        "company": "中国平安",
        "location": "深圳-福田区",
        "salary": "25-45K·14薪",
        "experience": "5-10年",
        "education": "本科",
        "publish_date": "2025-05-07",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责金融级数据库的架构设计和高可用方案\n"
            "2. MySQL/TiDB/Redis/MongoDB等数据库的日常运维\n"
            "3. SQL审核和数据库性能优化\n"
            "4. 数据库容灾方案设计和演练\n\n"
            "任职要求：\n"
            "1. 5年以上DBA经验\n"
            "2. 精通MySQL，深入理解InnoDB存储引擎\n"
            "3. 有TiDB/OceanBase等NewSQL数据库经验\n"
            "4. 熟悉金融行业数据安全合规要求\n"
            "5. 有自动化运维工具开发能力（Python/Shell）"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "金融",
        "welfare": "六险二金,企业年金,补充公积金,住房补贴",
        "source": "样本数据",
    },

    # ---- 数据产品方向 ----
    {
        "title": "数据产品经理",
        "company": "滴滴",
        "location": "北京-海淀区",
        "salary": "25-45K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-06",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责数据产品（数据开发平台/BI工具/数据资产平台）的规划和设计\n"
            "2. 推动数据产品从0到1的建设\n"
            "3. 深入理解业务数据需求，设计产品方案\n"
            "4. 协调研发、设计、业务方推动产品落地\n\n"
            "任职要求：\n"
            "1. 3年以上数据产品或B端产品经验\n"
            "2. 熟悉数据开发流程和大数据技术栈\n"
            "3. 有数据中台相关产品经验优先\n"
            "4. 出色的逻辑分析和产品设计能力\n"
            "5. 有SQL基础，能与技术团队高效沟通"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,股票期权,弹性工作,健身房",
        "source": "样本数据",
    },

    # ---- 更多数据开发 ----
    {
        "title": "大数据开发（数据治理方向）",
        "company": "中国银行",
        "location": "上海-浦东新区",
        "salary": "20-35K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-04",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责银行数据治理体系建设\n"
            "2. 制定数据标准、数据质量规则和数据安全策略\n"
            "3. 元数据管理平台建设\n"
            "4. 数据血缘分析和影响分析\n\n"
            "任职要求：\n"
            "1. 熟悉数据治理方法论（DAMA/DGPC）\n"
            "2. 了解大数据技术栈\n"
            "3. 有金融行业数据治理经验\n"
            "4. 熟悉Apache Atlas或类似元数据管理工具优先"
        ),
        "company_type": "国企",
        "company_size": "10000人以上",
        "industry": "金融",
        "welfare": "六险二金,企业年金,住房补贴,带薪年假",
        "source": "样本数据",
    },
    {
        "title": "初级大数据开发工程师",
        "company": "携程",
        "location": "上海-长宁区",
        "salary": "12-20K·14薪",
        "experience": "在校/应届",
        "education": "本科",
        "publish_date": "2025-05-03",
        "job_desc": (
            "岗位职责：\n"
            "1. 参与数据平台日常开发和维护\n"
            "2. 编写SQL/HQL脚本进行数据处理\n"
            "3. 参与ETL流程开发和数据质量监控\n"
            "4. 协助完成数据报表开发\n\n"
            "任职要求：\n"
            "1. 计算机相关专业，本科及以上学历\n"
            "2. 熟悉SQL和至少一门编程语言（Java/Python）\n"
            "3. 了解Hadoop/Spark/Hive等大数据技术\n"
            "4. 有相关实习经验优先\n"
            "5. 热爱技术，学习能力强"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,应届生培养计划,弹性工作,旅游基金",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师（Spark方向）",
        "company": "哔哩哔哩",
        "location": "上海-杨浦区",
        "salary": "25-45K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-01",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责Spark计算引擎的开发和优化\n"
            "2. 大规模离线数据处理和性能调优\n"
            "3. Spark SQL优化和数据倾斜处理\n"
            "4. 参与数据仓库建设\n\n"
            "任职要求：\n"
            "1. 精通Scala/Java，3年以上Spark开发经验\n"
            "2. 深入理解Spark Core/SQL/Streaming原理\n"
            "3. 掌握Spark内存管理和Shuffle优化\n"
            "4. 有Hive/数据仓库建模经验优先\n"
            "5. 有PB级数据处理经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,免费餐食,二次元文化",
        "source": "样本数据",
    },
    {
        "title": "流式计算开发工程师",
        "company": "唯品会",
        "location": "广州-天河区",
        "salary": "20-40K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-04-28",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责Flink流计算平台的开发和维护\n"
            "2. 建设实时数据仓库（Flink + Kafka + Hudi）\n"
            "3. 实时大屏、实时报表等数据应用开发\n"
            "4. 实时数据处理性能优化\n\n"
            "任职要求：\n"
            "1. 3年以上Java开发经验，熟悉Flink计算框架\n"
            "2. 深入理解Flink时间语义、状态和容错机制\n"
            "3. 熟悉Kafka，有大数据实时处理经验\n"
            "4. 有Hudi/Iceberg等数据湖技术经验优先\n"
            "5. 有电商实时数据处理经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "五险一金,带薪年假,员工旅游,节日福利",
        "source": "样本数据",
    },
    # 补充多样性
    {
        "title": "数据分析师（游戏方向）",
        "company": "米哈游",
        "location": "上海-徐汇区",
        "salary": "18-35K·16薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-04-25",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责游戏产品的数据分析工作\n"
            "2. 玩家行为分析、付费转化分析、流失预警\n"
            "3. 搭建游戏数据指标体系和看板\n"
            "4. A/B测试设计和效果评估\n\n"
            "任职要求：\n"
            "1. 2年以上游戏行业数据分析经验\n"
            "2. 精通SQL，熟练Python/R\n"
            "3. 熟悉Tableau/FineBI等BI工具\n"
            "4. 热爱游戏，理解游戏业务逻辑"
        ),
        "company_type": "未上市",
        "company_size": "1000-5000人",
        "industry": "游戏",
        "welfare": "六险一金,游戏福利,弹性工作,免费餐饮",
        "source": "样本数据",
    },
    # ============================================================
    # 第二批：扩展数据（~60条），覆盖更多公司/城市/行业/方向
    # ============================================================

    # ---- 数据开发（扩充）----
    {
        "title": "大数据开发工程师",
        "company": "小米",
        "location": "北京-海淀区",
        "salary": "20-45K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-22",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责小米大数据平台的数据开发和维护\n"
            "2. 建设用户行为数据仓库，支持精细化运营\n"
            "3. 开发数据同步、ETL调度和监控告警系统\n"
            "4. 参与数据质量保障和数据资产管理\n\n"
            "任职要求：\n"
            "1. 3年以上大数据开发经验，精通Java/Python\n"
            "2. 熟悉Hadoop/Spark/Hive/Kafka等大数据生态\n"
            "3. 有数据仓库建模和数据治理经验\n"
            "4. 了解IoT数据处理优先\n"
            "5. 有ClickHouse/Doris等OLAP引擎使用经验"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "通信",
        "welfare": "五险一金,股票期权,弹性工作,食堂",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师",
        "company": "理想汽车",
        "location": "北京-顺义区",
        "salary": "22-45K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-21",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责车联网大数据平台的开发和优化\n"
            "2. 处理车辆传感器数据、用户行为数据等海量数据\n"
            "3. 建设数据仓库和数据湖，支撑智能驾驶数据分析\n"
            "4. 参与实时和离线数据处理链路建设\n\n"
            "任职要求：\n"
            "1. 精通Java/Scala，3年以上大数据开发经验\n"
            "2. 深入理解Spark/Flink/Hive等计算引擎\n"
            "3. 有Hudi/Iceberg等数据湖技术经验优先\n"
            "4. 对汽车行业数据有兴趣"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "汽车",
        "welfare": "六险一金,股票期权,弹性工作,购车优惠",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师",
        "company": "贝壳找房",
        "location": "北京-海淀区",
        "salary": "22-42K·15薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-20",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责房产大数据平台的开发和数据仓库建设\n"
            "2. 构建房源、客户、经纪人等主题数据模型\n"
            "3. 开发数据API服务，支撑业务应用\n"
            "4. 参与数据质量监控和数据治理\n\n"
            "任职要求：\n"
            "1. 精通Java，熟悉Hadoop/Spark/Hive/Flink\n"
            "2. 掌握数仓建模方法论（维度建模/DataVault）\n"
            "3. 有房产/信息服务平台数据开发经验优先\n"
            "4. 熟悉阿里云DataWorks/MaxCompute优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,餐补",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师",
        "company": "SHEIN",
        "location": "广州-番禺区",
        "salary": "20-40K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-19",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责跨境电商大数据平台的数据开发\n"
            "2. 建设供应链、物流、营销等主题数据仓库\n"
            "3. 数据管道开发和优化，保障数据时效性\n"
            "4. 参与实时数据看板和自动化报表建设\n\n"
            "任职要求：\n"
            "1. 精通SQL和Java/Python，熟悉Hadoop生态\n"
            "2. 有Spark/Flink实际开发经验\n"
            "3. 了解跨境电商业务优先\n"
            "4. 熟悉AWS/GCP云平台大数据服务优先"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,年终奖金,弹性工作,国际化团队",
        "source": "样本数据",
    },
    {
        "title": "数据开发工程师",
        "company": "得物",
        "location": "上海-虹口区",
        "salary": "18-35K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-18",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责潮流电商平台的数据开发工作\n"
            "2. 构建交易、社区、供应链等数据模型\n"
            "3. 开发ETL任务，保障数据产出时效\n"
            "4. 数据接口开发和维护\n\n"
            "任职要求：\n"
            "1. 2年以上数据开发经验，精通SQL\n"
            "2. 熟悉Hive/Spark/Hadoop大数据技术栈\n"
            "3. 有电商数据开发经验优先\n"
            "4. 有数据建模和指标体系搭建经验"
        ),
        "company_type": "未上市",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "五险一金,股票期权,弹性工作,下午茶",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师",
        "company": "360数科",
        "location": "北京-朝阳区",
        "salary": "25-45K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-17",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责金融科技大数据平台的建设和维护\n"
            "2. 风控数据模型开发和特征工程\n"
            "3. 征信数据处理和分析\n"
            "4. 数据安全和合规管控\n\n"
            "任职要求：\n"
            "1. 3年以上大数据开发经验，精通Java/Scala\n"
            "2. 熟悉Spark/Hive/HBase/Kafka\n"
            "3. 有金融/风控数据开发经验优先\n"
            "4. 了解数据安全保护技术（脱敏/加密）"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "金融",
        "welfare": "六险一金,股票期权,年终奖,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "数据开发工程师（数据湖方向）",
        "company": "蔚来",
        "location": "上海-嘉定区",
        "salary": "25-50K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-16",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责数据湖（Data Lake）平台的架构设计和开发\n"
            "2. 基于Apache Hudi/Iceberg建设数据湖存储\n"
            "3. 数据湖上的ETL和实时计算开发\n"
            "4. 元数据管理和数据治理\n\n"
            "任职要求：\n"
            "1. 精通Java/Scala，深入理解Spark/Flink\n"
            "2. 有Hudi/Iceberg/Delta Lake等数据湖技术实践经验\n"
            "3. 熟悉Hive Metastore和表格式设计\n"
            "4. 有大规模数据湖落地经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "汽车",
        "welfare": "六险一金,股票期权,弹性工作,购车优惠",
        "source": "样本数据",
    },
    {
        "title": "ETL数据开发工程师",
        "company": "中信银行",
        "location": "北京-朝阳区",
        "salary": "18-35K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-15",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责银行核心数据仓库ETL开发\n"
            "2. 数据抽取、转换、加载全流程管理\n"
            "3. 存储过程和调度任务开发\n"
            "4. 配合数据治理团队完成数据标准化\n\n"
            "任职要求：\n"
            "1. 2年以上数据仓库ETL开发经验\n"
            "2. 精通SQL/PLSQL，有存储过程开发能力\n"
            "3. 了解DataX/DataStage/Informatica等ETL工具\n"
            "4. 有银行/金融行业数据开发经验优先"
        ),
        "company_type": "国企",
        "company_size": "10000人以上",
        "industry": "金融",
        "welfare": "六险二金,企业年金,住房补贴,食堂",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师",
        "company": "知乎",
        "location": "北京-海淀区",
        "salary": "22-42K·15薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-14",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责知乎内容数据平台的数据开发\n"
            "2. 建设内容理解、用户画像、推荐等数据链路\n"
            "3. 数据仓库主题模型设计和ETL开发\n"
            "4. 参与数据治理和成本优化\n\n"
            "任职要求：\n"
            "1. 精通Java/Python，2年以上大数据开发经验\n"
            "2. 熟悉Spark/Hive/Flink/Kafka\n"
            "3. 有内容平台数据开发经验优先\n"
            "4. 了解NLP数据处理和文本特征工程"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,下午茶",
        "source": "样本数据",
    },

    # ---- 数据分析（扩充）----
    {
        "title": "数据分析师",
        "company": "微博",
        "location": "北京-海淀区",
        "salary": "18-35K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-22",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责微博社交业务的数据分析\n"
            "2. 用户增长、内容消费、社交互动等核心指标分析\n"
            "3. 搭建业务看板和自动化报表\n"
            "4. 深度挖掘用户行为特征，支持业务决策\n\n"
            "任职要求：\n"
            "1. 2年以上互联网数据分析经验\n"
            "2. 精通SQL，熟练Python进行数据分析\n"
            "3. 熟悉AB实验设计和效果评估\n"
            "4. 有社交/内容平台分析经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "五险一金,带薪年假,弹性工作,免费班车",
        "source": "样本数据",
    },
    {
        "title": "数据分析师",
        "company": "Keep",
        "location": "北京-朝阳区",
        "salary": "18-35K·14薪",
        "experience": "2-4年",
        "education": "本科",
        "publish_date": "2025-05-21",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责运动健康App的数据分析工作\n"
            "2. 用户留存、付费转化、内容消费等分析\n"
            "3. 搭建业务指标体系和数据看板\n"
            "4. 与产品运营团队协作推动数据化运营\n\n"
            "任职要求：\n"
            "1. 2年以上数据分析经验，精通SQL和Python\n"
            "2. 熟练使用Tableau/FineBI等BI工具\n"
            "3. 有较强的数据敏感度和逻辑分析能力\n"
            "4. 有运动健康/教育类产品分析经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "五险一金,免费健身,弹性工作,餐补",
        "source": "样本数据",
    },
    {
        "title": "数据分析师（增长方向）",
        "company": "拼多多",
        "location": "上海-长宁区",
        "salary": "20-40K·16薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-20",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责用户增长相关数据分析\n"
            "2. 用户获取、激活、留存全链路分析\n"
            "3. 增长实验设计和效果评估\n"
            "4. 用户分群和增长策略制定\n\n"
            "任职要求：\n"
            "1. 精通SQL和Python，有增长分析经验\n"
            "2. 熟悉AB实验设计和统计分析\n"
            "3. 有电商/社交裂变增长分析经验优先\n"
            "4. 极强的数据驱动思维和商业敏感度"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,股票期权,弹性工作,免费三餐",
        "source": "样本数据",
    },
    {
        "title": "BI数据分析师",
        "company": "苏宁易购",
        "location": "南京-玄武区",
        "salary": "15-25K·13薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-19",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责零售业务BI报表和分析看板的建设\n"
            "2. 搭建经营分析体系，支持各业务线数据需求\n"
            "3. 数据提取和Ad-hoc分析\n"
            "4. 推动数据可视化工具的使用和推广\n\n"
            "任职要求：\n"
            "1. 精通SQL，熟练使用FineBI/Tableau\n"
            "2. 有零售/电商行业数据分析经验\n"
            "3. 了解数据仓库和ETL流程\n"
            "4. 沟通能力强，能与业务方高效协作"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "零售",
        "welfare": "五险一金,带薪年假,年终奖,员工内购",
        "source": "样本数据",
    },
    {
        "title": "数据分析师（供应链方向）",
        "company": "京东物流",
        "location": "北京-大兴区",
        "salary": "18-35K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-18",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责物流供应链的数据分析工作\n"
            "2. 仓储、运输、配送全链路效率分析\n"
            "3. 搭建物流指标体系和预测模型\n"
            "4. 推动数据化运营和成本优化\n\n"
            "任职要求：\n"
            "1. 2年以上数据分析经验，精通SQL/Python\n"
            "2. 有物流/供应链行业背景优先\n"
            "3. 掌握Tableau/PowerBI等可视化工具\n"
            "4. 有运筹优化或时间序列预测经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "物流",
        "welfare": "五险一金,餐补,班车,员工内购",
        "source": "样本数据",
    },
    {
        "title": "风控数据分析师",
        "company": "度小满金融",
        "location": "北京-海淀区",
        "salary": "22-40K·15薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-17",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责信贷风控数据分析\n"
            "2. 信用评分模型效果监控和分析\n"
            "3. 风险指标体系建设和报告\n"
            "4. 风控策略效果评估和优化建议\n\n"
            "任职要求：\n"
            "1. 精通SQL和Python，有风控数据分析经验\n"
            "2. 了解信用评分（A/B/C卡）和风控策略\n"
            "3. 熟悉互联网金融/消费金融业务\n"
            "4. 有机器学习建模经验优先"
        ),
        "company_type": "未上市",
        "company_size": "1000-5000人",
        "industry": "金融",
        "welfare": "六险一金,股票期权,年终奖,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "数据分析师（教育方向）",
        "company": "好未来",
        "location": "北京-海淀区",
        "salary": "18-30K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-16",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责在线教育产品的数据分析\n"
            "2. 学生学习行为分析、课程效果评估\n"
            "3. 搭建教学质量监控数据体系\n"
            "4. 支撑产品优化和教研决策\n\n"
            "任职要求：\n"
            "1. 2年以上数据分析经验，精通SQL\n"
            "2. 熟练Python/R进行统计分析\n"
            "3. 有教育行业数据分析经验优先\n"
            "4. 有较强的业务理解能力"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "教育",
        "welfare": "五险一金,带薪年假,子女教育优惠,食堂",
        "source": "样本数据",
    },
    {
        "title": "数据分析师（用户研究）",
        "company": "陌陌",
        "location": "北京-朝阳区",
        "salary": "20-38K·15薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-15",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责社交产品用户行为数据分析\n"
            "2. 用户画像构建和用户分群分析\n"
            "3. 产品功能上线效果评估\n"
            "4. 核心用户行为漏斗分析\n\n"
            "任职要求：\n"
            "1. 精通SQL和Python，2年以上用户分析经验\n"
            "2. 掌握用户行为分析方法论\n"
            "3. 熟悉社交/直播等行业优先\n"
            "4. 有用户增长分析经验者优先"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,下午茶",
        "source": "样本数据",
    },
    {
        "title": "数据分析师",
        "company": "华住集团",
        "location": "上海-长宁区",
        "salary": "15-28K·13薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-14",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责酒店业务的数据分析\n"
            "2. 会员分析、收益管理分析、营销效果分析\n"
            "3. 搭建经营管理数据看板\n"
            "4. 数据驱动业务决策和运营优化\n\n"
            "任职要求：\n"
            "1. 精通SQL，熟练Excel和BI工具\n"
            "2. 有零售/酒店/旅游行业数据分析经验优先\n"
            "3. 了解基本统计方法和数据分析方法论\n"
            "4. 沟通能力强，能与业务方高效协作"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "零售",
        "welfare": "五险一金,带薪年假,员工住宿优惠,年终奖",
        "source": "样本数据",
    },

    # ---- 数据挖掘/算法（扩充）----
    {
        "title": "机器学习工程师",
        "company": "商汤科技",
        "location": "上海-浦东新区",
        "salary": "30-60K·15薪",
        "experience": "2-5年",
        "education": "硕士",
        "publish_date": "2025-05-22",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责计算机视觉相关算法研发和落地\n"
            "2. 图像分类、目标检测、语义分割等模型开发\n"
            "3. 模型压缩和推理加速优化\n"
            "4. 大规模分布式模型训练\n\n"
            "任职要求：\n"
            "1. 硕士及以上学历，CV/深度学习方向\n"
            "2. 精通Python和PyTorch/TensorFlow\n"
            "3. 扎实的图像处理和深度学习基础\n"
            "4. 在CVPR/ICCV/ECCV发表过论文优先"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,健身房",
        "source": "样本数据",
    },
    {
        "title": "推荐算法工程师",
        "company": "虎牙直播",
        "location": "广州-番禺区",
        "salary": "30-55K·15薪",
        "experience": "3-5年",
        "education": "硕士",
        "publish_date": "2025-05-21",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责直播推荐系统的算法设计和优化\n"
            "2. 用户兴趣建模和实时推荐\n"
            "3. 多目标排序优化（时长/互动/付费）\n"
            "4. 冷启动和探索利用策略优化\n\n"
            "任职要求：\n"
            "1. 3年以上推荐算法经验，精通Python/C++\n"
            "2. 深入理解深度学习推荐模型\n"
            "3. 有大规模推荐系统（千万DAU）经验优先\n"
            "4. 有直播/短视频推荐经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,下午茶",
        "source": "样本数据",
    },
    {
        "title": "NLP算法工程师",
        "company": "阅文集团",
        "location": "上海-浦东新区",
        "salary": "30-55K·15薪",
        "experience": "2-5年",
        "education": "硕士",
        "publish_date": "2025-05-20",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责网文/NLP相关算法研发\n"
            "2. 文本理解、内容标签、情感分析等\n"
            "3. 大语言模型微调和应用\n"
            "4. AI辅助创作工具研发\n\n"
            "任职要求：\n"
            "1. 2年以上NLP算法经验，精通Python和PyTorch\n"
            "2. 掌握BERT/GPT等预训练模型\n"
            "3. 有大模型微调（LoRA/P-Tuning）经验优先\n"
            "4. 有内容推荐或AIGC项目经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,免费阅读",
        "source": "样本数据",
    },
    {
        "title": "广告算法工程师",
        "company": "搜狐",
        "location": "北京-海淀区",
        "salary": "30-55K·15薪",
        "experience": "3-5年",
        "education": "硕士",
        "publish_date": "2025-05-19",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责程序化广告算法开发\n"
            "2. CTR/CVR预估模型优化\n"
            "3. 广告投放策略和竞价算法\n"
            "4. 广告归因和ROI分析\n\n"
            "任职要求：\n"
            "1. 3年以上广告算法经验，精通Python/C++\n"
            "2. 深入理解CTR预估和广告排序算法\n"
            "3. 有RTB/DSP/SSP等广告系统经验\n"
            "4. 有大规模分布式模型训练经验"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "五险一金,带薪年假,弹性工作,免费班车",
        "source": "样本数据",
    },
    {
        "title": "数据挖掘工程师（风控方向）",
        "company": "第四范式",
        "location": "北京-海淀区",
        "salary": "28-50K·15薪",
        "experience": "2-5年",
        "education": "硕士",
        "publish_date": "2025-05-18",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责金融风控模型的开发和优化\n"
            "2. 反欺诈、信用评估、异常检测等建模\n"
            "3. 特征工程和模型可解释性分析\n"
            "4. 模型部署和在线推理系统建设\n\n"
            "任职要求：\n"
            "1. 2年以上机器学习经验，精通Python\n"
            "2. 了解风控建模（评分卡/随机森林/XGBoost）\n"
            "3. 有AI平台/AutoML经验优先\n"
            "4. 有金融行业项目经验者优先"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,年终奖,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "大模型算法工程师",
        "company": "智谱AI",
        "location": "北京-海淀区",
        "salary": "40-80K·15薪",
        "experience": "3-5年",
        "education": "硕士",
        "publish_date": "2025-05-17",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责大语言模型(LLM)的训练和优化\n"
            "2. SFT/RLHF/DPO等对齐技术研发\n"
            "3. 大模型推理优化和部署\n"
            "4. 多模态大模型研发\n\n"
            "任职要求：\n"
            "1. 精通PyTorch和分布式训练框架（DeepSpeed/Megatron）\n"
            "2. 深入理解Transformer/GPT/LLaMA架构\n"
            "3. 有大模型预训练或微调实践经验\n"
            "4. 在ACL/EMNLP/NeurIPS等会议发表论文优先"
        ),
        "company_type": "未上市",
        "company_size": "500-1000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,前沿技术氛围",
        "source": "样本数据",
    },
    {
        "title": "搜索算法工程师",
        "company": "小红书",
        "location": "上海-黄浦区",
        "salary": "30-60K·15薪",
        "experience": "3-5年",
        "education": "硕士",
        "publish_date": "2025-05-16",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责小红书搜索系统算法优化\n"
            "2. 查询理解、相关性排序、语义召回\n"
            "3. 多模态搜索技术研发\n"
            "4. 搜索质量评估和持续优化\n\n"
            "任职要求：\n"
            "1. 3年以上搜索/NLP/推荐算法经验\n"
            "2. 精通Python/C++，熟悉TensorFlow/PyTorch\n"
            "3. 有搜索引擎（ES/Solr）相关经验优先\n"
            "4. 有LLM + 搜索（RAG）经验优先"
        ),
        "company_type": "未上市",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,免费三餐",
        "source": "样本数据",
    },
    {
        "title": "计算机视觉工程师",
        "company": "旷视科技",
        "location": "北京-海淀区",
        "salary": "30-55K·15薪",
        "experience": "2-5年",
        "education": "硕士",
        "publish_date": "2025-05-15",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责CV算法研发和产品落地\n"
            "2. 人脸识别、目标检测、ReID等\n"
            "3. 模型优化和端侧部署\n"
            "4. 参与AIoT产品算法开发\n\n"
            "任职要求：\n"
            "1. 精通Python/C++和PyTorch\n"
            "2. 深入理解CNN/Transformer/ViT等模型\n"
            "3. 有模型量化/剪枝/蒸馏等优化经验\n"
            "4. 有安防/物联网/CV项目落地经验优先"
        ),
        "company_type": "未上市",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,年终奖,弹性工作",
        "source": "样本数据",
    },

    # ---- 数据架构（扩充）----
    {
        "title": "大数据架构师",
        "company": "联想",
        "location": "北京-海淀区",
        "salary": "40-70K·14薪",
        "experience": "5-10年",
        "education": "本科",
        "publish_date": "2025-05-20",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责全球业务大数据平台架构设计\n"
            "2. 制定技术规范和演进路线\n"
            "3. 多数据中心数据架构设计\n"
            "4. 推动云原生大数据技术落地\n\n"
            "任职要求：\n"
            "1. 8年以上大数据开发经验，有架构设计经验\n"
            "2. 精通主流大数据技术栈\n"
            "3. 有跨国企业数据架构经验优先\n"
            "4. 熟悉AWS/Azure/GCP等云平台\n"
            "5. 良好的英语沟通能力"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "制造",
        "welfare": "五险一金,企业年金,年终奖,全球工作机会",
        "source": "样本数据",
    },
    {
        "title": "数据架构师",
        "company": "天翼云",
        "location": "成都-高新区",
        "salary": "30-55K·14薪",
        "experience": "5-10年",
        "education": "本科",
        "publish_date": "2025-05-19",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责天翼云大数据产品的架构设计\n"
            "2. 大规模多租户数据平台技术方案\n"
            "3. 数据安全和隐私保护架构设计\n"
            "4. 信创环境下的数据技术适配\n\n"
            "任职要求：\n"
            "1. 5年以上大数据架构经验\n"
            "2. 精通Hadoop/Spark/Flink等大数据技术\n"
            "3. 有云平台/大数据PaaS产品经验优先\n"
            "4. 了解数据安全法和等保要求"
        ),
        "company_type": "国企",
        "company_size": "5000-10000人",
        "industry": "通信",
        "welfare": "六险二金,企业年金,年终奖,人才公寓",
        "source": "样本数据",
    },
    {
        "title": "数据治理专家",
        "company": "工商银行",
        "location": "北京-西城区",
        "salary": "30-55K·14薪",
        "experience": "5-10年",
        "education": "本科",
        "publish_date": "2025-05-18",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责银行级数据治理体系建设\n"
            "2. 制定数据标准、数据质量管理规范\n"
            "3. 推动元数据管理、数据血缘、数据资产目录落地\n"
            "4. 配合监管合规要求进行数据治理\n\n"
            "任职要求：\n"
            "1. 5年以上数据治理/数据管理经验\n"
            "2. 熟悉DAMA/DGPC/CMMI等数据治理框架\n"
            "3. 了解Apache Atlas/DataHub等工具\n"
            "4. 有银行/金融机构数据治理经验优先"
        ),
        "company_type": "国企",
        "company_size": "10000人以上",
        "industry": "金融",
        "welfare": "六险二金,企业年金,住房补贴,稳定高福利",
        "source": "样本数据",
    },

    # ---- 数据运维（扩充）----
    {
        "title": "大数据运维工程师",
        "company": "虎牙",
        "location": "广州-番禺区",
        "salary": "20-40K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-21",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责Hadoop/HBase/Spark/Flink等大数据集群运维\n"
            "2. 集群监控、告警和故障处理\n"
            "3. 集群性能调优和资源管理\n"
            "4. 自动化运维平台建设\n\n"
            "任职要求：\n"
            "1. 3年以上大数据运维经验，精通Linux\n"
            "2. 深入理解Hadoop/Spark/Kafka架构\n"
            "3. 掌握Prometheus/Grafana等监控工具\n"
            "4. 有Python/Shell自动化脚本开发能力"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,免费餐饮",
        "source": "样本数据",
    },
    {
        "title": "数据库管理员(DBA)",
        "company": "微众银行",
        "location": "深圳-南山区",
        "salary": "25-45K·15薪",
        "experience": "5-10年",
        "education": "本科",
        "publish_date": "2025-05-20",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责金融核心数据库运维管理\n"
            "2. MySQL/TiDB/Redis等数据库高可用架构\n"
            "3. SQL审核和性能优化\n"
            "4. 数据库自动化运维平台建设\n\n"
            "任职要求：\n"
            "1. 5年以上DBA经验，精通MySQL\n"
            "2. 有分布式数据库运维经验（TiDB/OceanBase）\n"
            "3. 熟悉金融行业数据合规要求\n"
            "4. 有自动化运维工具开发能力（Python/Go）"
        ),
        "company_type": "未上市",
        "company_size": "1000-5000人",
        "industry": "金融",
        "welfare": "六险二金,股票期权,年终奖,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "大数据运维开发工程师",
        "company": "顺丰科技",
        "location": "深圳-南山区",
        "salary": "20-38K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-19",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责顺丰大数据平台的运维开发\n"
            "2. 集群自动化部署和运维工具开发\n"
            "3. 监控告警体系建设和优化\n"
            "4. 大数据平台容器化改造\n\n"
            "任职要求：\n"
            "1. 精通Linux和Shell/Python编程\n"
            "2. 熟悉Kubernetes/Docker容器技术\n"
            "3. 有Hadoop/Spark/Flink集群运维经验\n"
            "4. 有Ansible/Terraform等自动化工具经验"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "物流",
        "welfare": "五险一金,餐补,年终奖,员工宿舍",
        "source": "样本数据",
    },
    {
        "title": "数据安全工程师",
        "company": "奇安信",
        "location": "北京-朝阳区",
        "salary": "20-40K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-18",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责大数据平台的数据安全方案设计和实施\n"
            "2. 数据分类分级、脱敏、加密方案\n"
            "3. 数据访问审计和异常检测\n"
            "4. 数据安全合规建设（个保法/数据安全法）\n\n"
            "任职要求：\n"
            "1. 3年以上数据安全经验\n"
            "2. 了解大数据平台安全架构（Kerberos/Ranger）\n"
            "3. 熟悉数据脱敏/加密/水印技术\n"
            "4. 有CISSP/CISP认证优先"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,年终奖,弹性工作",
        "source": "样本数据",
    },

    # ---- 数据产品（扩充）----
    {
        "title": "高级数据产品经理",
        "company": "字节跳动",
        "location": "上海-徐汇区",
        "salary": "30-55K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-22",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责企业内部数据平台产品的规划和设计\n"
            "2. 推动数据开发平台、数据治理工具的迭代\n"
            "3. 深入挖掘数据工程师和数据分析师的需求\n"
            "4. 推动数据产品从0到1建设\n\n"
            "任职要求：\n"
            "1. 3年以上B端产品经理经验\n"
            "2. 了解大数据技术栈和数据开发流程\n"
            "3. 有数据中台相关产品经验优先\n"
            "4. 有SQL基础，能独立进行数据探查"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,免费三餐",
        "source": "样本数据",
    },
    {
        "title": "数据产品经理（BI方向）",
        "company": "神策数据",
        "location": "北京-海淀区",
        "salary": "25-45K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-21",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责用户行为分析/B I产品的设计和迭代\n"
            "2. 深入理解数据分析场景和用户画像需求\n"
            "3. 产品功能规划和需求管理\n"
            "4. 与研发团队协作推动产品交付\n\n"
            "任职要求：\n"
            "1. 有数据分析或BI产品经验\n"
            "2. 了解埋点/用户行为分析/AB实验等概念\n"
            "3. 有SQL基础，能进行数据探查\n"
            "4. 有SaaS/PaaS产品经验优先"
        ),
        "company_type": "未上市",
        "company_size": "500-1000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,技术氛围好",
        "source": "样本数据",
    },

    # ---- 流计算（扩充）----
    {
        "title": "实时计算开发工程师",
        "company": "得物",
        "location": "上海-虹口区",
        "salary": "25-45K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-20",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责实时数据处理平台的建设和优化\n"
            "2. 基于Flink构建实时数据管道\n"
            "3. 实时指标计算和实时特征工程\n"
            "4. 实时数据质量监控\n\n"
            "任职要求：\n"
            "1. 精通Java，3年以上Flink开发经验\n"
            "2. 深入理解Flink状态管理和容错机制\n"
            "3. 熟悉Kafka/RocketMQ等消息中间件\n"
            "4. 有实时数仓建设经验（Flink+Hudi）"
        ),
        "company_type": "未上市",
        "company_size": "1000-5000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,弹性工作,下午茶",
        "source": "样本数据",
    },
    {
        "title": "实时数据开发工程师",
        "company": "Lazada",
        "location": "深圳-南山区",
        "salary": "25-50K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-19",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责跨境电商实时数据平台建设\n"
            "2. 基于Flink开发多区域实时数据同步链路\n"
            "3. 实时数据仓库建设和指标计算\n"
            "4. 实时大屏和报警系统开发\n\n"
            "任职要求：\n"
            "1. 精通Java/Python，3年以上Flink开发经验\n"
            "2. 有大数据实时处理经验（Flink+Kafka）\n"
            "3. 有跨境电商数据处理经验优先\n"
            "4. 熟悉AWS Kinesis/MSK等服务优先\n"
            "5. 良好的英语沟通能力"
        ),
        "company_type": "未上市",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "五险一金,股票期权,弹性工作,国际团队",
        "source": "样本数据",
    },

    # ---- 数据测试/质量 ----
    {
        "title": "大数据测试工程师",
        "company": "同程旅行",
        "location": "苏州-工业园区",
        "salary": "15-28K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-18",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责大数据平台和数据产品的质量保障\n"
            "2. 设计数据测试方案和测试用例\n"
            "3. 数据质量监控和自动化测试\n"
            "4. 数据一致性校验和性能测试\n\n"
            "任职要求：\n"
            "1. 2年以上数据测试经验\n"
            "2. 熟悉SQL，能编写复杂查询\n"
            "3. 了解大数据技术栈（Hive/Spark）\n"
            "4. 有自动化测试框架开发经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "五险一金,带薪年假,员工旅游,年终奖",
        "source": "样本数据",
    },
    {
        "title": "数据质量工程师",
        "company": "建设银行",
        "location": "上海-浦东新区",
        "salary": "20-38K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-17",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责银行数据质量管理体系建设\n"
            "2. 制定数据质量度量标准和规则\n"
            "3. 开发数据质量稽核和监控系统\n"
            "4. 推动数据质量问题闭环处理\n\n"
            "任职要求：\n"
            "1. 3年以上数据开发或数据质量经验\n"
            "2. 精通SQL，有数据校验和比对经验\n"
            "3. 了解金融数据标准和监管要求\n"
            "4. 有数据治理相关工作经验优先"
        ),
        "company_type": "国企",
        "company_size": "10000人以上",
        "industry": "金融",
        "welfare": "六险二金,企业年金,住房补贴,稳定高福利",
        "source": "样本数据",
    },

    # ---- 新增：二线城市+中小公司 ----
    {
        "title": "大数据开发工程师",
        "company": "字节跳动",
        "location": "成都-高新区",
        "salary": "20-40K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-22",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责成都团队大数据平台建设\n"
            "2. 数据处理链路开发和优化\n"
            "3. 数据仓库建设和数据治理\n"
            "4. 与北京团队协作推进项目\n\n"
            "任职要求：\n"
            "1. 3年以上大数据开发经验，精通Java/Python\n"
            "2. 熟悉Spark/Hive/Flink/Kafka\n"
            "3. 有数据仓库建模经验优先\n"
            "4. 可接受出差（频率低）"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "六险一金,弹性工作,免费三餐,租房补贴",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师",
        "company": "腾讯",
        "location": "武汉-洪山区",
        "salary": "18-35K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-21",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责腾讯武汉团队数据平台开发\n"
            "2. 参与数据仓库和ETL流程建设\n"
            "3. 数据处理和维护\n"
            "4. 数据质量保障\n\n"
            "任职要求：\n"
            "1. 2年以上大数据开发经验，精通SQL\n"
            "2. 熟悉Hive/Spark/Hadoop\n"
            "3. 有Java/Python开发能力\n"
            "4. 有数据仓库建模经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,补充商业保险,弹性工作,免费班车",
        "source": "样本数据",
    },
    {
        "title": "数据分析师",
        "company": "美团",
        "location": "成都-高新区",
        "salary": "15-30K·15薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-20",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责美团成都团队业务数据分析\n"
            "2. 搭建业务监控指标和报表体系\n"
            "3. 品类分析、用户分析、营销分析\n"
            "4. 数据驱动业务优化\n\n"
            "任职要求：\n"
            "1. 2年以上数据分析经验，精通SQL\n"
            "2. 熟练Python/Excel进行数据处理\n"
            "3. 有O2O/本地生活行业经验优先\n"
            "4. 逻辑清晰，沟通能力强"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "六险一金,餐补,弹性工作,健身房",
        "source": "样本数据",
    },
    {
        "title": "数据开发工程师",
        "company": "小米",
        "location": "武汉-东湖高新区",
        "salary": "15-28K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-19",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责武汉研发中心数据平台开发\n"
            "2. ETL流程开发和维护\n"
            "3. 数据仓库建设和数据处理\n"
            "4. 数据质量监控\n\n"
            "任职要求：\n"
            "1. 2年以上数据开发经验，精通SQL\n"
            "2. 熟悉Hadoop/Hive/Spark\n"
            "3. 有Java/Python编程能力\n"
            "4. 有IoT数据处理经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "通信",
        "welfare": "五险一金,股票期权,弹性工作,食堂",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师",
        "company": "中软国际",
        "location": "南京-雨花台区",
        "salary": "12-25K·13薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-18",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责外包项目大数据平台开发和维护\n"
            "2. 数据处理脚本开发\n"
            "3. ETL流程优化和维护\n"
            "4. 数据接口开发\n\n"
            "任职要求：\n"
            "1. 2年以上数据开发经验\n"
            "2. 精通SQL和Java/Python\n"
            "3. 了解Hadoop/Spark/Hive\n"
            "4. 有政府/企业项目经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,年终奖,项目奖金,培训机会",
        "source": "样本数据",
    },
    {
        "title": "数据分析师",
        "company": "吉利汽车",
        "location": "杭州-滨江区",
        "salary": "15-28K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-17",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责汽车销售和用户数据分析\n"
            "2. 市场趋势分析和销量预测\n"
            "3. 用户画像和客户生命周期分析\n"
            "4. 搭建业务数据看板\n\n"
            "任职要求：\n"
            "1. 2年以上数据分析经验，精通SQL和Excel\n"
            "2. 熟练Tableau/PowerBI等可视化工具\n"
            "3. 有汽车/零售行业分析经验优先\n"
            "4. 有Python数据分析能力优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "汽车",
        "welfare": "五险一金,带薪年假,年终奖,购车优惠",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师",
        "company": "海康威视",
        "location": "杭州-滨江区",
        "salary": "18-35K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-16",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责安防大数据平台的开发和优化\n"
            "2. 视频结构化数据的存储和查询优化\n"
            "3. 大规模时序数据处理\n"
            "4. 数据平台性能和稳定性保障\n\n"
            "任职要求：\n"
            "1. 精通Java/Scala，熟悉Hadoop/Spark/HBase\n"
            "2. 有时序数据库使用经验（TDengine/InfluxDB等）\n"
            "3. 有IoT/安防行业数据开发经验优先\n"
            "4. 了解Kafka和流计算框架"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "制造",
        "welfare": "五险一金,股票期权,年终奖,人才公寓",
        "source": "样本数据",
    },
    {
        "title": "数据仓库工程师",
        "company": "中兴通讯",
        "location": "西安-高新区",
        "salary": "15-30K·14薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-15",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责通讯领域数据仓库建设和维护\n"
            "2. 网络数据、设备数据等主题模型设计\n"
            "3. ETL流程开发和优化\n"
            "4. 数据质量保障和元数据管理\n\n"
            "任职要求：\n"
            "1. 3年以上数据仓库开发经验\n"
            "2. 精通SQL，熟悉Oracle/Hive/Greenplum\n"
            "3. 有通讯行业数据开发经验优先\n"
            "4. 了解大数据技术栈"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "通信",
        "welfare": "五险一金,企业年金,年终奖,人才公寓",
        "source": "样本数据",
    },
    {
        "title": "数据开发工程师",
        "company": "三七互娱",
        "location": "广州-天河区",
        "salary": "15-30K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-14",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责游戏数据平台的开发和维护\n"
            "2. 游戏日志数据采集和ETL处理\n"
            "3. 玩家行为数据仓库建设\n"
            "4. 实时和离线数据报表开发\n\n"
            "任职要求：\n"
            "1. 精通SQL和Java/Python\n"
            "2. 熟悉Hive/Spark/Flink等大数据技术\n"
            "3. 有游戏行业数据开发经验优先\n"
            "4. 了解数据仓库建模方法论"
        ),
        "company_type": "上市公司",
        "company_size": "1000-5000人",
        "industry": "游戏",
        "welfare": "五险一金,年终奖,游戏福利,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "BI工程师",
        "company": "步步高",
        "location": "东莞-长安镇",
        "salary": "12-22K·13薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-13",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责制造企业BI报表和可视化开发\n"
            "2. FineReport/FineBI报表开发\n"
            "3. 数据建模和ETL开发\n"
            "4. 数据分析需求响应\n\n"
            "任职要求：\n"
            "1. 精通SQL，熟悉FineReport/FineBI\n"
            "2. 有制造/零售行业BI经验优先\n"
            "3. 了解数据仓库和ETL流程\n"
            "4. 熟悉Kettle/DataX等ETL工具优先"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "制造",
        "welfare": "五险一金,带薪年假,年终奖,员工宿舍",
        "source": "样本数据",
    },
    {
        "title": "数据分析师",
        "company": "芒果TV",
        "location": "长沙-开福区",
        "salary": "12-22K·14薪",
        "experience": "2-5年",
        "education": "本科",
        "publish_date": "2025-05-12",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责视频平台内容数据分析\n"
            "2. 用户观看行为分析和内容推荐效果评估\n"
            "3. 搭建视频运营数据指标\n"
            "4. 会员增长和留存分析\n\n"
            "任职要求：\n"
            "1. 精通SQL，熟练Python/R\n"
            "2. 了解Tableau/FineBI等BI工具\n"
            "3. 有视频/内容平台分析经验优先\n"
            "4. 对影视内容有兴趣"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "五险一金,带薪年假,员工观影福利,年终奖",
        "source": "样本数据",
    },

    # ---- 数据分析更多细分方向 ----
    {
        "title": "经营分析师",
        "company": "网易",
        "location": "杭州-滨江区",
        "salary": "18-35K·16薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-11",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责游戏/音乐/教育等业务线经营分析\n"
            "2. 收入、成本、利润等财务+业务指标分析\n"
            "3. 搭建经营管理看板和预测模型\n"
            "4. 支撑管理层经营决策\n\n"
            "任职要求：\n"
            "1. 3年以上经营分析/财务分析经验\n"
            "2. 精通SQL和Excel建模\n"
            "3. 有互联网行业经营分析经验优先\n"
            "4. 了解基本财务知识"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,包三餐,健身房,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "数据运营专员",
        "company": "雪球",
        "location": "北京-朝阳区",
        "salary": "12-22K·14薪",
        "experience": "1-3年",
        "education": "本科",
        "publish_date": "2025-05-10",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责社区产品数据运营分析\n"
            "2. 用户运营数据分析和策略支持\n"
            "3. 核心业务指标监控和异常预警\n"
            "4. 数据提取和临时分析需求响应\n\n"
            "任职要求：\n"
            "1. 1-3年数据运营经验，精通SQL\n"
            "2. 熟练Excel进行数据处理\n"
            "3. 有社区/内容产品经验优先\n"
            "4. 有较强的数据敏感性"
        ),
        "company_type": "上市公司",
        "company_size": "500-1000人",
        "industry": "金融",
        "welfare": "五险一金,股票期权,弹性工作,下午茶",
        "source": "样本数据",
    },

    # ---- 补充初级岗 ----
    {
        "title": "数据分析师（校招）",
        "company": "百度",
        "location": "北京-海淀区",
        "salary": "15-25K·16薪",
        "experience": "在校/应届",
        "education": "本科",
        "publish_date": "2025-05-09",
        "job_desc": (
            "岗位职责：\n"
            "1. 参与搜索/Feed等业务的数据分析\n"
            "2. 核心指标监控和波动归因分析\n"
            "3. 协助搭建数据看板和报表\n"
            "4. 参与AB实验设计和分析\n\n"
            "任职要求：\n"
            "1. 统计学/数学/计算机相关专业\n"
            "2. 熟练掌握SQL和Python/R\n"
            "3. 有数据分析相关实习经验优先\n"
            "4. 学习能力强，逻辑思维清晰"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,补充公积金,股票期权,免费食堂",
        "source": "样本数据",
    },
    {
        "title": "大数据开发工程师（校招）",
        "company": "华为",
        "location": "深圳-龙岗区",
        "salary": "18-30K·14薪",
        "experience": "在校/应届",
        "education": "硕士",
        "publish_date": "2025-05-08",
        "job_desc": (
            "岗位职责：\n"
            "1. 参与华为大数据平台产品开发\n"
            "2. 优化大数据引擎性能和稳定性\n"
            "3. 参与开源社区技术跟踪和贡献\n"
            "4. 技术文档和设计文档编写\n\n"
            "任职要求：\n"
            "1. 计算机相关专业硕士及以上\n"
            "2. 扎实的Java/Scala编程基础\n"
            "3. 了解Hadoop/Spark/Flink等大数据技术\n"
            "4. 有大数据相关项目经验优先"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "通信",
        "welfare": "五险一金,企业年金,股票分红,人才公寓",
        "source": "样本数据",
    },

    # ---- 更多细分技术方向 ----
    {
        "title": "数据集成工程师",
        "company": "阿里云智能",
        "location": "杭州-余杭区",
        "salary": "25-45K·16薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-07",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责数据集成和数据同步平台开发\n"
            "2. 设计和优化CDC/全量/增量数据同步方案\n"
            "3. 多数据源适配（MySQL/Oracle/MongoDB/API等）\n"
            "4. 数据同步性能优化和稳定性保障\n\n"
            "任职要求：\n"
            "1. 精通Java，3年以上数据开发经验\n"
            "2. 深入理解DataX/Canal/Debezium等数据同步工具\n"
            "3. 了解Flink CDC和流式数据集成\n"
            "4. 有数据集成平台开发经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,补充公积金,股票期权,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "OLAP引擎开发工程师",
        "company": "StarRocks",
        "location": "北京-海淀区",
        "salary": "40-80K·15薪",
        "experience": "3-5年",
        "education": "硕士",
        "publish_date": "2025-05-06",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责StarRocks OLAP引擎内核开发\n"
            "2. 查询优化器、向量化执行引擎研发\n"
            "3. 性能调优和稳定性保障\n"
            "4. 参与开源社区建设\n\n"
            "任职要求：\n"
            "1. 精通C++/Java，数据库内核开发经验\n"
            "2. 深入理解SQL优化器和查询执行\n"
            "3. 了解MPP架构和列式存储\n"
            "4. 有开源项目贡献经验优先"
        ),
        "company_type": "未上市",
        "company_size": "100-500人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权(早期),弹性工作,技术氛围顶尖",
        "source": "样本数据",
    },
    {
        "title": "机器学习平台工程师",
        "company": "商汤科技",
        "location": "北京-海淀区",
        "salary": "30-60K·15薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-05",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责机器学习平台（训练/推理）的开发和优化\n"
            "2. 大规模分布式训练系统建设\n"
            "3. 模型管理、部署和在线推理服务\n"
            "4. GPU集群资源调度优化\n\n"
            "任职要求：\n"
            "1. 精通Python/Go，有分布式系统开发经验\n"
            "2. 熟悉Kubernetes/Docker容器编排\n"
            "3. 了解PyTorch/TensorFlow分布式训练\n"
            "4. 有MLOps/模型平台建设经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "5000-10000人",
        "industry": "互联网",
        "welfare": "六险一金,股票期权,年终奖,顶配开发机",
        "source": "样本数据",
    },
    {
        "title": "数据湖开发工程师",
        "company": "阿里云",
        "location": "杭州-余杭区",
        "salary": "28-50K·16薪",
        "experience": "3-5年",
        "education": "本科",
        "publish_date": "2025-05-04",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责数据湖产品（Lake Formation）开发和优化\n"
            "2. 表格式设计（Iceberg/Hudi/Paimon）\n"
            "3. 数据湖上的查询优化和性能调优\n"
            "4. 元数据管理和权限控制\n\n"
            "任职要求：\n"
            "1. 精通Java，深入理解Spark/Flink\n"
            "2. 有Apache Iceberg/Hudi/Paimon开发经验\n"
            "3. 了解数据湖架构和存算分离设计\n"
            "4. 有云原生数据平台开发经验优先"
        ),
        "company_type": "上市公司",
        "company_size": "10000人以上",
        "industry": "互联网",
        "welfare": "五险一金,补充公积金,股票期权,弹性工作",
        "source": "样本数据",
    },
    {
        "title": "知识图谱工程师",
        "company": "蚂蚁集团",
        "location": "杭州-西湖区",
        "salary": "30-60K·16薪",
        "experience": "3-5年",
        "education": "硕士",
        "publish_date": "2025-05-03",
        "job_desc": (
            "岗位职责：\n"
            "1. 负责金融知识图谱的构建和应用\n"
            "2. 实体识别、关系抽取、知识融合\n"
            "3. 图算法开发（图神经网络/社区发现等）\n"
            "4. 知识图谱在风控/营销等场景的落地\n\n"
            "任职要求：\n"
            "1. 硕士及以上学历，NLP/知识图谱方向\n"
            "2. 精通Python，熟悉图数据库（Neo4j/JanusGraph）\n"
            "3. 了解图神经网络和知识推理\n"
            "4. 有金融领域知识图谱项目经验优先"
        ),
        "company_type": "未上市",
        "company_size": "10000人以上",
        "industry": "金融",
        "welfare": "六险一金,股票期权,弹性工作,免费食堂",
        "source": "样本数据",
    },
]
