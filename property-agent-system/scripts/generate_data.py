#!/usr/bin/env python3
"""生成「智修云」小区物业历史报修工单模拟数据。

特点：
- 固定随机种子 20261020，可重复生成完全一致的数据；
- 时间范围：截至 2026-10-20 的最近 180 天（2026-04-24 ~ 2026-10-20）；
- 按真实规律分布：工作日 8-11 点、18-21 点双高峰，周末单量略多，叠加月度波动；
- 13 类报修类型与 (设施类型, 紧急程度, SLA, 责任工种) 严格映射；
- 口语化 description（60+ 句式模板，其中约 7% 为模糊口语），保留 ground truth 标签；
- 响应/处理时长、超时、闭环、满意度、派单方式、改派、照片、处理记录、来源等全部按业务规则生成。

输出：
  data/historical_orders.csv
  data/generator_meta.json
"""
from __future__ import annotations

import csv
import json
import random
from collections import Counter, OrderedDict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BASE_DIR / "data"
CSV_PATH = DATA_DIR / "historical_orders.csv"
META_PATH = DATA_DIR / "generator_meta.json"

SEED = 20261020
N_ORDERS = 3000
END_DATE = datetime(2026, 10, 20)
START_DATE = END_DATE - timedelta(days=179)  # 2026-04-24，含首尾共 180 天
DAYS = (END_DATE.date() - START_DATE.date()).days + 1  # 180

COLUMNS = [
    "order_id", "created_at", "community", "building", "unit", "room", "description",
    "repair_type", "facility_type", "urgency", "worker_id", "worker_name", "worker_trade",
    "dispatch_mode", "reassigned", "response_minutes", "duration_minutes", "sla_minutes",
    "is_timeout", "is_closed", "satisfaction", "photos_count", "result_note", "source",
]

# 报修类型 -> (设施类型, 紧急程度, SLA 分钟, 责任工种, 抽样权重, 正常处理时长均值, 标准差, 下限, 上限)
TYPE_SPECS = OrderedDict([
    ("电梯困人", dict(facility="电梯", urgency="紧急", sla=30, trade="电梯维保", weight=1.8,
                   dur=(19.0, 5.0, 6.0, 28.0))),
    ("电梯故障", dict(facility="电梯", urgency="高", sla=60, trade="电梯维保", weight=9.5,
                   dur=(44.0, 11.0, 18.0, 57.0))),
    ("水管爆裂", dict(facility="给排水", urgency="紧急", sla=30, trade="水电工", weight=3.2,
                   dur=(20.0, 5.5, 8.0, 28.0))),
    ("水管漏水", dict(facility="给排水", urgency="高", sla=120, trade="水电工", weight=17.0,
                   dur=(82.0, 22.0, 30.0, 113.0))),
    ("下水道堵塞", dict(facility="给排水", urgency="中", sla=120, trade="水电工", weight=12.5,
                    dur=(72.0, 20.0, 25.0, 113.0))),
    ("电路故障", dict(facility="电气", urgency="高", sla=60, trade="电工", weight=11.5,
                   dur=(40.0, 10.0, 15.0, 57.0))),
    ("燃气泄漏", dict(facility="燃气", urgency="紧急", sla=15, trade="燃气抢修", weight=1.5,
                   dur=(10.5, 2.5, 5.0, 14.2))),
    ("消防隐患", dict(facility="消防", urgency="紧急", sla=15, trade="秩序维护", weight=1.5,
                   dur=(9.5, 2.2, 4.0, 14.2))),
    ("门禁故障", dict(facility="门禁", urgency="中", sla=240, trade="综合维修", weight=8.5,
                   dur=(75.0, 30.0, 20.0, 228.0))),
    ("公共照明", dict(facility="照明", urgency="中", sla=240, trade="电工", weight=9.5,
                   dur=(62.0, 22.0, 15.0, 228.0))),
    ("墙面地面破损", dict(facility="土建", urgency="低", sla=480, trade="土建维修", weight=8.5,
                     dur=(225.0, 75.0, 40.0, 465.0))),
    ("环境卫生", dict(facility="环境", urgency="低", sla=480, trade="保洁", weight=10.0,
                   dur=(58.0, 20.0, 15.0, 465.0))),
    ("其他报修", dict(facility="其他", urgency="中", sla=240, trade="综合维修", weight=6.0,
                   dur=(92.0, 35.0, 20.0, 228.0))),
])

# 维修工主数据：id -> (姓名, 工种, 片区)
WORKERS = OrderedDict([
    ("W001", ("张伟", "电梯维保", "A")),
    ("W002", ("李强", "电梯维保", "B")),
    ("W003", ("王建国", "水电工", "A")),
    ("W004", ("赵磊", "水电工", "B")),
    ("W005", ("陈明", "电工", "A")),
    ("W006", ("刘洋", "电工", "C")),
    ("W007", ("孙杰", "燃气抢修", "A")),
    ("W008", ("周敏", "综合维修", "B")),
    ("W009", ("吴芳", "保洁", "C")),
    ("W010", ("郑海", "秩序维护", "A")),
    ("W011", ("马超", "土建维修", "B")),
    ("W012", ("何静", "综合维修", "C")),
])

# 每种报修类型的派工偏好（只落在对应工种的维修工上）
TYPE_WORKER_WEIGHTS = {
    "电梯困人": {"W001": 0.60, "W002": 0.40},
    "电梯故障": {"W001": 0.45, "W002": 0.55},
    "水管爆裂": {"W003": 0.55, "W004": 0.45},
    "水管漏水": {"W003": 0.45, "W004": 0.55},
    "下水道堵塞": {"W003": 0.50, "W004": 0.50},
    "电路故障": {"W005": 0.60, "W006": 0.40},
    "燃气泄漏": {"W007": 1.00},
    "消防隐患": {"W010": 1.00},
    "门禁故障": {"W008": 0.55, "W012": 0.45},
    "公共照明": {"W005": 0.58, "W006": 0.42},
    "墙面地面破损": {"W011": 1.00},
    "环境卫生": {"W009": 1.00},
    "其他报修": {"W008": 0.50, "W012": 0.50},
}

# 社区：楼栋范围、最高楼层
COMMUNITIES = OrderedDict([
    ("锦绣家园", dict(weight=0.24, buildings=list(range(1, 11)), max_floor=18)),
    ("翠湖名居", dict(weight=0.22, buildings=list(range(1, 9)), max_floor=26)),
    ("阳光里", dict(weight=0.18, buildings=list(range(1, 7)), max_floor=11)),
    ("江南雅苑", dict(weight=0.19, buildings=list(range(1, 10)), max_floor=32)),
    ("金域蓝湾", dict(weight=0.17, buildings=list(range(1, 8)), max_floor=24)),
])

SOURCES = {"业主App": 0.42, "微信小程序": 0.28, "电话": 0.20, "前台登记": 0.10}
URGENCY_RESPONSE = {
    "紧急": (7.0, 3.2, 2.0, 15.0),
    "高": (14.0, 6.0, 5.0, 30.0),
    "中": (30.0, 12.0, 10.0, 60.0),
    "低": (62.0, 24.0, 20.0, 120.0),
}
# 超时目标概率：三类生命财产相关的紧急工单显著更低（4%~6%）
TIMEOUT_TARGET = {"电梯困人": 0.05, "燃气泄漏": 0.05, "消防隐患": 0.05}
DEFAULT_TIMEOUT_TARGET = 0.125

# 口语化描述模板（占位符：{loc} 楼栋单元、{room} 房号、{floor} 楼层、{community} 小区）
TEMPLATES = {
    "电梯困人": [
        "{loc}电梯门关不上，有人被困在里面，按警铃也没人应",
        "有人被困在{loc}电梯里，电梯停在{floor}楼不动了",
        "{loc}电梯突然停住，门打不开，里面有老人和小孩",
        "{loc}电梯困人了，已经困了十几分钟，麻烦快点来",
    ],
    "电梯故障": [
        "{loc}电梯门关不上，一直滴滴报警",
        "电梯到{floor}楼就停，按了也没反应，{loc}",
        "{loc}电梯运行的时候晃得厉害，还有异响",
        "{loc}货梯门开合很慢，经常卡住",
    ],
    "水管爆裂": [
        "{loc}水管爆裂，楼道里全是积水",
        "{room}厨房水管爆了，水喷得到处都是，赶紧来关阀",
        "{loc}外面主水管破裂，水都流到电梯口了",
        "{room}卫生间水管爆了，家里发大水了",
    ],
    "水管漏水": [
        "{room}卫生间天花板一直往下滴水",
        "{loc}水管接头渗水，墙角都泡湿了",
        "{room}厨房水槽下面漏水，柜子都发霉了",
        "{loc}楼道水管漏水，墙面湿了一大片",
    ],
    "下水道堵塞": [
        "{room}卫生间下水道反水，味道特别大",
        "{room}厨房下水道堵了，水都漫到客厅了",
        "{loc}阳台地漏不下水，积水严重",
        "{room}马桶堵了，冲了好几次都下不去",
    ],
    "电路故障": [
        "{room}家里突然跳闸，全屋都没电了",
        "{loc}电表箱有烧焦的味道，总闸推不上去",
        "{room}客厅插座没电，空气开关一推就跳",
        "{loc}配电箱有异响，灯还一闪一闪的",
    ],
    "燃气泄漏": [
        "{room}厨房闻到很浓的燃气味，不敢开灯",
        "{loc}燃气管道接口好像漏气，味道很大",
        "{room}燃气报警器响了，家里有煤气味",
        "{loc}燃气表附近有嘶嘶的声音，麻烦快来",
    ],
    "消防隐患": [
        "{loc}消防通道被杂物堵住了，人都过不去",
        "{loc}消防栓玻璃破了，水带也不见了",
        "{loc}楼道里消防指示灯不亮，晚上看不见",
        "{loc}有人把电动车推到楼道充电，电线乱拉",
    ],
    "门禁故障": [
        "{loc}门禁刷卡没反应，门打不开",
        "{loc}单元门禁一直响，门关不上",
        "{loc}门禁可视对讲没有画面，也听不到声音",
        "{loc}地下车库道闸识别不了车牌，车出不去",
    ],
    "公共照明": [
        "{loc}楼道灯不亮，晚上上下楼看不清",
        "{loc}地下车库的灯坏了好几盏，一片黑",
        "{loc}小区路灯不亮，晚上散步不安全",
        "{loc}应急照明灯不亮，也不知道是不是坏了",
    ],
    "墙面地面破损": [
        "{loc}墙面瓷砖脱落，露出了水泥",
        "{room}卫生间墙皮返潮脱落，掉了一地",
        "{loc}地面地砖翘起来了，走路容易绊倒",
        "{loc}外墙有一块墙皮掉下来了，差点砸到人",
    ],
    "环境卫生": [
        "{loc}垃圾桶满溢，垃圾都堆到桶外面了",
        "{loc}楼道里有宠物粪便，味道很重",
        "{loc}绿化带里落叶和垃圾很多，没人清理",
        "{loc}楼道有异味，需要消杀一下",
    ],
    "其他报修": [
        "{loc}公共区域的长椅坏了，木条断了一根",
        "{room}窗户把手坏了，关不严实",
        "{loc}健身器材螺丝松动，看着挺危险",
        "{loc}晾衣架断了一根，需要更换",
    ],
}

# 模糊口语（约 7%），仍能从语义推断到对应类型
VAGUE_TEMPLATES = {
    "电梯困人": ["电梯出事了，人出不来", "电梯把人关里面了，快点来"],
    "电梯故障": ["电梯坏了，按了没反应", "电梯不动了，上不去"],
    "水管爆裂": ["家里发大水了", "水管爆了，到处是水"],
    "水管漏水": ["家里漏水了", "水管一直滴水，烦死了", "家里没水了"],
    "下水道堵塞": ["下水道不通了", "家里下水道堵了，味道大"],
    "电路故障": ["家里没电了", "灯不亮，插座也没电"],
    "燃气泄漏": ["家里有燃气味", "好像燃气漏了，味道很重"],
    "消防隐患": ["消防通道被堵了", "消防栓坏了，没人管"],
    "门禁故障": ["门禁坏了，进不去", "单元门打不开"],
    "公共照明": ["灯不亮", "楼道灯坏了，一片黑"],
    "墙面地面破损": ["墙面掉皮了", "地面砖坏了，翘起来了"],
    "环境卫生": ["楼道太脏了", "垃圾桶满了，没人倒"],
    "其他报修": ["家里东西坏了，需要修一下", "有个东西坏了，说不清楚"],
}
DESCRIPTION_SUFFIXES = ["，麻烦尽快处理", "，谢谢师傅", "，家里有老人，麻烦了", "，孩子还等着上学"]

RESULT_NOTES = {
    "电梯困人": ["已安全救出被困人员，电梯恢复运行", "手动盘车救人，随后更换门锁触点",
             "已救出被困乘客，测试开关门正常", "更换电梯门机皮带，困人故障排除"],
    "电梯故障": ["已更换门机皮带，开关门恢复正常", "调整平层感应器，运行平稳",
             "更换接触器，电梯恢复正常运行", "已清理导轨异物，异响消除"],
    "水管爆裂": ["已关闭阀门并更换破裂管段，清理积水", "更换PPR管接头，打压测试合格",
             "已修复主管道，恢复供水并清理楼道", "更换阀门与管件，压力测试正常"],
    "水管漏水": ["已更换密封圈，漏水问题解决", "重新做防水并更换接头，观察无渗漏",
             "已修补管缝，墙面烘干处理", "更换水槽下水管，试水正常"],
    "下水道堵塞": ["已疏通管道并清理返水，异味消除", "使用疏通机清除油污堵塞，排水通畅",
              "拆检存水弯并清理杂物，已恢复", "高压水枪疏通主管道，排水正常"],
    "电路故障": ["已更换漏电保护器，恢复供电", "紧固配电箱接线，跳闸问题解决",
             "更换烧蚀插座与空开，送电正常", "排查线路短路点并修复，电压正常"],
    "燃气泄漏": ["已关闭燃气总阀并更换密封垫，检漏合格", "更换燃气软管，肥皂水检漏无泄漏",
             "已通风并修复接口，燃气公司复检通过", "更换阀门，报警器复位正常"],
    "消防隐患": ["已清理消防通道杂物并张贴警示", "补齐消防水带与灭火器，完成检查",
             "更换消防应急指示灯，恢复照明", "清理楼道违规充电线，已劝导业主"],
    "门禁故障": ["已更换门禁读卡器，刷卡正常", "调整闭门器与磁力锁，门禁恢复",
             "修复可视对讲线路，画面声音正常", "升级道闸识别系统，车牌识别正常"],
    "公共照明": ["已更换楼道LED灯管，照明恢复", "更换车库灯具备件，全部点亮",
             "检修路灯线路并更换灯泡", "更换应急照明电池，测试通过"],
    "墙面地面破损": ["已铲除空鼓墙皮并重新粉刷", "更换破损地砖，找平处理",
               "修补外墙脱落区域并做防水", "修复墙面裂缝，重新刮腻子刷漆"],
    "环境卫生": ["已完成垃圾清运与桶站冲洗", "清理楼道宠物粪便并消杀",
             "清扫绿化带落叶与垃圾", "完成楼道消杀，异味消除"],
    "其他报修": ["已更换长椅木条并加固", "更换窗户把手，开关顺畅",
             "紧固健身器材螺丝并除锈", "更换晾衣架钢丝，已可正常使用"],
}
UNCLOSED_NOTES = [
    "已联系业主，等待配件到货",
    "已安排返工，待业主在家时间",
    "缺少配件，预计明日处理",
    "业主暂时无法到场，改约时间，工单挂起",
    "现场情况复杂，已升级主管跟进",
]


def trunc_normal(rng: random.Random, mean: float, sd: float, lo: float, hi: float, tries: int = 200) -> float:
    """截断正态采样，用于响应/处理时长的自然浮动。"""
    for _ in range(tries):
        value = rng.gauss(mean, sd)
        if lo <= value <= hi:
            return value
    return min(max(mean, lo), hi)


def weighted_choice(rng: random.Random, weights: dict) -> str:
    keys = list(weights.keys())
    return rng.choices(keys, weights=[weights[k] for k in keys], k=1)[0]


def sample_description(rng: random.Random, repair_type: str, community: str, building: str,
                       unit: str, room: str, floor: int) -> str:
    """生成业主口语化描述；约 7% 使用模糊口语，ground truth 仍可解释。"""
    loc = f"{building}{unit}"
    if rng.random() < 0.30:
        loc = f"{community}{loc}"
    if rng.random() < 0.07:
        return rng.choice(VAGUE_TEMPLATES[repair_type])
    text = rng.choice(TEMPLATES[repair_type]).format(
        loc=loc, room=f"{room}室", floor=floor,
        building=building, community=community,
    )
    if rng.random() < 0.12:
        text += rng.choice(DESCRIPTION_SUFFIXES)
    return text


def build_day_weights(rng: random.Random) -> list:
    """按周内规律 + 月度波动 + 轻微增长趋势 + 随机噪声计算每天的抽样权重。"""
    season = {4: 0.97, 5: 1.00, 6: 1.05, 7: 1.10, 8: 1.08, 9: 1.00, 10: 0.98}
    weights = []
    for i in range(DAYS):
        day = START_DATE + timedelta(days=i)
        base = 1.12 if day.weekday() >= 5 else 1.0
        trend = 1.0 + 0.08 * i / max(DAYS - 1, 1)
        noise = rng.lognormvariate(0.0, 0.22)
        weights.append(base * season[day.month] * trend * noise)
    return weights


def main() -> None:
    rng = random.Random(SEED)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    type_names = list(TYPE_SPECS.keys())
    type_weights = [TYPE_SPECS[t]["weight"] for t in type_names]
    day_weights = build_day_weights(rng)

    # 按天权重做最大余数分配，保证 3000 单在 180 天内分布稳定、周末日均略高于工作日
    total_day_weight = sum(day_weights)
    raw_counts = [N_ORDERS * w / total_day_weight for w in day_weights]
    day_counts = [int(x) for x in raw_counts]
    remainders = sorted(range(DAYS), key=lambda i: raw_counts[i] - day_counts[i], reverse=True)
    for i in remainders[: N_ORDERS - sum(day_counts)]:
        day_counts[i] += 1
    day_bag = [i for i, c in enumerate(day_counts) for _ in range(c)]
    rng.shuffle(day_bag)

    # 工作日 / 周末日内小时权重（工作日 8-11、18-21 双高峰；周末整体更分散、单量略多）
    hour_weekday = [1.2, 0.8, 0.6, 0.5, 0.5, 0.8, 2.0, 4.5, 8.5, 9.5, 8.5, 6.0,
                    4.5, 4.0, 3.8, 3.6, 4.2, 6.5, 9.0, 9.5, 8.0, 5.5, 3.2, 2.0]
    hour_weekend = [2.2, 1.5, 1.0, 0.8, 0.8, 1.2, 2.5, 4.0, 6.5, 8.0, 8.5, 7.5,
                    6.0, 5.5, 5.8, 6.5, 7.5, 8.5, 9.0, 8.5, 7.0, 5.0, 3.5, 2.5]

    rows = []
    for _ in range(N_ORDERS):
        # ---- 时间：天 -> 小时 -> 分秒 ----
        day_idx = day_bag.pop()
        day = START_DATE + timedelta(days=day_idx)
        hours = hour_weekend if day.weekday() >= 5 else hour_weekday
        hour = rng.choices(range(24), weights=hours, k=1)[0]
        created_at = day.replace(hour=hour, minute=rng.randrange(60), second=rng.randrange(60))

        # ---- 报修类型 & 类型规则 ----
        repair_type = rng.choices(type_names, weights=type_weights, k=1)[0]
        spec = TYPE_SPECS[repair_type]
        facility, urgency, sla, trade = spec["facility"], spec["urgency"], spec["sla"], spec["trade"]

        # ---- 地点 ----
        community = weighted_choice(rng, {c: v["weight"] for c, v in COMMUNITIES.items()})
        cmeta = COMMUNITIES[community]
        building = f"{rng.choice(cmeta['buildings'])}栋"
        unit = f"{rng.randint(1, 4)}单元"
        floor = rng.randint(1, cmeta["max_floor"])
        room = f"{floor}{rng.randint(1, 4):02d}"
        description = sample_description(rng, repair_type, community, building, unit, room, floor)

        # ---- 派工：只派给对应工种的维修工 ----
        worker_id = weighted_choice(rng, TYPE_WORKER_WEIGHTS[repair_type])
        worker_name, worker_trade, _zone = WORKERS[worker_id]
        assert worker_trade == trade, (repair_type, worker_id, worker_trade, trade)

        # ---- 派单方式 / 改派 ----
        dispatch_mode = "manual" if rng.random() < 0.06 else "auto"
        p_reassign = 0.55 if dispatch_mode == "manual" else 0.02
        reassigned = 1 if rng.random() < p_reassign else 0

        # ---- 响应时长 ----
        r_mean, r_sd, r_lo, r_hi = URGENCY_RESPONSE[urgency]
        response_minutes = round(trunc_normal(rng, r_mean, r_sd, r_lo, r_hi), 1)

        # ---- 正常处理时长（超时单在第二阶段统一按类型精确分配）----
        d_mean, d_sd, d_lo, d_hi = spec["dur"]
        base_duration = round(trunc_normal(rng, d_mean, d_sd, d_lo, min(d_hi, sla * 0.97)), 1)

        rows.append({
            "created_at": created_at,
            "community": community,
            "building": building,
            "unit": unit,
            "room": room,
            "description": description,
            "repair_type": repair_type,
            "facility_type": facility,
            "urgency": urgency,
            "worker_id": worker_id,
            "worker_name": worker_name,
            "worker_trade": worker_trade,
            "dispatch_mode": dispatch_mode,
            "reassigned": reassigned,
            "response_minutes": response_minutes,
            "sla_minutes": sla,
            "_base_duration": base_duration,
        })

    # ---- 按类型精确分配超时单：紧急处置类目标 5%，其余 12.5% ----
    by_type: dict[str, list[int]] = {}
    for idx, row in enumerate(rows):
        by_type.setdefault(row["repair_type"], []).append(idx)
    for repair_type, idxs in by_type.items():
        sla = TYPE_SPECS[repair_type]["sla"]
        target = TIMEOUT_TARGET.get(repair_type, DEFAULT_TIMEOUT_TARGET)
        n_timeout = max(1, round(len(idxs) * target))
        timeout_idx = set(rng.sample(idxs, n_timeout))
        for idx in idxs:
            row = rows[idx]
            if idx in timeout_idx:
                extra = trunc_normal(rng, sla * 0.10, sla * 0.07, 0.6, sla * 0.80)
                duration = round(sla + extra, 1)
                if duration <= sla:  # 兜底：严格保证 duration > sla
                    duration = round(sla + 0.6, 1)
                row["duration_minutes"] = duration
                row["is_timeout"] = 1
            else:
                duration = row["_base_duration"]
                if duration > sla * 0.97:  # 兜底：绝不误判超时
                    duration = round(sla * 0.97, 1)
                row["duration_minutes"] = duration
                row["is_timeout"] = 0
            del row["_base_duration"]

    # ---- 闭环 / 满意度 / 照片 / 处理记录（依赖超时结果）----
    for row in rows:
        repair_type = row["repair_type"]
        is_timeout = row["is_timeout"]
        reassigned = row["reassigned"]
        dispatch_mode = row["dispatch_mode"]

        p_closed = 0.99
        if is_timeout:
            p_closed *= 0.60
        if reassigned:
            p_closed *= 0.95
        if dispatch_mode == "manual":
            p_closed *= 0.98
        is_closed = 1 if rng.random() < p_closed else 0
        row["is_closed"] = is_closed

        if is_closed:
            sat_mean = 4.58 - 0.40 * is_timeout - 0.12 * reassigned - (0.10 if dispatch_mode == "manual" else 0.0)
            row["satisfaction"] = round(trunc_normal(rng, sat_mean, 0.30, 3.2, 5.0), 1)
            row["photos_count"] = rng.choices([0, 1, 2, 3, 4], weights=[0.12, 0.32, 0.30, 0.18, 0.08], k=1)[0]
            row["result_note"] = rng.choice(RESULT_NOTES[repair_type])
        else:
            row["satisfaction"] = None
            row["photos_count"] = rng.choices([0, 1, 2], weights=[0.55, 0.33, 0.12], k=1)[0]
            row["result_note"] = rng.choice(UNCLOSED_NOTES) if rng.random() < 0.35 else ""

        row["source"] = weighted_choice(rng, SOURCES)

    # ---- 按时间排序后生成工单号（同一天内顺序编号）----
    rows.sort(key=lambda r: r["created_at"])
    day_seq: Counter = Counter()
    for row in rows:
        day_key = row["created_at"].strftime("%Y%m%d")
        day_seq[day_key] += 1
        row["order_id"] = f"WO{day_key}{day_seq[day_key]:04d}"

    # ---- 写 CSV ----
    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["created_at"] = row["created_at"].strftime("%Y-%m-%d %H:%M:%S")
            out["response_minutes"] = f"{row['response_minutes']:.1f}"
            out["duration_minutes"] = f"{row['duration_minutes']:.1f}"
            out["satisfaction"] = "" if row["satisfaction"] is None else f"{row['satisfaction']:.1f}"
            writer.writerow(out)

    # ---- 摘要统计 ----
    total = len(rows)
    type_counts = Counter(r["repair_type"] for r in rows)
    urgency_counts = Counter(r["urgency"] for r in rows)
    closed = sum(r["is_closed"] for r in rows)
    timeout = sum(r["is_timeout"] for r in rows)
    avg_duration = sum(r["duration_minutes"] for r in rows) / total
    avg_response = sum(r["response_minutes"] for r in rows) / total
    sats = [r["satisfaction"] for r in rows if r["satisfaction"] is not None]
    avg_sat = sum(sats) / len(sats)
    auto_n = sum(1 for r in rows if r["dispatch_mode"] == "auto")
    manual_n = total - auto_n
    reassigned_n = sum(r["reassigned"] for r in rows)
    weekend_n = sum(1 for r in rows if r["created_at"].weekday() >= 5)

    meta = {
        "seed": SEED,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rows": total,
        "start_date": START_DATE.strftime("%Y-%m-%d"),
        "end_date": END_DATE.strftime("%Y-%m-%d"),
        "days": DAYS,
        "columns": COLUMNS,
        "type_rules": {
            t: {"facility": s["facility"], "urgency": s["urgency"], "sla_minutes": s["sla"], "trade": s["trade"]}
            for t, s in TYPE_SPECS.items()
        },
        "type_distribution": {t: type_counts[t] for t in type_names},
        "urgency_distribution": {u: urgency_counts[u] for u in ["紧急", "高", "中", "低"]},
        "closed": closed,
        "closed_rate": round(closed / total * 100, 1),
        "timeout": timeout,
        "timeout_rate": round(timeout / total * 100, 1),
        "avg_duration": round(avg_duration, 1),
        "avg_response": round(avg_response, 1),
        "avg_satisfaction": round(avg_sat, 2),
        "auto": auto_n,
        "auto_rate": round(auto_n / total * 100, 1),
        "manual_rate": round(manual_n / total * 100, 1),
        "reassigned": reassigned_n,
        "reassigned_rate": round(reassigned_n / total * 100, 1),
        "weekend_orders": weekend_n,
        "worker_master": {wid: {"name": n, "trade": t, "zone": z} for wid, (n, t, z) in WORKERS.items()},
    }
    META_PATH.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")

    # ---- 控制台摘要 ----
    print("=" * 62)
    print("历史工单数据生成完成")
    print(f"输出文件 : {CSV_PATH.relative_to(BASE_DIR)}")
    print(f"记录数量 : {total} 条（{START_DATE:%Y-%m-%d} ~ {END_DATE:%Y-%m-%d}，{DAYS} 天）")
    print(f"闭环率   : {meta['closed_rate']}%（{closed} 单）   超时率: {meta['timeout_rate']}%（{timeout} 单）")
    print(f"平均处理 : {meta['avg_duration']} 分钟   平均响应: {meta['avg_response']} 分钟   满意度: {meta['avg_satisfaction']}")
    print(f"派单方式 : 自动 {meta['auto_rate']}% / 人工 {meta['manual_rate']}%   改派率: {meta['reassigned_rate']}%")
    print(f"周末单量 : {weekend_n} 单（{weekend_n / total * 100:.1f}%）")
    print("-" * 62)
    print("报修类型分布：")
    for t in sorted(type_names, key=lambda x: -type_counts[x]):
        print(f"  {t:<6} {type_counts[t]:>4} 单  {type_counts[t] / total * 100:>5.1f}%  "
              f"SLA {TYPE_SPECS[t]['sla']:>3} 分钟  {TYPE_SPECS[t]['trade']}")
    print("=" * 62)
    print(f"元数据已写入 {META_PATH.relative_to(BASE_DIR)}")


if __name__ == "__main__":
    main()
