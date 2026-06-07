"""
链家网上海二手房数据爬取模块
目标区域：徐家汇商圈周边约10km²
"""
import re
import time
import random
import requests
import pandas as pd
from fake_useragent import UserAgent
from bs4 import BeautifulSoup


class LianjiaScraper:
    """链家上海二手房爬虫"""

    BASE_URL = "https://sh.lianjia.com/ershoufang/"

    # 徐家汇周边区域（通过链家"商圈"参数定位）
    TARGET_AREAS = [
        "xujiahui",       # 徐家汇
        "fenglinlu",      # 枫林路
        "tianpinglu",     # 天平路
        "hunanlu",        # 湖南路
        "shanghai-tiyuchang",  # 上海体育场
        "longhua",        # 龙华（部分）
        "xietulu",        # 斜土路
    ]

    def __init__(self):
        self.ua = UserAgent()
        self.session = requests.Session()
        self.all_listings = []

    def _get_headers(self):
        """
        构建请求头，包含从真实浏览器中复制的 Cookie
        获取方法：
        1. 用 Chrome 打开 https://sh.lianjia.com/ershoufang/，手动过一下人机验证
        2. 按 F12 → Application (应用) → Cookies → 选 sh.lianjia.com
        3. 找到 lianjia_token 和 lianjia_uuid 两个值，分别复制到下方单引号中
        """
        # ========== 请在这里填入你的真实 Cookie ==========
        lianjia_token = '2.001517dd6a4f94bcf504baf45bdf274680'  # 复制 lianjia_token 的 Value
        lianjia_uuid = 'dfc78fae-2382-4449-b720-77d6c51f04f6'  # 复制 lianjia_uuid  的 Value
        # =============================================
        cookie_str = f"lianjia_token={lianjia_token}; lianjia_uuid={lianjia_uuid};"
        return {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "keep-alive",
            "Referer": "https://sh.lianjia.com/",
            "Cookie": cookie_str,
        }

    def _parse_listing(self, item_tag):
        """解析单个房源卡片，提取25个属性"""
        try:
            # --- 基础信息 ---
            title = item_tag.find("div", class_="title")
            title_text = title.find("a").get_text(strip=True) if title else ""

            # 房屋总价（万元）
            total_price_tag = item_tag.find("div", class_="totalPrice")
            total_price = None
            if total_price_tag:
                price_text = total_price_tag.get_text(strip=True)
                total_price = float(re.sub(r"[^\d.]", "", price_text))

            # 单价（元/㎡）
            unit_price_tag = item_tag.find("div", class_="unitPrice")
            unit_price = None
            if unit_price_tag:
                unit_price = float(re.sub(r"[^\d]", "", unit_price_tag.get_text(strip=True)))

            # --- 房屋属性 ---
            house_info = item_tag.find("div", class_="houseInfo")
            info_text = house_info.get_text(strip=True) if house_info else ""

            # 解析户型（室/厅/卫）
            rooms, halls, bathrooms = None, None, None
            room_match = re.search(r"(\d+)室", info_text)
            if room_match:
                rooms = int(room_match.group(1))
            hall_match = re.search(r"(\d+)厅", info_text)
            if hall_match:
                halls = int(hall_match.group(1))
            bath_match = re.search(r"(\d+)卫", info_text)
            if bath_match:
                bathrooms = int(bath_match.group(1))

            # 建筑面积
            area = None
            area_match = re.search(r"([\d.]+)㎡", info_text)
            if area_match:
                area = float(area_match.group(1))

            # 朝向
            orientation = ""
            orientation_list = ["南", "北", "东", "西", "南北", "东南", "西南", "东北", "西北", "东西"]
            for ori in orientation_list:
                if ori in info_text:
                    orientation = ori
                    break

            # 装修情况
            decoration = ""
            deco_keywords = ["豪装", "精装", "简装", "毛坯"]
            for kw in deco_keywords:
                if kw in info_text:
                    decoration = kw
                    break

            # 所在楼层与总楼层
            current_floor, total_floor = None, None
            floor_match = re.search(r"(\w+)楼层.*?共(\d+)层", info_text)
            if not floor_match:
                floor_match = re.search(r"(\d+)/(\d+)层", info_text)
            if floor_match:
                floor_str = floor_match.group(1)
                if floor_str.isdigit():
                    current_floor = int(floor_str)
                else:
                    # 低/中/高楼层
                    current_floor = {"低": 1, "中": None, "高": None}.get(floor_str[0], None)
                total_floor = int(floor_match.group(2))

            # 建筑年代
            build_year = None
            year_match = re.search(r"(\d{4})年建", info_text)
            if year_match:
                build_year = int(year_match.group(1))

            # 楼栋类型
            building_type = ""
            btype_keywords = ["板楼", "塔楼", "板塔结合"]
            for bt in btype_keywords:
                if bt in info_text:
                    building_type = bt
                    break

            # --- 位置信息 ---
            position_info = item_tag.find("div", class_="positionInfo")
            position_text = position_info.get_text(strip=True) if position_info else ""

            # 小区名称
            community = ""
            comm_match = re.search(r"(.+?)\s", position_text)
            if comm_match:
                community = comm_match.group(1)

            # --- 关注信息 ---
            follow_info = item_tag.find("div", class_="followInfo")
            follow_text = follow_info.get_text(strip=True) if follow_info else ""

            # --- 标签信息 ---
            tag_info = item_tag.find("div", class_="tag")
            tag_text = tag_info.get_text(strip=True) if tag_info else ""

            # 电梯房判断
            has_elevator = 1 if "电梯" in (info_text + tag_text) or (
                total_floor and total_floor > 7) else 0

            # 近地铁
            near_subway_tag = item_tag.find("span", class_="subway")
            near_subway = 1 if near_subway_tag else 0

            # --- 房源编号 ---
            house_id = ""
            id_match = re.search(r"/(\d+)\.html", str(item_tag))
            if id_match:
                house_id = id_match.group(1)

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
                "原始标签": tag_text,
                "原始关注信息": follow_text,
                "原始位置信息": position_text,
                "原始房屋信息": info_text,
            }
            return listing

        except Exception as e:
            print(f"解析出错: {e}")
            return None

    def scrape_area(self, area_code, max_pages=10):
        """爬取指定商圈的多页数据"""
        for page in range(1, max_pages + 1):
            url = f"{self.BASE_URL}{area_code}/pg{page}/"
            print(f"  正在爬取: {url}")

            try:
                resp = self.session.get(url, headers=self._get_headers(), timeout=15)
                if resp.status_code != 200:
                    print(f"  状态码 {resp.status_code}，跳过")
                    continue

                soup = BeautifulSoup(resp.text, "lxml")

                # 检查是否被反爬
                if "访问验证" in resp.text or "请稍后" in resp.text:
                    print("  触发反爬机制，等待后重试...")
                    time.sleep(random.uniform(30, 60))
                    continue

                # 解析房源列表
                listing_items = soup.find_all("li", class_="clear LOGVIEWDATA LOGCLICKDATA")
                if not listing_items:
                    listing_items = soup.find_all("div", class_="info clear")

                if not listing_items:
                    print(f"  第{page}页无数据，可能已到末页")
                    break

                for item in listing_items:
                    parsed = self._parse_listing(item)
                    if parsed:
                        self.all_listings.append(parsed)

                print(f"  第{page}页完成，已采集 {len(self.all_listings)} 条")
                time.sleep(random.uniform(8, 15))  # 礼貌间隔

            except requests.RequestException as e:
                print(f"  请求异常: {e}")
                time.sleep(10)
                continue

    def scrape_all(self, max_pages_per_area=8):
        """爬取所有目标区域"""
        print("=" * 50)
        print("开始爬取链家上海二手房数据")
        print(f"目标区域: {self.TARGET_AREAS}")
        print("=" * 50)

        for area in self.TARGET_AREAS:
            print(f"\n>>> 正在爬取区域: {area}")
            self.scrape_area(area, max_pages=max_pages_per_area)
            time.sleep(random.uniform(5, 10))

        df = pd.DataFrame(self.all_listings)
        df.drop_duplicates(subset=["房源编号"], inplace=True)
        print(f"\n总共采集到 {len(df)} 条去重房源数据")
        return df

    def save_raw_data(self, df, filepath="data/raw/lianjia_raw.csv"):
        """保存原始数据"""
        import os
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")
        print(f"原始数据已保存至: {filepath}")