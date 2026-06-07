#!/usr/bin/env python3
"""
大数据岗位招聘信息采集与标签系统
主入口
"""
import sys
import os

# 确保项目根目录和模块路径在 Python 路径中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'data_collection(数据采集)'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'feature_engineering(特征工程)'))

from scraper import BigDataJobScraper
from processor import process_all, save_processed
from labeler import label_all, generate_statistics, save_labeled, export_excel


def ensure_dependencies():
    """检查并提示安装依赖"""
    missing = []
    optional = []
    try:
        import requests
    except ImportError:
        missing.append("requests")
    try:
        import bs4
    except ImportError:
        missing.append("beautifulsoup4")
    try:
        import lxml
    except ImportError:
        missing.append("lxml")
    try:
        import jieba
    except ImportError:
        missing.append("jieba")
    try:
        import pandas
    except ImportError:
        missing.append("pandas")
    try:
        import openpyxl
    except ImportError:
        missing.append("openpyxl")
    try:
        import curl_cffi
    except ImportError:
        optional.append("curl_cffi")

    if missing:
        print(f"缺少依赖: {', '.join(missing)}")
        print(f"请运行: pip install {' '.join(missing)}")
        print("或直接: pip install -r requirements.txt")
        return False
    if optional:
        print(f"提示: 安装 curl_cffi 可支持腾讯招聘等站点的数据采集")
        print(f"  pip install curl_cffi")
    return True


def main():
    print("=" * 60)
    print("  大数据岗位招聘信息采集与标签特征处理系统")
    print("=" * 60)

    if not ensure_dependencies():
        return

    # ============================================================
    # Step 1: 数据采集
    # ============================================================
    print("\n[Step 1/4] 数据采集")
    scraper = BigDataJobScraper(delay=2.0)  # 请求间隔2秒，避免被封
    raw_jobs = scraper.scrape_all(pages=3)  # 爬取3页

    if not raw_jobs:
        print("\n⚠️  未能爬取到数据。可能是网站反爬或网络问题。")
        print("尝试加载本地样本数据...")
        # 如果有本地缓存则加载
        if os.path.exists("data/raw_jobs.json"):
            import json
            with open("data/raw_jobs.json", "r", encoding="utf-8") as f:
                raw_jobs = json.load(f)
            print(f"已加载本地缓存 {len(raw_jobs)} 条")
        else:
            print("无本地缓存，退出。")
            return

    scraper.save_raw("data/raw_jobs.json")

    # ============================================================
    # Step 2: 文本处理
    # ============================================================
    print(f"\n[Step 2/4] 文本处理")
    processed_jobs = process_all(raw_jobs)
    save_processed(processed_jobs, "data/processed_jobs.json")

    # ============================================================
    # Step 3: 标签与特征提取
    # ============================================================
    print(f"\n[Step 3/4] 标签与特征处理")
    labeled_jobs = label_all(processed_jobs)

    # 生成统计
    stats = generate_statistics(labeled_jobs)

    # 保存标注结果
    save_labeled(labeled_jobs, stats, "data/labeled_jobs.json")

    # ============================================================
    # Step 4: 导出
    # ============================================================
    print(f"\n[Step 4/4] 导出Excel")
    export_excel(labeled_jobs, "data/bigdata_jobs_labeled.xlsx")

    # ============================================================
    # 总结
    # ============================================================
    print("\n" + "=" * 60)
    print("  处理完成！")
    print("=" * 60)
    print(f"""
输出文件：
  data/raw_jobs.json            - 原始爬取数据
  data/processed_jobs.json      - 文本处理后数据
  data/labeled_jobs.json        - 标签标注后数据
  data/bigdata_jobs_labeled.xlsx - Excel 表格

数据概况：
  岗位总数: {stats['total_jobs']}
  识别大类: {len(stats['category_distribution'])} 个
  经验等级: {len(stats['level_distribution'])} 个层级
  技术栈标签: {len(stats['tech_stack_top20'])} 种技术

标签体系说明：
- 大类标签: {', '.join(k for k in stats['category_distribution'].keys() if k)}
- 经验等级: 初级/中级/高级/管理-负责人
- 技术栈: Hadoop生态、Spark、Flink、Hive、Kafka等
- 编程语言: Java/Python/Scala/SQL/Go等
- 云平台: 阿里云/腾讯云/华为云/AWS/Azure等
- 行业: 金融/互联网/游戏/教育/医疗等
""")


if __name__ == "__main__":
    main()
