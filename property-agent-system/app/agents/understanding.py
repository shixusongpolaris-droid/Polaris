"""Agent 1：报修理解 Agent。

规则 + 关键词加权分类器：识别报修类型、设施、位置、紧急程度与关键实体。
所有命中规则和置信度都会写入决策日志，保证可追溯。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..config import TYPE_RULES, URGENCY_ORDER, type_meta
from .base import AgentDecision, BaseAgent

CN_NUM = {"一": "1", "二": "2", "两": "2", "三": "3", "四": "4", "五": "5", "六": "6", "七": "7", "八": "8", "九": "9", "十": "10"}

# 每条规则: (正则, 权重, 规则说明)。权重叠加后取最高分类。
TYPE_PATTERNS: Dict[str, List[Tuple[str, float, str]]] = {
    "电梯困人": [
        (r"(有人被困|困在电梯|被困电梯|电梯困人|被困在电梯)", 9, "明确出现人员被困"),
        (r"(电梯|货梯|客梯|扶梯|升降机).{0,12}(关不上|打不开|卡住|停在|坠落|停运)", 3, "电梯门/轿厢异常"),
        (r"(老人|小孩|孩子|孕妇|婴儿|病人).{0,8}(被困|电梯)", 6, "弱势人员涉险"),
    ],
    "电梯故障": [
        (r"(电梯|货梯|客梯|扶梯|升降机)", 4, "提及电梯设备"),
        (r"(异响|抖动|按钮|不平层|门关|停运|故障|不动|显示异常|开合|卡住|关不上|打不开|很慢)", 2, "电梯运行异常"),
    ],
    "燃气泄漏": [
        (r"(燃气|煤气|天然气).{0,10}(泄漏|漏气|味道|味|报警|臭)", 7, "燃气泄漏特征"),
        (r"(煤气味|燃气味|漏气|天然气味)", 7, "明显燃气气味"),
        (r"燃气|煤气|天然气", 2, "提及燃气设施"),
    ],
    "消防隐患": [
        (r"(着火|起火|冒烟|火警|明火)", 8, "出现火情"),
        (r"(消防指示灯|应急灯|疏散指示).{0,8}(不亮|坏|缺失|故障|损坏)", 6, "消防指示/应急照明异常"),
        (r"(防火门|防火卷帘|防火阀|防火用具|防火器材|防火设施|消防水带|消防水枪|消防斧|消防器材|灭火器材|消防设施|消防箱)", 5, "消防设施/器材异常"),
        (r"(消防|灭火器|灭火器材|烟感|温感|喷淋|疏散|消防栓|消防通道|防火门|防火卷帘|防火用具|消防水带|应急灯|疏散指示|安全出口).{0,12}(坏|损坏|破损|缺失|丢失|遗失|不见|被盗|堵|过期|占用|遮挡|失效|压力不足|不亮|锈蚀|漏水|关不上|打不开|一直响|误报|报警)", 5, "消防设施缺失/失效"),
        (r"消防|灭火器|防火|烟感|温感|喷淋|疏散|消防栓|消防通道|消防水带|应急灯|疏散指示|安全出口", 3, "提及消防设施"),
    ],
    "水管爆裂": [
        (r"(爆管|爆裂|爆了|喷水|水柱|水管爆)", 7, "爆管/喷水"),
        (r"(水管|自来水管).{0,10}(裂|爆|喷)", 5, "水管破裂"),
        (r"(大量积水|积水严重|水漫|楼道积水|满地是水)", 4, "大量积水"),
        (r"水管", 1.5, "提及水管"),
    ],
    "下水道堵塞": [
        (r"(下水道|地漏|马桶|污水|排污|反水|返水|化粪池|排水管)", 3, "排水设施"),
        (r"(地漏|下水道|马桶|污水|排污|反水|返水).{0,10}(不下水|积水|漫|堵|溢|不通|排不出去)", 4, "排水不畅/积水"),
        (r"(堵|溢|反|冒|不通|漫|塞|排不出去|不下水)", 3, "堵塞/反水"),
        (r"(味道很大|异味|臭)", 1, "伴随异味"),
    ],
    "水管漏水": [
        (r"(漏水|渗水|滴水|冒水|关不住)", 4, "漏水/渗水"),
        (r"(水管|水龙头|角阀|阀门|马桶|水箱|卫生间|厨房|阳台).{0,10}(漏|渗|滴|坏|松动)", 4, "涉水部件异常"),
        (r"漏水|渗水", 2, "提及漏水"),
    ],
    "电路故障": [
        (r"(跳闸|停电|没电|断电|漏电|短路|电表|配电箱|总闸)", 5, "供电异常"),
        (r"(电线|插座|开关|线路|电灯|配电).{0,10}(坏|烧|跳|断|故障|冒烟|火花|裸露)", 4, "线路/设备故障"),
        (r"电", 0.8, "提及用电"),
    ],
    "公共照明": [
        (r"(楼道|走廊|车库|大堂|路灯|公共区域|电梯厅).{0,8}(灯|照明)", 4, "公共区域照明"),
        (r"(灯不亮|灯坏了|灯泡|灯管|照明|漆黑)", 3, "灯具异常"),
    ],
    "门禁故障": [
        (r"(门禁|门锁|单元门|电子锁|道闸|门卡|密码锁|可视对讲|门铃)", 3, "门禁设备"),
        (r"(坏|失灵|打不开|关不上|刷不开|报警|无法识别)", 2, "门禁异常"),
    ],
    "墙面地面破损": [
        (r"(墙皮|墙面|地砖|瓷砖|地面|天花板|吊顶|裂缝|空鼓)", 3, "土建部位"),
        (r"(脱落|破损|裂|掉|鼓|塌|渗水起皮)", 2, "破损特征"),
    ],
    "环境卫生": [
        (r"(垃圾|卫生|异味|臭味|脏|清扫|保洁|蚊虫|杂物堆放|宠物粪便)", 4, "环境卫生问题"),
        (r"(楼道|电梯|车库|绿化带).{0,8}(垃圾|异味|脏|杂物)", 2, "公共区域卫生"),
    ],
}

TYPE_ORDER = list(TYPE_RULES.keys())

# 防止“坏了”这类泛化词跨类别误命中：每个类型必须出现对应上下文词之一
CONTEXT_WORDS = {
    "电梯困人": ["电梯", "货梯", "客梯", "扶梯", "升降机", "被困"],
    "电梯故障": ["电梯", "货梯", "客梯", "扶梯", "升降机"],
    "燃气泄漏": ["燃气", "煤气", "天然气"],
    "消防隐患": ["消防", "灭火", "防火", "烟感", "温感", "喷淋", "疏散", "火", "冒烟", "着火", "火警", "应急灯", "安全出口"],
    "水管爆裂": ["水", "管", "阀", "爆", "喷"],
    "下水道堵塞": ["下水道", "地漏", "马桶", "污水", "排污", "反水", "返水", "排水", "化粪池"],
    "水管漏水": ["水", "管", "龙头", "阀", "漏", "渗"],
    "电路故障": ["电", "闸", "线", "插座", "开关", "配电"],
    "公共照明": ["灯", "照明"],
    "门禁故障": ["门", "锁", "禁", "道闸", "对讲"],
    "墙面地面破损": ["墙", "砖", "地面", "天花板", "吊顶", "裂缝", "空鼓", "土建"],
    "环境卫生": ["垃圾", "卫生", "异味", "臭味", "脏", "清扫", "保洁", "蚊虫", "杂物", "粪便"],
    "其他报修": [],
}
DETAIL_KEYWORDS = ["楼道", "大堂", "地下车库", "地下室", "车库", "电梯厅", "天台", "屋顶", "绿化带", "门岗", "商铺", "卫生间", "厨房", "阳台", "卧室", "客厅", "公共区域", "小区门口"]
TRAP_WORDS = ["被困", "困在", "困人", "有人被困", "里面有人", "出不来", "困住"]
VULNERABLE_WORDS = ["老人", "小孩", "孩子", "孕妇", "婴儿", "病人", "残疾人"]
SEVERE_WORDS = ["大面积", "整栋", "整单元", "整层", "全楼", "停水", "停电", "水漫", "爆裂", "大量"]
MILD_WORDS = ["轻微", "一点点", "不着急", "有空", "小问题", "不太严重"]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _cn_to_int(value: str) -> str:
    if value.isdigit():
        return str(int(value))
    if value in CN_NUM:
        return CN_NUM[value]
    if len(value) == 2 and value[0] == "十":
        return str(10 + int(CN_NUM.get(value[1], 0)))
    if len(value) == 2 and value[1] == "十":
        return str(int(CN_NUM.get(value[0], 0)) * 10)
    return value


def _extract_location(text: str, hints: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    hints = hints or {}
    building = (hints.get("building") or "").strip()
    unit = (hints.get("unit") or "").strip()
    room = (hints.get("room") or "").strip()
    if not building:
        m = re.search(r"([0-9]{1,2}|[一二三四五六七八九十两]{1,3})\s*(?:栋|号楼|幢|座)", text)
        if m:
            building = _cn_to_int(m.group(1)) + "栋"
    if not building:
        # 兼容“27号203”这类老小区门牌写法：27号 → 27号楼，203 → 203室
        m = re.search(r"([0-9]{1,3})\s*号(?!柜|门|线|院|院)(?:\s*([0-9]{3,4})\s*(?:室|房)?)?", text)
        if m:
            building = str(int(m.group(1))) + "号楼"
            if m.group(2) and not room:
                room = str(int(m.group(2))) + "室"
    if not unit:
        m = re.search(r"([0-9]{1,2}|[一二三四五六七八九十]{1,2})\s*单元", text)
        if m:
            unit = _cn_to_int(m.group(1)) + "单元"
    if not room:
        m = re.search(r"([0-9]{3,4})\s*(?:室|号房|房)", text)
        if m and not re.match(r"^(19|20)\d{2}$", m.group(1)):
            room = m.group(1) + "室"
    detail = ""
    for word in DETAIL_KEYWORDS:
        if word in text:
            detail = word
            break
    if not building and not unit and not room and not detail:
        m = re.search(r"([A-Za-z])\s*(?:栋|座)", text)
        if m:
            building = m.group(1).upper() + "栋"
    parts = [p for p in [building, unit, room] if p]
    location_text = "".join(parts) if parts else detail
    if detail and detail not in location_text:
        location_text = (location_text + " " + detail).strip()
    return {
        "building": building,
        "unit": unit,
        "room": room,
        "detail": detail,
        "text": location_text or "位置待确认",
    }


def _classify(text: str) -> Tuple[str, float, List[str]]:
    scores = {t: 0.0 for t in TYPE_ORDER}
    reasons: List[str] = []
    for repair_type, patterns in TYPE_PATTERNS.items():
        context = CONTEXT_WORDS.get(repair_type, [])
        if context and not any(word in text for word in context):
            continue
        for pattern, weight, reason in patterns:
            if re.search(pattern, text):
                scores[repair_type] += weight
                reasons.append(f"{repair_type} ← {reason}(+{weight:g})")
    ranked = sorted(scores.items(), key=lambda kv: (kv[1], -TYPE_ORDER.index(kv[0])), reverse=True)
    top_type, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_score <= 0:
        return "其他报修", 0.0, ["未命中具体设施关键词，归入其他报修"]
    margin = top_score - second_score
    selected_reasons = [r for r in reasons if r.startswith(top_type + " ←")]
    if margin <= 1:
        selected_reasons.append(f"与次优类型分差较小({margin:g})，已按关键词优先级判定")
    return top_type, top_score, selected_reasons


def _detect_entities(text: str) -> Dict[str, Any]:
    return {
        "people_trapped": any(w in text for w in TRAP_WORDS),
        "water_accumulation": any(w in text for w in ["积水", "水漫", "满地是水", "喷水", "水柱"]),
        "power_outage": any(w in text for w in ["停电", "没电", "断电", "跳闸"]),
        "gas_smell": any(w in text for w in ["燃气味", "煤气味", "漏气", "燃气泄漏", "煤气泄漏"]),
        "fire_risk": any(w in text for w in ["着火", "起火", "冒烟", "火警", "明火"]),
        "vulnerable_people": [w for w in VULNERABLE_WORDS if w in text],
        "severe_scope": [w for w in SEVERE_WORDS if w in text],
        "mild_tone": [w for w in MILD_WORDS if w in text],
    }


def _score_urgency(repair_type: str, entities: Dict[str, Any], base_urgency: str) -> Tuple[str, float, List[str]]:
    urgency = base_urgency
    score = {"紧急": 0.96, "高": 0.8, "中": 0.58, "低": 0.32}.get(base_urgency, 0.5)
    reasons = [f"报修类型基线：{base_urgency}"]
    if repair_type in ("燃气泄漏", "消防隐患", "电梯困人"):
        urgency, score = "紧急", max(score, 0.98)
        reasons.append("安全类事件，强制最高优先级")
    if entities["people_trapped"]:
        urgency, score = "紧急", max(score, 0.99)
        reasons.append("检测到人员被困，触发紧急升级")
    if entities["gas_smell"] or entities["fire_risk"]:
        urgency, score = "紧急", max(score, 0.98)
        reasons.append("检测到燃气/火情风险")
    if entities["severe_scope"] and URGENCY_ORDER.get(urgency, 0) < URGENCY_ORDER["高"]:
        urgency = "高"
        score = max(score, 0.82)
        reasons.append("影响范围较大，提升至高风险")
    if entities["vulnerable_people"] and URGENCY_ORDER.get(urgency, 0) < URGENCY_ORDER["高"]:
        urgency = "高"
        score = max(score, 0.8)
        reasons.append("涉及老人/儿童等弱势人群")
    if entities["mild_tone"] and urgency in ("高", "中"):
        urgency = "中" if urgency == "高" else "低"
        score = max(0.3, score - 0.15)
        reasons.append("业主语气轻微，适当下调优先级")
    return urgency, round(score, 2), reasons


class UnderstandingAgent(BaseAgent):
    code = "understanding"
    name = "报修理解 Agent"
    action = "analyze_repair_request"

    def analyze(self, description: str, hints: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = _normalize(description)
        if len(text) < 4:
            text = text or "未填写报修描述"
        repair_type, raw_score, rules = _classify(text)
        if repair_type == "其他报修":
            # 尝试用位置信息辅助兜底
            if any(w in text for w in ["漏水", "渗水", "水管", "水龙头", "角阀"]):
                repair_type = "水管漏水"
                rules.append("兜底规则：含漏水/水管线索但无明确设施，按水管漏水处理")
            elif "电" in text:
                repair_type = "电路故障"
                rules.append("兜底规则：含“电”且无明确设施，按电路故障处理")
        facility_type, base_urgency, sla_minutes, trades = type_meta(repair_type)
        entities = _detect_entities(text)
        urgency, urgency_score, urgency_reasons = _score_urgency(repair_type, entities, base_urgency)
        location = _extract_location(text, hints)

        # 置信度：关键词强度 + 与次优类别的间隔 + 位置/紧急信号
        confidence = 0.55 + min(0.3, raw_score * 0.045)
        if repair_type == "其他报修":
            confidence = 0.52
        if location["building"]:
            confidence += 0.04
        if urgency in ("紧急", "高") and (entities["people_trapped"] or entities["severe_scope"]):
            confidence += 0.03
        confidence = round(max(0.45, min(0.98, confidence)), 2)

        output = {
            "repair_type": repair_type,
            "facility_type": facility_type,
            "urgency": urgency,
            "urgency_score": urgency_score,
            "sla_minutes": sla_minutes,
            "required_trades": trades,
            "location": location,
            "entities": entities,
            "keywords": [w for w in ["爆裂", "漏水", "积水", "被困", "跳闸", "堵", "燃气", "冒烟", "灯", "门锁", "墙皮", "垃圾"] if w in text],
            "matched_rules": rules + urgency_reasons,
            "summary": f"{location['text']} · {repair_type} · {urgency}",
            "confidence": confidence,
        }
        return output

    def run(self, description: str, hints: Optional[Dict[str, Any]] = None) -> AgentDecision:
        output = self.analyze(description, hints)
        rationale = (
            f"命中 {len(output['matched_rules'])} 条规则，判定为「{output['repair_type']}」；"
            f"位置「{output['location']['text']}」；紧急程度「{output['urgency']}」"
            f"（{output['urgency_score']:.2f}）。依据：" + "；".join(output["matched_rules"][:4])
        )
        return self.make_decision(
            output=output,
            rationale=rationale,
            confidence=output.get("confidence", 0.8),
            input_summary=description[:120],
        )
