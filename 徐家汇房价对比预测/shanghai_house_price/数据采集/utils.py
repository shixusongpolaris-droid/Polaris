"""工具函数模块"""
import numpy as np
import pandas as pd


def generate_simulated_data(n_samples=500):
    """
    生成模拟的上海徐家汇区域二手房数据
    当爬虫无法正常工作时使用此函数生成演示数据
    """
    np.random.seed(42)

    # 建筑面积
    area = np.random.uniform(30, 200, n_samples)

    # 户型
    rooms = np.random.choice([1, 2, 3, 4, 5], n_samples, p=[0.10, 0.35, 0.35, 0.15, 0.05])
    halls = np.where(rooms <= 2, 1, np.where(rooms <= 4, 2, 3))
    bathrooms = np.where(rooms <= 2, 1, np.where(rooms <= 4, 2, 3))

    # 楼层信息
    total_floor = np.random.choice([6, 11, 18, 24, 33], n_samples, p=[0.15, 0.15, 0.25, 0.25, 0.20])
    current_floor = np.array([np.random.randint(1, tf + 1) for tf in total_floor])
    floor_ratio = current_floor / total_floor

    # 建筑年代与房龄
    build_year = np.random.randint(1990, 2023, n_samples)
    house_age = 2025 - build_year

    # 朝向
    orientation_south = np.random.choice([0, 1], n_samples, p=[0.15, 0.85])
    orientation_ns = np.random.choice([0, 1], n_samples, p=[0.55, 0.45])

    # 装修等级
    decoration_level = np.random.choice([0, 1, 2, 3], n_samples, p=[0.05, 0.20, 0.50, 0.25])

    # 电梯
    has_elevator = (total_floor > 7).astype(int)
    has_elevator = np.where(np.random.random(n_samples) < 0.05, 1 - has_elevator, has_elevator)

    # 梯户比
    elevator_ratio = np.where(has_elevator == 1, np.random.uniform(0.15, 1.0, n_samples), 0)

    # 小区属性
    plot_ratio = np.random.uniform(1.5, 5.5, n_samples)
    green_ratio = np.random.uniform(0.15, 0.50, n_samples)
    property_fee = np.random.uniform(0.8, 10.0, n_samples)

    # 地铁
    subway_distance = np.random.exponential(350, n_samples)
    subway_distance = np.clip(subway_distance, 20, 2000)
    subway_count = np.random.poisson(2, n_samples)
    subway_count = np.clip(subway_count, 0, 6)

    # 学区
    school_district = np.random.choice([0, 1], n_samples, p=[0.60, 0.40])

    # 楼栋类型
    building_type = np.random.choice(["板楼", "塔楼", "板塔结合"], n_samples, p=[0.50, 0.30, 0.20])

    # 房屋用途
    usage = np.random.choice(["住宅", "商住", "公寓"], n_samples, p=[0.75, 0.10, 0.15])

    # 产权
    property_rights = np.random.choice([40, 50, 70], n_samples, p=[0.10, 0.05, 0.85])

    # --- 生成目标变量：房屋总价（万元）---
    # 徐家汇区域单价约8-15万/㎡，总价=面积×单价+噪声
    base_unit_price = 90000  # 基础单价 9万/㎡
    # 各因素对单价的影响
    price_per_sqm = (
        base_unit_price
        + (rooms - 2) * 3000                          # 户型影响
        - house_age * 800                              # 房龄折旧
        - np.abs(floor_ratio - 0.5) * 15000           # 中间楼层最贵
        + orientation_south * 5000                     # 朝南溢价
        + orientation_ns * 8000                        # 南北通透溢价
        + decoration_level * 6000                      # 装修溢价
        + has_elevator * 4000                          # 电梯溢价
        - (subway_distance / 1000) * 3000              # 地铁距离
        + subway_count * 2000                          # 地铁数量
        + school_district * 15000                      # 学区溢价
        - plot_ratio * 2500                            # 容积率
        + green_ratio * 20000                          # 绿化率
        + np.random.normal(0, 8000, n_samples)        # 随机噪声
    )
    price_per_sqm = np.clip(price_per_sqm, 30000, 180000)
    total_price = area * price_per_sqm / 10000  # 转换为万元

    # 构建DataFrame
    df = pd.DataFrame({
        "房源编号": [f"SIM{str(i).zfill(6)}" for i in range(n_samples)],
        "房屋总价_万元": total_price,
        "单价_元每平": price_per_sqm,
        "建筑面积_㎡": area,
        "户型_室": rooms,
        "户型_厅": halls,
        "户型_卫": bathrooms,
        "所在楼层": current_floor,
        "总楼层": total_floor,
        "楼层相对位置": floor_ratio,
        "朝向": np.where(orientation_ns == 1, "南北",
                  np.where(orientation_south == 1, "南", "北")),
        "朝向_南": orientation_south,
        "朝向_南北通透": orientation_ns,
        "装修情况": ["毛坯", "简装", "精装", "豪装"][np.digitize(decoration_level, [0, 1, 2])],
        "装修等级": decoration_level,
        "建筑年代": build_year,
        "房龄": house_age,
        "是否电梯房": has_elevator,
        "梯户比": elevator_ratio,
        "楼栋类型": building_type,
        "楼层类别": np.where(floor_ratio < 0.33, "低层",
                       np.where(floor_ratio < 0.67, "中层", "高层")),
        "小区容积率": plot_ratio,
        "小区绿化率": green_ratio,
        "物业费": property_fee,
        "到最近地铁站距离": subway_distance,
        "周边1km地铁站数": subway_count,
        "是否学区房": school_district,
        "是否近地铁": (subway_distance < 800).astype(int),
        "房屋用途": usage,
        "产权年限": property_rights,
        "小区名称": [f"模拟小区_{i%20}" for i in range(n_samples)],
        "标题": None,
        "原始标签": None,
        "原始关注信息": None,
        "原始位置信息": None,
        "原始房屋信息": None,
    })

    print(f"已生成 {n_samples} 条模拟二手房数据")
    print(f"价格范围: {total_price.min():.1f} ~ {total_price.max():.1f} 万元")
    print(f"均价: {total_price.mean():.1f} 万元")
    return df