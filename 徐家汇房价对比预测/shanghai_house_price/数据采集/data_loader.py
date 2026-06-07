"""
CSV 数据解析模块 —— 解析链家爬取的原始 CSV 数据
"""
import re
import pandas as pd


def parse_house_info(info_str):
    result = {}
    if pd.isna(info_str):
        return result
    parts = [p.strip() for p in str(info_str).split('|')]
    # 户型
    if len(parts) >= 1:
        m = re.search(r'(\d+)室(\d+)厅(\d+)卫', parts[0])
        if m:
            result['户型_室'] = int(m.group(1))
            result['户型_厅'] = int(m.group(2))
            result['户型_卫'] = int(m.group(3))
        else:
            m = re.search(r'(\d+)室(\d+)厅', parts[0])
            if m:
                result['户型_室'] = int(m.group(1))
                result['户型_厅'] = int(m.group(2))
                result['户型_卫'] = 1
            else:
                m = re.search(r'(\d+)室', parts[0])
                if m:
                    result['户型_室'] = int(m.group(1))
    # 面积
    if len(parts) >= 2:
        m = re.search(r'([\d.]+)平米', parts[1])
        if m:
            result['建筑面积_㎡'] = float(m.group(1))
    # 朝向
    if len(parts) >= 3:
        result['朝向'] = parts[2]
    # 装修
    if len(parts) >= 4:
        result['装修情况'] = parts[3]
    # 楼层
    if len(parts) >= 5:
        floor_str = parts[4]
        total_floor = None
        m = re.search(r'共(\d+)层', floor_str)
        if m:
            total_floor = int(m.group(1))
        result['总楼层'] = total_floor
        if '低' in floor_str:
            result['楼层类别'] = '低层'
            result['楼层相对位置'] = 0.2
        elif '中' in floor_str:
            result['楼层类别'] = '中层'
            result['楼层相对位置'] = 0.5
        elif '高' in floor_str:
            result['楼层类别'] = '高层'
            result['楼层相对位置'] = 0.8
        m = re.search(r'(\d+)/(\d+)层', floor_str)
        if m:
            cur = int(m.group(1))
            total = int(m.group(2))
            result['所在楼层'] = cur
            result['总楼层'] = total
            result['楼层相对位置'] = cur / total
            if cur <= total / 3:
                result['楼层类别'] = '低层'
            elif cur <= 2 * total / 3:
                result['楼层类别'] = '中层'
            else:
                result['楼层类别'] = '高层'
    # 建筑年代
    if len(parts) >= 6:
        m = re.search(r'(\d{4})年', parts[5])
        if m:
            result['建筑年代'] = int(m.group(1))
    # 楼栋类型
    if len(parts) >= 7:
        result['楼栋类型'] = parts[6]
    return result


def parse_position(pos_str):
    result = {}
    if pd.isna(pos_str):
        return result
    parts = str(pos_str).split('-')
    if len(parts) >= 2:
        result['小区名称'] = parts[0].strip()
        result['板块名称'] = parts[1].strip()
    else:
        result['小区名称'] = parts[0].strip()
    return result


def parse_follow(follow_str):
    result = {}
    if pd.isna(follow_str):
        return result
    m = re.search(r'(\d+)人关注', str(follow_str))
    if m:
        result['关注人数'] = int(m.group(1))
    return result


def parse_tags(tag_str):
    result = {}
    if pd.isna(tag_str):
        return result
    tags = str(tag_str)
    result['是否近地铁'] = 1 if '近地铁' in tags else 0
    result['是否VR看房'] = 1 if 'VR' in tags else 0
    result['是否房本满五年'] = 1 if '满五年' in tags else 0
    result['是否随时看房'] = 1 if '随时看房' in tags else 0
    return result


def load_and_parse_csv(filepath):
    """加载并解析链家 CSV 数据"""
    print(f"正在加载: {filepath}")
    df = pd.read_csv(filepath, dtype=str)
    df['房屋总价_万元'] = df['总价'].str.replace(',', '').astype(float)
    df['单价_元每平'] = df['单价'].str.replace('元/平', '').str.replace(',', '').astype(float)

    house_info = df['房屋信息'].apply(parse_house_info).apply(pd.Series)
    position_info = df['位置信息'].apply(parse_position).apply(pd.Series)
    follow_info = df['关注信息'].apply(parse_follow).apply(pd.Series)
    tag_info = df['标签'].apply(parse_tags).apply(pd.Series)

    df = pd.concat([df, house_info, position_info, follow_info, tag_info], axis=1)
    print(f"解析完成，共 {len(df)} 条房源记录")
    return df
