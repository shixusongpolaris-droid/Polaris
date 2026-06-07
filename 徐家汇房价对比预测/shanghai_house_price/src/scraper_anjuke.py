"""
安居客上海二手房数据爬取模块
目标区域：徐家汇商圈周边约10km²（徐汇区核心板块）
"""
import re
import time
import random
import requests
import pandas as pd
from fake_useragent import UserAgent
from bs4 import BeautifulSoup


class AnjukeScraper:
    """安居客上海二手房爬虫"""

    # 安居客上海二手房基础URL（支持区域筛选）
    BASE_URL = "https://sh.anjuke.com/sale/"

    # 徐家汇周边区域（安居客的区域/板块名称）
    TARGET_AREAS = [
        "xuhui",          # 徐汇区
        "xujiahui",       # 徐家汇板块
        "wanpingnanlu",   # 宛平南路
        "shanghaitiyuchang",  # 上海体育场
        "fenglinlu",      # 枫林路
        "hengshanlu",     # 衡山路
        "longhua",        # 龙华
    ]

    def __init__(self):
        self.ua = UserAgent()
        self.session = requests.Session()
        self.all_listings = []

    def _get_headers(self):
        """
        构建请求头
        注意：安居客反爬严格，建议按以下步骤获取真实Cookie：
        1. 用Chrome打开 https://sh.anjuke.com/sale/，手动过验证码
        2. F12 → Application → Cookies → 复制关键cookie字段
        3. 粘贴到下方Cookie中
        """
        # ========== 如果有真实Cookie可填在这里 ==========
        cookie_str = ""
        # 例如: cookie_str = "aQQ_ajkguid=xxx; sessid=xxx; ..."
        # =============================================

        return {
            "User-Agent": self.ua.random,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://sh.anjuke.com/",
            "Cookie": cookie_str if cookie_str else "",
        }

    def _parse_listing(self, item_tag, soup_context=None):
        """
        解析单个房源卡片，提取25个属性

        安居客页面结构（可能随版本变化，需根据实际情况调整CSS选择器）：
        - 房源卡片: div.property 或 li.list-item
        - 标题: a.property-title 或 h3.property-content-title-name
        - 总价: span.property-price-total
        - 单价: span.property-price-average
        - 详情: div.property-content-info 或 div.property-content-info-text
        - 小区: span.community-name 或 a.property-community-name
        - 标签: div.property-tags 或 span.tag
        """
        try:
            # ============ 解析总价 ============
            total_price = None
            price_tag = item_tag.find("span", class_=re.compile(r"property-price-total|price-total|total-price"))
            if not price_tag:
                price_tag = item_tag.find("span", class_=re.compile(r"price"))
            if price_tag:
                price_text = price_tag.get_text(strip=True)
                price_match = re.search(r"([\d.]+)\s*万", price_text)
                if price_match:
                    total_price = float(price_match.group(1))

            # ============ 解析单价 ============
            unit_price = None
            unit_price_tag = item_tag.find("span", class_=re.compile(r"property-price-average|price-average|unit-price"))
            if unit_price_tag:
                unit_text = unit_price_tag.get_text(strip=True)
                unit_match = re.search(r"([\d,]+)\s*元/㎡", unit_text.replace(",", ""))
                if unit_match:
                    unit_price = float(unit_match.group(1))

            # ============ 解析标题 ============
            title_text = ""
            title_tag = item_tag.find("a", class_=re.compile(r"property-title|title|house-title"))
            if title_tag:
                title_text = title_tag.get_text(strip=True)

            # ============ 解析房屋详情信息块 ============
            info_text = ""
            info_tag = item_tag.find("div", class_=re.compile(r"property-content-info|details|info"))
            if not info_tag:
                info_tag = item_tag.find("div", class_=re.compile(r"content"))
            if info_tag:
                info_text = info_tag.get_text(strip=True, separator=" ")

            # 用整个item的文本作为兜底
            full_text = item_tag.get_text(strip=True, separator=" | ") if not info_text else info_text

            # ---------- 建筑面积 ----------
            area = None
            area_match = re.search(r"([\d.]+)\s*㎡|\s([\d.]+)\s*平米", full_text)
            if area_match:
                area_val = area_match.group(1) or area_match.group(2)
                if area_val:
                    area = float(area_val)

            # ---------- 户型 ----------
            rooms, halls, bathrooms = None, None, None
            room_match = re.search(r"(\d+)室", full_text)
            if room_match:
                rooms = int(room_match.group(1))
            hall_match = re.search(r"(\d+)厅", full_text)
            if hall_match:
                halls = int(hall_match.group(1))
            bath_match = re.search(r"(\d+)卫", full_text)
            if bath_match:
                bathrooms = int(bath_match.group(1))

            # ---------- 朝向 ----------
            orientation = ""
            orientation_list = ["南北", "南", "东南", "西南", "东北", "西北", "东西", "东", "西", "北"]
            for ori in orientation_list:
                if ori in full_text:
                    orientation = ori
                    break

            # ---------- 楼层信息 ----------
            current_floor, total_floor = None, None
            floor_match = re.search(r"(\d+)/(\d+)\s*层|共\s*(\d+)\s*层.*?(\w+)\s*层|(\w+)\s*楼层.*?共\s*(\d+)\s*层", full_text)
            if floor_match:
                groups = floor_match.groups()
                # 尝试多种匹配模式
                nums_in_match = [g for g in groups if g is not None]
                # 简化处理：如果匹配到数字就提取
            # 更通用的楼层解析
            floor_alt = re.search(r"(\d+)/(\d+)\s*层", full_text)
            if floor_alt:
                current_floor = int(floor_alt.group(1))
                total_floor = int(floor_alt.group(2))
            else:
                # 尝试 "中层/共6层" 这种格式
                floor_alt2 = re.search(r"(低|中|高)\s*层\s*/\s*共\s*(\d+)\s*层", full_text)
                if floor_alt2:
                    level_map = {"低": 1, "中": None, "高": None}
                    current_floor = level_map.get(floor_alt2.group(1), None)
                    total_floor = int(floor_alt2.group(2))
                else:
                    # "共25层"
                    tf_match = re.search(r"共\s*(\d+)\s*层", full_text)
                    if tf_match:
                        total_floor = int(tf_match.group(1))

            # ---------- 建筑年代 ----------
            build_year = None
            year_match = re.search(r"(\d{4})\s*年建|建成.*?(\d{4})", full_text)
            if year_match:
                year_val = year_match.group(1) or year_match.group(2)
                if year_val:
                    build_year = int(year_val)

            # ---------- 装修情况 ----------
            decoration = ""
            deco_keywords = ["豪装", "精装", "简装", "毛坯"]
            for kw in deco_keywords:
                if kw in full_text:
                    decoration = kw
                    break

            # ---------- 楼栋类型 ----------
            building_type = ""
            btype_keywords = ["板楼", "塔楼", "板塔结合"]
            for bt in btype_keywords:
                if bt in full_text:
                    building_type = bt
                    break

            # ---------- 小区名称 ----------
            community = ""
            comm_tag = item_tag.find("span", class_=re.compile(r"community|village|xiaoqu"))
            if not comm_tag:
                comm_tag = item_tag.find("a", class_=re.compile(r"community|village|xiaoqu"))
            if comm_tag:
                community = comm_tag.get_text(strip=True)
            else:
                # 从title中提取小区名
                comm_match = re.search(r"【(.+?)】|(.+?)\s+\d+室", title_text)
                if comm_match:
                    community = comm_match.group(1) or comm_match.group(2)

            # ---------- 是否近地铁 ----------
            near_subway = 0
            if "地铁" in full_text or "轨交" in full_text:
                near_subway = 1
            # 检查是否有地铁标签
            subway_tag = item_tag.find("span", class_=re.compile(r"subway|metro|ditie"))
            if subway_tag:
                near_subway = 1

            # ---------- 是否电梯房 ----------
            has_elevator = 0
            if "电梯" in full_text or (total_floor and total_floor > 7):
                has_elevator = 1

            # ---------- 房源编号 ----------
            house_id = ""
            id_match = re.search(r"(\d{10,15})", str(item_tag))
            if id_match:
                house_id = id_match.group(1)
            # 也尝试从链接中提取
            link_tag = item_tag.find("a", href=re.compile(r"/view/|/detail/|/sale/"))
            if link_tag:
                href = link_tag.get("href", "")
                id_match2 = re.search(r"(\d{6,15})", href)
                if id_match2:
                    house_id = id_match2.group(1)

            listing = {
                "房源编号": house_id,
                "房屋总价_万元": total_price,
                "单价_元每平": unit_price,
                "标题": title_text,
                "建筑面积_㎡": area,
                "户型_室": rooms,
                "户型_厅": halls,
                "户型_卫": bathrooms,
                "所在楼层": current_floor,
                "总楼层": total_floor,
                "朝向": orientation,
                "装修情况": decoration,
                "建筑年代": build_year,
                "楼栋类型": building_type,
                "小区名称": community,
                "是否近地铁": near_subway,
                "是否电梯房": has_elevator,
                "原始标签": "",
                "原始关注信息": "",
                "原始位置信息": "",
                "原始房屋信息": info_text,
            }
            return listing

        except Exception as e:
            print(f"  解析单条房源出错: {e}")
            return None

    def scrape_area(self, area_code, max_pages=5):
        """爬取指定区域的多页数据"""
        # 安居客分页格式通常是: https://sh.anjuke.com/sale/xuhui/p2/
        for page in range(1, max_pages + 1):
            if page == 1:
                url = f"{self.BASE_URL}{area_code}/"
            else:
                url = f"{self.BASE_URL}{area_code}/p{page}/"

            print(f"  正在爬取: {url}")

            try:
                resp = self.session.get(url, headers=self._get_headers(), timeout=15)
                if resp.status_code != 200:
                    print(f"  状态码 {resp.status_code}，跳过")
                    continue

                # 检查是否触发验证码
                if "验证" in resp.text and ("滑块" in resp.text or "拼图" in resp.text or "点击" in resp.text):
                    print("  ⚠️ 触发安居客验证码！请手动访问网站过验证")
                    continue
                if "人机验证" in resp.text or "请完成安全验证" in resp.text:
                    print("  ⚠️ 触发人机验证，跳过")
                    continue
                if len(resp.text) < 5000:  # 页面内容过短，可能是空白页或验证页
                    print(f"  ⚠️ 页面内容过短（{len(resp.text)}字符），可能被拦截")
                    continue

                soup = BeautifulSoup(resp.text, "lxml")

                # 安居客的房源列表可能有多种class名
                listing_items = soup.find_all("li", class_=re.compile(r"list-item|listing-item|property-item"))
                if not listing_items:
                    listing_items = soup.find_all("div", class_=re.compile(r"property[^s]"))
                if not listing_items:
                    listing_items = soup.find_all("div", class_=re.compile(r"listing"))

                if not listing_items:
                    print(f"  第{page}页未找到房源卡片（可能页面结构已变化），尝试打印部分HTML供调试...")
                    # 打印前500字符帮助定位问题
                    print(f"  页面片段: {resp.text[:500]}")
                    continue

                for item in listing_items:
                    parsed = self._parse_listing(item)
                    if parsed and parsed["房屋总价_万元"] is not None:
                        self.all_listings.append(parsed)

                print(f"  第{page}页完成，本页解析到 {len(listing_items)} 个卡片，累计 {len(self.all_listings)} 条")

                # 礼貌间隔（安居客反爬敏感，间隔要长）
                time.sleep(random.uniform(8, 15))

            except requests.RequestException as e:
                print(f"  请求异常: {e}")
                time.sleep(15)
                continue

    def scrape_all(self, max_pages_per_area=5):
        """爬取所有目标区域"""
        print("=" * 50)
        print("开始爬取安居客上海二手房数据")
        print(f"目标区域: {self.TARGET_AREAS}")
        print("⚠️ 注意：安居客反爬严格，可能触发验证码导致爬取失败")
        print("=" * 50)

        for area in self.TARGET_AREAS:
            print(f"\n>>> 正在爬取区域: {area}")
            self.scrape_area(area, max_pages=max_pages_per_area)
            time.sleep(random.uniform(10, 20))  # 区域之间长间隔

        df = pd.DataFrame(self.all_listings)
        if len(df) > 0:
            df.drop_duplicates(subset=["房源编号"], inplace=True)

        print(f"\n总共采集到 {len(df)} 条去重房源数据")
        return df

    def save_raw_data(self, df, filepath="data/raw/anjuke_raw.csv"):
        """保存原始数据"""
        import os
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        print(f"原始数据已保存至: {filepath}")


# ============ 快速测试 ============
if __name__ == "__main__":
    scraper = AnjukeScraper()
    df = scraper.scrape_all(max_pages_per_area=2)
    if len(df) > 0:
        scraper.save_raw_data(df)
        print(f"\n采集成功！共 {len(df)} 条数据")
        print(f"价格范围: {df['房屋总价_万元'].min():.0f} ~ {df['房屋总价_万元'].max():.0f} 万元")
    else:
        print("\n❌ 未采集到任何数据。")
        print("可能原因：")
        print("  1. 安居客页面结构已变化（CSS选择器需调整）")
        print("  2. 触发反爬验证码")
        print("  3. 网络连接问题")
        print("\n建议：改用模拟数据运行 main.py（选择n）")