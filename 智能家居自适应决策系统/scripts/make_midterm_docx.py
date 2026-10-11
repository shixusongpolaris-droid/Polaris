# -*- coding: utf-8 -*-
"""生成中期报告（五个部分）的 Word 文档。"""

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


DOC_PATH = (
    r"D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    r"\docs\中期报告_填写材料.docx"
)

FONT_CN = "宋体"
FONT_CN_HEAD = "黑体"
FONT_EN = "Times New Roman"

# A4 页面：21.0 × 29.7 cm
PAGE_W_CM = 21.0
PAGE_H_CM = 29.7
MARGIN_LR_CM = 2.0          # 左页边距（右侧单独设更宽）
USABLE_CM = PAGE_W_CM - MARGIN_LR_CM - 2.5   # 可用宽度 = 16.5 cm


def set_run_font(run, cn=FONT_CN, size=10.5, bold=False, color=None):
    """设置中英文字体（中文必须显式指定 w:eastAsia，否则可能回退）。"""
    run.font.name = FONT_EN
    run.font.size = Pt(size)
    run.font.bold = bold
    if color is not None:
        run.font.color.rgb = color
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = rpr.makeelement(qn("w:rFonts"), {})
        rpr.append(rfonts)
    rfonts.set(qn("w:ascii"), FONT_EN)
    rfonts.set(qn("w:hAnsi"), FONT_EN)
    rfonts.set(qn("w:eastAsia"), cn)


def para(doc, text="", size=10.5, bold=False, cn=FONT_CN,
         align=None, space_after=6, indent=False, color=None):
    p = doc.add_paragraph()
    if align is not None:
        p.alignment = align
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.4
    p.paragraph_format.left_indent = Cm(0)
    p.paragraph_format.right_indent = Cm(0)
    if indent:
        p.paragraph_format.first_line_indent = Pt(21)
    if text:
        r = p.add_run(text)
        set_run_font(r, cn=cn, size=size, bold=bold, color=color)
    return p


def heading(doc, text, level=1):
    sizes = {1: 13, 2: 11.5, 3: 11}
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(6)
    p.paragraph_format.line_spacing = 1.3
    r = p.add_run(text)
    set_run_font(r, cn=FONT_CN_HEAD, size=sizes.get(level, 11), bold=True)
    return p


def make_table(doc, headers, rows, widths=None):
    """创建表格。

    widths 按比例给出（会自动缩放到可用宽度），
    并设置固定布局 (tblLayout=fixed) 与显式 tblW，
    避免 LibreOffice / Word 因自动布局把表格撑出页面。
    """
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.alignment = WD_TABLE_ALIGNMENT.CENTER
    t.autofit = False

    tbl = t._tbl
    tblPr = tbl.tblPr

    # 固定布局
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)

    # 表格总宽 = 可用宽度
    tblW = tblPr.find(qn("w:tblW"))
    if tblW is None:
        tblW = OxmlElement("w:tblW")
        tblPr.append(tblW)
    tblW.set(qn("w:w"), str(int(USABLE_CM * 0.85 * 567)))
    tblW.set(qn("w:type"), "dxa")

    # 列宽按比例分配
    if widths:
        total = sum(widths)
        col_cm = [w / total * USABLE_CM * 0.85 for w in widths]
    else:
        col_cm = [USABLE_CM * 0.85 / len(headers)] * len(headers)

    # 写入 tblGrid（决定实际列宽）
    grid = tbl.find(qn("w:tblGrid"))
    if grid is not None:
        tbl.remove(grid)
    grid = OxmlElement("w:tblGrid")
    for w in col_cm:
        gc = OxmlElement("w:gridCol")
        gc.set(qn("w:w"), str(int(w * 567)))
        grid.append(gc)
    tbl.insert(list(tbl).index(tblPr) + 1, grid)

    # 给表格设置单元格内边距（右侧留白更宽），
    # 避免文字紧贴边框；在部分渲染器出现画布裁切时也不易丢失文字。
    tblCellMar = OxmlElement("w:tblCellMar")
    for side, val in (("top", 40), ("left", 80), ("bottom", 40),
                      ("right", 140)):
        e = OxmlElement(f"w:{side}")
        e.set(qn("w:w"), str(val))
        e.set(qn("w:type"), "dxa")
        tblCellMar.append(e)
    tblPr.append(tblCellMar)

    hdr = t.rows[0].cells
    for i, h in enumerate(headers):
        hdr[i].text = ""
        p = hdr[i].paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
        r = p.add_run(h)
        set_run_font(r, cn=FONT_CN_HEAD, size=9.5, bold=True)
    for row in rows:
        cells = t.add_row().cells
        for i, val in enumerate(row):
            cells[i].text = ""
            p = cells[i].paragraphs[0]
            p.paragraph_format.line_spacing = 1.15
            r = p.add_run(str(val))
            set_run_font(r, size=9.5)

    # 必须显式写入每个单元格的 tcW（单元格宽度优先于 tblGrid）。
    # 教训：python-docx 创建单元格时会写入一个默认/先前的 tcW；
    # 若只改 tblGrid 而不改 tcW，渲染器（实测 LibreOffice）会按 tcW
    # 把表格撑宽（本次出现表格宽 20.8cm > 页面可用 16.5cm，右侧被裁）。
    # 因此这里同时写 tblGrid、tblW 与每个 tcW，三者保持一致。
    def set_tcW(cell, cm):
        tcPr = cell._tc.get_or_add_tcPr()
        for old in tcPr.findall(qn("w:tcW")):
            tcPr.remove(old)
        e = OxmlElement("w:tcW")
        e.set(qn("w:w"), str(int(cm * 567)))
        e.set(qn("w:type"), "dxa")
        tcPr.append(e)

    for i, cell in enumerate(t.rows[0].cells):
        set_tcW(cell, col_cm[i])
    for row in t.rows[1:]:
        for i, cell in enumerate(row.cells):
            set_tcW(cell, col_cm[i])

    return t


def main():
    doc = Document()

    # 页面设置：A4
    sec = doc.sections[0]
    sec.page_width = Cm(PAGE_W_CM)
    sec.page_height = Cm(PAGE_H_CM)
    sec.left_margin = Cm(MARGIN_LR_CM)
    sec.right_margin = Cm(2.5)
    sec.top_margin = Cm(2.5)
    sec.bottom_margin = Cm(2.5)

    # 默认样式
    style = doc.styles["Normal"]
    style.font.name = FONT_EN
    style.font.size = Pt(10.5)
    style.element.rPr.rFonts.set(qn("w:eastAsia"), FONT_CN)

    # ---------- 标题 ----------
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run("基于强化学习与多目标优化的")
    set_run_font(r, cn=FONT_CN_HEAD, size=14, bold=True)
    p2 = doc.add_paragraph()
    p2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p2.paragraph_format.space_after = Pt(4)
    r2 = p2.add_run("智能家居自适应决策系统")
    set_run_font(r2, cn=FONT_CN_HEAD, size=14, bold=True)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_after = Pt(18)
    r = p.add_run("中 期 报 告")
    set_run_font(r, cn=FONT_CN_HEAD, size=12, bold=True)

    # ============================================================
    # 一、项目进展情况小结
    # ============================================================
    heading(doc, "一、项目进展情况小结", 1)

    para(doc,
         "本项目按“环境感知 → 数据建模（MDP）→ 智能决策（强化学习）→ "
         "系统验证”的技术路线推进。目前软件部分已全部完成并超额推进，"
         "硬件部分完成选型采购、等待到货。", indent=True)

    heading(doc, "（一）已完成工作", 2)
    make_table(
        doc,
        ["类别", "完成内容"],
        [
            ["问题建模",
             "将家庭环境控制形式化为马尔可夫决策过程（MDP）：\n"
             "状态 25 维、动作 36 个，奖励含舒适度／能耗／切换惩罚；\n"
             "并完成马尔可夫性检验"],
            ["仿真平台",
             "自建仿真平台（热力学、湿度、光照、人员占用四类模型）；\n"
             "含约束机制（最小切换间隔、无人自动关机）与统一评估口径"],
            ["算法实现",
             "PPO 强化学习控制策略；\n"
             "另实现循环网络（RecurrentPPO）与显式储能（电池）建模"],
            ["实验体系",
             "完成 8 组系统实验：主实验、等舒适度比较、分时电价、"
             "消融（多种子）、\n参数敏感性、循环网络、储能、电价变化适应"],
            ["对比基线",
             "构建 5 类对照策略：恒温运行、全关、定温规则、"
             "分时电价规则、规则＋储能"],
            ["硬件准备",
             "完成器件选型与采购；\n"
             "验证固件与采集固件已编写并编译通过（含 NTP 对时）"],
            ["数据链路",
             "完成采集脚本、标定脚本（反推热工参数）、合成数据生成器；\n"
             "标定链路经验证，参数反推误差 0.2%／0.4%"],
            ["文档", "4 份文档共约 1300 行：MDP 定义、实验结果报告、"
                    "实测数据与模型验证、状态总结"],
            ["工程规模", "30 个脚本、41 张图表、环境自检 6 类检查全部通过"],
        ],
        widths=[1.7, 8.3],      # 已完成工作
    )

    para(doc, "", space_after=4)
    heading(doc, "（二）关键阶段性成果（量化）", 2)
    para(doc, "1. 相对规则控制的正向结果", bold=True, space_after=4)
    make_table(
        doc,
        ["结论", "实测数据"],
        [
            ["舒适度不达标率显著更低", "21.57% → 11.73%（降低 45.6%）"],
            ["设备切换次数显著更少", "24.6 → 7.4 次/天（降低 70.0%，几乎无代价）"],
            ["温度控制精度显著更高", "RMSE 0.735 → 0.055（降低 92.5%）"],
            ["相对恒温运行节能", "16.1%（舒适度基本持平）"],
            ["循环网络（LSTM）降低电费", "16.1%"],
            ["显式储能降低电费", "28%～35%（谷段用电占比提升至 95% 以上）"],
            ["工作区间更宽", "规则最低不达标率 24%～29%，本方法可达 8%～19%"],
            ["首次量化规则的隐含适应成本",
             "电价变化后规则不重新调参，电费上升 49.8%"],
        ],
        widths=[6.0, 9.0],       # 关键成果
    )

    para(doc, "", space_after=4)
    para(doc, "2. 待改进方面（已如实记录并完成归因）", bold=True, space_after=4)
    para(doc,
         "在电费这一优化目标上，经过 7 条技术路线（场景化权重、前视电价、"
         "循环网络、增加训练步数、显式储能、奖励权重重标定、电价变化适应）的"
         "系统检验，本方法始终未能超过人工规则，电费差距为 20%～132%。",
         indent=True)
    para(doc,
         "根本原因已定位：本方法的不达标率约 11%（规则约 32%～37%），"
         "但为此付出 48%～71% 的额外能耗。即强化学习学到的是“高能耗的温度"
         "控制器”，而人工规则是“低能耗的成本最小化器”。这界定了方法的适用"
         "边界——本方法定位为“舒适度优先”的方法，而非“电费优先”的方法。",
         indent=True)

    # ============================================================
    # 二、项目存在的问题及解决方案
    # ============================================================
    doc.add_page_break()
    heading(doc, "二、项目存在的问题及解决方案", 1)

    heading(doc, "问题 1：硬件采购到货延迟，实物验证环节滞后", 2)
    para(doc, "【问题描述】", bold=True, space_after=2)
    para(doc,
         "项目所需的 ESP32-S3 开发板与 5 类传感器因物流原因尚未到货，"
         "导致“实物系统验证”与“实测数据采集”两个环节无法按期开展。",
         indent=True)
    para(doc, "【已采取的解决方案】", bold=True, space_after=2)
    for txt in [
        "提前完成固件开发：在无硬件条件下完成两份固件编写并编译通过"
        "（硬件验活程序、长时间采集程序），使硬件到货后可立即烧录，"
        "缩短现场调试时间；",
        "提前打通数据链路：开发合成数据生成器，在无真实数据条件下验证了"
        "“采集 → 标定 → 参数”整条链路，标定精度达 0.2%/0.4%；",
        "调整硬件定位：因家庭中央空调不具备程序控制接口（无红外、无通讯），"
        "将硬件端定位由“闭环控制实验”调整为“感知链路验证 + 真实作息与"
        "热工参数采集”，控制策略的验证在仿真环境中完成；",
        "制定到货即用的操作清单：编写接线表、烧录参数、采集流程"
        "（人工投入约 40 分钟），降低到货后的时间成本。",
    ]:
        para(doc, "· " + txt, indent=True, space_after=3)
    para(doc, "【后续措施】", bold=True, space_after=2)
    para(doc, "持续跟踪物流；到货后立即执行验活 → 采集 3 天 → 参数标定流程。",
         indent=True)

    heading(doc, "问题 2：强化学习在电费维度上未能超过人工规则", 2)
    para(doc, "【问题描述】", bold=True, space_after=2)
    para(doc,
         "项目原定目标之一是“利用强化学习实现自适应控制并优于规则控制”。"
         "实验表明，在分时电价场景下人工规则的电费显著更低"
         "（低 20%～132%）。这是对项目预期的重大偏离。",
         indent=True)
    para(doc, "【已采取的解决方案】", bold=True, space_after=2)
    for txt in [
        "系统排查而非规避：依次检验了 7 条可能的技术路线，全部给出量化结论"
        "与失败归因，而非选择性报告；",
        "严格化比较口径：发现并修正了早期比较中的口径缺陷（不同舒适度水平"
        "直接比较电费），构建了“等舒适度比较”与“帕累托前沿分析”两套更严格的"
        "评判方法，确保结论可靠；",
        "定位根本原因：量化证实本方法的能耗比规则高 48%～71%，这是电费劣势的"
        "直接来源，且该劣势无法通过调参、换网络、加储能消除；",
        "修正项目定位：将目标由“全面超越规则”调整为“多维度性能权衡与能力边界"
        "刻画”——本方法的价值在于舒适度、控制平稳性、工作区间宽度，"
        "以及无需人工设计规则。",
    ]:
        para(doc, "· " + txt, indent=True, space_after=3)
    para(doc, "【取得的认识】", bold=True, space_after=2)
    para(doc,
         "该结果本身具有方法学价值，它界定了强化学习的适用边界——在"
         "“电价结构已知且固定、最优策略可被简单规则刻画”的场景下，人工规则"
         "难以被超越；而在“评价指标难以写成明确规则”的场景下（如兼顾舒适度"
         "与控制平稳性），学习方法有明确优势。",
         indent=True)

    heading(doc, "问题 3：文档数据存在版本不一致风险", 2)
    para(doc, "【问题描述】", bold=True, space_after=2)
    para(doc,
         "项目经历多个版本迭代（v1～v5），在自查中发现一处事实性错误："
         "文档中“PPO 在能耗上也优于规则控制”的表述源自早期版本数据，"
         "而后续版本中该结论已反转（PPO 能耗高 11.8%），文档未同步更新。",
         indent=True)
    para(doc, "【解决方案】", bold=True, space_after=2)
    for txt in [
        "立即修正：核实原始评估数据后，修正了 3 份文档中的相关表述；",
        "建立自动核对机制：新增数据一致性核对脚本，覆盖全部实验的关键数字与"
        "派生指标，可随时复核；",
        "核对结果：当前全部实验数字核对通过，0 处不符。",
    ]:
        para(doc, "· " + txt, indent=True, space_after=3)

    # ============================================================
    # 三、项目今后计划
    # ============================================================
    doc.add_page_break()
    heading(doc, "三、项目今后计划", 1)
    make_table(
        doc,
        ["阶段", "任务", "预计完成"],
        [
            ["近期",
             "硬件到货后烧录验活固件，确认感知链路（I2C 扫描、传感器读数）",
             "硬件到货后 1 天内"],
            ["",
             "挂机采集 3 天真实数据（温湿度/光照/人员占用），"
             "同步人工记录空调使用时段",
             "到货后 1 周内"],
            ["",
             "用实测数据标定仿真模型参数（反推围护结构传热系数 UA 与热容 C），"
             "替换现有估计值",
             "采集完成后 2 天内"],
            ["中期",
             "将真实作息分布写入仿真配置，替换内置的“典型上班族”假设",
             "10 天内"],
            ["",
             "用标定后的模型重新评估全部策略，输出最终实验结论",
             "15 天内"],
            ["后期",
             "整理全部实验成果，撰写结题报告",
             "结题前 2 周"],
            ["",
             "准备答辩材料（含问题定位演进图、实验结论速览、MDP 建模框图）",
             "结题前 1 周"],
            ["可选扩展",
             "扩展至多房间/多设备耦合场景，检验本方法在高维耦合决策下的表现",
             "视进度安排"],
        ],
        widths=[2.0, 10.0, 3.0],   # 今后计划
    )
    para(doc, "", space_after=4)
    para(doc,
         "说明：核心实验已完成，后续重点是实测数据接入与模型标定，"
         "以及成果整理与结题材料撰写。软件部分不再新增大规模实验，"
         "以保证按期结题。", indent=True)

    # ============================================================
    # 四、经费使用情况
    # ============================================================
    heading(doc, "四、经费使用情况", 1)
    para(doc, "项目总预算：1000 元。截至中期，已支出 212.54 元，"
              "剩余 787.46 元。", indent=True)

    heading(doc, "（一）已支出明细", 2)
    make_table(
        doc,
        ["序号", "项目", "内容", "金额（元）"],
        [
            ["1", "硬件采购", "ESP32-S3-N16R8 开发板 ×1", ""],
            ["", "", "SHT31 温湿度传感器（串口版）×1", ""],
            ["", "", "BH1750/GY-302 光照传感器 ×1", ""],
            ["", "", "HC-SR501 PIR 人体活动传感器 ×2", ""],
            ["", "", "LD2410C 毫米波雷达人体存在传感器 ×1", ""],
            ["", "", "830 孔面包板套件（含杜邦线、电源模块）×1", ""],
            ["", "", "硬件小计", "142.54"],
            ["2", "API 调用",
             "大模型 API 调用费用（用于环境建模、算法实现、实验脚本开发、"
             "结果分析与文档撰写）", "70.00"],
            ["", "合计", "", "212.54"],
        ],
        widths=[1.4, 2.4, 8.6, 2.6],  # 支出明细
    )
    para(doc, "", space_after=4)
    para(doc,
         "经费使用说明：硬件部分以国产替代与模块化方案控制成本（全部选取"
         "免焊接的成品模块），实际支出 142.54 元，仅占预算的 14.3%；"
         "API 调用费用 70 元，用于项目全周期的开发与实验工作，"
         "是本项目的主要智力成本项。", indent=True)

    heading(doc, "（二）剩余经费安排计划", 2)
    para(doc, "剩余金额：787.46 元。", indent=True, space_after=4)
    make_table(
        doc,
        ["优先级", "用途", "预算（元）", "说明"],
        [
            ["1", "API 调用费用预留", "300.00",
             "用于后续实测数据标定、最终实验复核、结题报告与答辩材料撰写"],
            ["2", "硬件补充", "200.00",
             "若现有传感器精度不足，补充高精度温湿度记录仪（用于数据校准比对）；"
             "或补充备件"],
            ["3", "成果制作", "150.00", "结题报告印制、答辩海报或展板制作"],
            ["4", "机动预留", "137.46",
             "应对不可预见支出（如器件损坏补购、额外数据采集需求）"],
            ["", "合计", "787.46", ""],
        ],
        widths=[1.6, 3.2, 2.4, 7.8],  # 经费安排
    )

    doc.save(DOC_PATH)
    print("已生成:", DOC_PATH)


if __name__ == "__main__":
    main()
