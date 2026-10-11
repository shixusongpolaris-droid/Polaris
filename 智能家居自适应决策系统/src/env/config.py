"""
配置参数 —— 智能家居自适应决策系统

本文件集中存放所有可调参数，与 MDP 定义文档（docs/MDP定义_v1.0.md）一一对应。
把所有参数集中在这里的目的：改参数不用动代码逻辑，也便于做敏感性分析。

对应关系：
    SimConfig       -> 文档第 4 节（状态转移 / 仿真模型），问题 Q6
    RewardConfig    -> 文档第 3 节（奖励函数），问题 Q1、Q2、Q3
    ActionConfig    -> 文档第 2 节（动作空间）
    ConstraintConfig-> 文档第 3.4 节（约束条件）
"""

from dataclasses import dataclass, field
from typing import List


# ============================================================
# 仿真模型参数（对应文档第 4 节）
# ============================================================
@dataclass
class SimConfig:
    """环境仿真模型的物理参数。

    注意：这些参数是简化的物理模型参数，不是从真实建筑标定得到的。
    文档 Q6 要求后续用实测数据做最小二乘拟合来标定它们。
    """

    # ---------- 时间设定 ----------
    dt_minutes: int = 15              # 时间步长（分钟），文档设定 15 分钟
    steps_per_day: int = 96           # 每天步数 = 24*60/15 = 96

    # ---------- 热模型参数 ----------
    temp_initial: float = 28.0        # 初始室内温度（°C）
    temp_outdoor_mean: float = 30.0   # 日平均室外温度（°C）
    temp_outdoor_swing: float = 5.0   # 室外温度日波动幅度（°C）

    # 围护结构热传导系数：室内外每差 1°C，每 15 分钟的温度变化量（°C）
    # 数值越大表示保温越差、温度向室外靠拢越快
    ua_coef: float = 0.040

    # 空调等效降温系数：室内与设定温度每差 1°C，每 15 分钟的降温量（°C）
    #
    # 取值依据（稳态分析）：
    #   平衡条件是 制冷量 = 围护传热 + 内部增益。
    #   在室外 30°C、室内 26°C 的条件下，围护传热约 +0.16 °C/步。
    #   若 ac_coef = 0.10，则制冷量仅 0.20 °C/步，净变化接近 0，
    #   平衡点落在约 25.9°C —— 即设定 24°C 时物理上根本达不到，
    #   会导致所有策略都被"制冷能力不足"掩盖，对比失去意义。
    #   取 ac_coef = 0.30 后，设定 24°C 时平衡点约 23.7°C（略低于设定值），
    #   设定 26°C 时平衡点约 25.8°C —— 均能基本维持，符合家用空调实际能力。
    ac_coef: float = 0.30

    # 空调单步最大降温能力（°C），模拟设备功率上限
    ac_max_cooling: float = 1.2

    # 通风（开窗）换热系数：室内外每差 1°C，每 15 分钟的温度变化量（°C）
    vent_coef: float = 0.12

    # 内部热增益：有人时每步的温度抬升（°C）
    internal_gain: float = 0.02

    # 安全上限：温度不会超过此值（°C），防止仿真发散
    temp_clip_max: float = 45.0

    # ---------- 湿度模型参数 ----------
    hum_initial: float = 55.0         # 初始相对湿度（%）
    hum_outdoor: float = 60.0         # 室外相对湿度（%）
    hum_ac_dehumid: float = 0.30      # 空调开启时每步除湿量（%）
    hum_vent_rate: float = 0.08       # 通风时向室外湿度靠拢的比例
    hum_occupant_gain: float = 0.25   # 每人每步的湿度增加（%）
    hum_clip_min: float = 20.0
    hum_clip_max: float = 90.0

    # ---------- 光照模型参数 ----------
    light_outdoor_max: float = 20000.0  # 正午室外最大照度（lx）
    light_window_ratio: float = 0.15    # 窗户透光比例（室内 = 室外 × 此比例）
    light_low_level: float = 250.0      # 弱档照明贡献（lx）
    light_high_level: float = 800.0     # 强档照明贡献（lx）

    # ---------- 人员占用模型参数 ----------
    # 周末相对工作日的在室概率修正（正值表示更可能在家）
    weekend_boost: float = 0.20

    # 历史同时段占用率的计算窗口（天）
    occ_slot_history_days: int = 7

    # ---------- 占用基础概率表 ----------
    # 7 天 × 24 小时。行 = 周一~周日，列 = 0~23 时。
    # 这是"典型上班族"的先验。真实运行时会被实测数据替换。
    occupancy_prob: List[List[float]] = field(default_factory=lambda: [
        # 0    1    2    3    4    5    6    7    8    9   10   11
        [0.95,0.95,0.95,0.95,0.95,0.90,0.60,0.20,0.10,0.10,0.10,0.10,
         0.10,0.10,0.10,0.10,0.15,0.35,0.80,0.90,0.95,0.95,0.95,0.95],  # 周一
        [0.95,0.95,0.95,0.95,0.95,0.90,0.60,0.20,0.10,0.10,0.10,0.10,
         0.10,0.10,0.10,0.10,0.15,0.35,0.80,0.90,0.95,0.95,0.95,0.95],  # 周二
        [0.95,0.95,0.95,0.95,0.95,0.90,0.60,0.20,0.10,0.10,0.10,0.10,
         0.10,0.10,0.10,0.10,0.15,0.35,0.80,0.90,0.95,0.95,0.95,0.95],  # 周三
        [0.95,0.95,0.95,0.95,0.95,0.90,0.60,0.20,0.10,0.10,0.10,0.10,
         0.10,0.10,0.10,0.10,0.15,0.35,0.80,0.90,0.95,0.95,0.95,0.95],  # 周四
        [0.95,0.95,0.95,0.95,0.95,0.85,0.50,0.20,0.10,0.10,0.10,0.10,
         0.10,0.10,0.10,0.10,0.20,0.45,0.75,0.85,0.90,0.95,0.95,0.95],  # 周五
        [0.95,0.95,0.95,0.95,0.95,0.85,0.50,0.30,0.30,0.40,0.50,0.50,
         0.50,0.40,0.40,0.50,0.60,0.70,0.80,0.85,0.90,0.95,0.95,0.95],  # 周六
        [0.95,0.95,0.95,0.95,0.95,0.85,0.50,0.30,0.30,0.30,0.30,0.30,
         0.30,0.30,0.30,0.40,0.60,0.80,0.90,0.95,0.95,0.95,0.95,0.95],  # 周日
    ])


# ============================================================
# 动作空间参数（对应文档第 2 节）
# ============================================================
@dataclass
class ActionConfig:
    """动作空间的离散化定义。

    联合动作 = (空调档, 照明档, 通风档)。
    空调档位经细化后为 6 档（关闭 + 25/26/27/28/29°C），
    因此 |A| = 6 × 3 × 2 = 36。

    为什么细化：
        初版只有 24/26/28°C 三档（步长 2°C），实测发现分时电价场景下
        调度精度不足——人工规则能实现"谷段 24°C、峰段 28°C"的粗调度，
        但强化学习需要更细的中间档位才能找到更优工作点。
        细化后步长为 1°C，使"舒适"与"省电"之间的过渡更平滑。
        代价是动作空间由 24 增至 36，仍在 PPO/DQN 的合理范围内。
    """

    # 空调档位：0 = 关闭；1..5 = 开启并设定对应温度（共 5 个设定值）
    ac_setpoints: List[float] = field(
        default_factory=lambda: [25.0, 26.0, 27.0, 28.0, 29.0])

    # 各空调档对应的压机负载比例（用于能耗计算）
    # 设定温度越低，压机负载越高
    ac_load_ratio: List[float] = field(
        default_factory=lambda: [1.00, 0.80, 0.62, 0.46, 0.34])

    # 照明档位：0=关，1=弱，2=强
    light_choices: int = 3

    # 通风档位：0=关，1=开
    vent_choices: int = 2

    @property
    def ac_choices(self) -> int:
        """空调动作数 = 关闭 + 各设定温度档"""
        return 1 + len(self.ac_setpoints)

    @property
    def n_actions(self) -> int:
        return self.ac_choices * self.light_choices * self.vent_choices


# ============================================================
# 约束参数（对应文档第 3.4 节）
# ============================================================
@dataclass
class ConstraintConfig:
    """硬约束参数。

    第一轮采用"动作屏蔽"方式实现，即策略输出违反约束的动作时，
    该动作被替换为保持现状的动作。
    """

    # C1/C2：空调状态（开关或设定档）改变的最小间隔（分钟）
    ac_min_switch_interval: int = 10

    # C3：室内温度强制安全区间（°C）
    temp_safe_min: float = 16.0
    temp_safe_max: float = 30.0

    # C4：无人超过该时长后必须关闭空调（分钟）
    ac_auto_off_empty: int = 30


# ============================================================
# 奖励函数参数（对应文档第 3 节）
# ============================================================
@dataclass
class RewardConfig:
    """奖励函数的全部系数。

    奖励形式（文档第 3 节）：
        R = − [ C_comfort + λ_e · E ]

        C_comfort = w_T·f_T + w_H·f_H + w_L·f_L
    """

    # ---------- 舒适度区间（问题 Q1）----------
    # 有人时的舒适温度区间（°C）
    temp_low_occupied: float = 22.0
    temp_high_occupied: float = 26.0

    # 睡眠时段（23:00-07:00）的舒适温度区间（°C）
    temp_low_sleep: float = 24.0
    temp_high_sleep: float = 27.0

    # 无人时的舒适区间（放宽，以节能为主）
    temp_low_empty: float = 16.0
    temp_high_empty: float = 30.0

    # 睡眠时段定义
    sleep_start_hour: int = 23
    sleep_end_hour: int = 7

    # 舒适湿度区间（%）
    hum_low: float = 40.0
    hum_high: float = 60.0

    # 人员活动所需最低照度（lx）
    light_min_lux: float = 300.0

    # ---------- 各项权重 ----------
    w_temp: float = 1.0       # 温度代价权重
    w_hum: float = 0.5        # 湿度代价权重
    w_light: float = 0.5      # 光照代价权重

    # 温度区间外惩罚系数（每°C 每步的代价）
    alpha_cool: float = 1.0   # 偏冷
    alpha_hot: float = 1.0    # 偏热

    # 温度偏离的放大倍数，用于让温度项在奖励中占合适比重
    temp_scale: float = 3.0

    # 湿度区间外惩罚系数
    beta_hum: float = 0.05

    # 光照代价常量
    gamma_light_off: float = 0.3     # 有人但光照不足
    gamma_light_waste: float = 0.5   # 无人却开灯

    # ---------- 量纲归一化（关键参数）----------
    # 能耗项的量级远小于舒适度项（约 1e-2 kWh vs 1e1 代价），
    # 若不归一化，能耗权重 λ_e 无论如何调整都无法影响策略，
    # 多目标权衡机制会失效。这里通过 energy_scale 把能耗换算到
    # 与舒适度可比的量级。
    #
    # 归一化后的含义（便于向读者解释）：
    #   温度偏离舒适区间 1°C 的代价 ≈ temp_scale × 1 = 3.0
    #   空调（24°C档）运行 1 步的代价 ≈ energy_scale × 0.1375 ≈ 1.4
    # 即"舒适度 1°C 的牺牲 ≈ 可换取约 2 步空调运行时间"
    energy_scale: float = 10.0

    # ---------- 切换惩罚 ----------
    # 防止设备状态频繁抖动。频繁切换会带来设备损耗与额外能耗，
    # 属于文档 5.3 节"响应效率"下的负面指标，故在奖励中显式惩罚。
    switch_penalty: float = 0.20

    # ---------- 能耗参数（问题 Q2）----------
    # 空调额定电功率（kW）
    ac_rated_kw: float = 1.10
    # 空调名义制冷量（kW），用于把制冷量换算成电功率
    ac_cooling_capacity_kw: float = 3.50
    # 照明额定功率（kW）
    light_rated_kw: float = 0.060
    # 通风额定功率（kW）
    vent_rated_kw: float = 0.045

    # 压缩机启动损耗（kWh/次启动）
    #
    # 为什么需要这一项：制冷压缩机的启停过程存在效率损失
    # （启动电流冲击、压力未平衡时的低效运行）。若不建模该损耗，
    # 仿真会得出"恒温运行比间歇运行更省电"的结论——这在现实中不成立，
    # 因为频繁启停的代价没有计入。
    #
    # 取值含义：每次启动额外消耗约 0.010 kWh
    # （相当于额定功率运行约 33 秒的耗电量，属于保守估计）
    ac_startup_penalty_kwh: float = 0.010

    # 空调开启时的最低运行功率比例（相对额定功率）
    #
    # 物理依据：空调开启后，即使室温已达设定值、压缩机卸载或降频，
    # 室内风机、室外风机、控制电路仍持续耗电；变频压缩机的输出也无法
    # 降到零（存在最低运行频率）。
    #
    # 若不建模这一项，会出现一个反常结果：空调"开着但不需要制冷"时
    # 耗电为零，导致"全程恒温运行"在能耗上优于任何间歇策略，
    # 而这是不符合实际的。
    #
    # 取值说明：家用分体空调待机（送风）功率约 0.15~0.25 倍额定功率，
    # 取 0.20 作为代表值。
    ac_min_power_ratio: float = 0.20

    # 空调通电但关闭时的待机功率（kW）
    # （室内机控制电路、显示屏等；量级很小，用于体现"完全断电才最省"）
    ac_standby_kw: float = 0.0015

    # ---------- 储能系统（v4 新增：实验性功能）----------
    # 用途：为跨时段能量调度提供**物理载体**。
    #
    # 动机：v3 实验发现，强化学习在分时电价场景下学不会错峰用电。
    #   进一步分析指出根本原因是"问题的物理结构限制"——空调的制冷
    #   必须即时交付，无法把谷段的冷量存到峰段使用。因此在没有储能的
    #   场景下，任何策略都只能在"即时舒适"与"即时电费"之间取舍。
    #
    #   本配置引入电池储能，使"低价时段充电、高价时段放电"成为可能，
    #   从而检验"有储能时强化学习能否超过规则"这一假设。
    #
    # 建模方式（简化但自洽）：
    #   · 电池在电价低时充电（从电网取电，计入电费），
    #     在电价高时放电（抵扣空调压缩机功耗，不计电费）。
    #   · 充电/放电效率分开建模，往返效率 = charge_eff × discharge_eff。
    #   · 电池状态（SOC、上一步功率）进入观测空间，使策略可感知储能水平。
    #
    # 说明：这**不是**对某款真实产品的建模，而是用于研究"储能对控制策略
    #       影响"的理想化模型。经济性方面的结论需谨慎解读（见实验结果报告）。
    battery_enabled: bool = False

    # 电池容量（kWh）
    # 取值 4.0：约相当于空调 2 小时的额定运行电量（1.1 kW × 2 h = 2.2 kWh）
    # 的 1.8 倍，足以覆盖一个峰段（3 小时）的大部分空调需求。
    battery_capacity_kwh: float = 4.0

    # 最大充放电功率（kW）
    # 取 1.5：略高于空调额定功率 1.1 kW，使电池能同时满足空调需求与充电。
    battery_max_power_kw: float = 1.5

    # 充电效率（交流→电池）
    battery_charge_eff: float = 0.95

    # 放电效率（电池→负载）
    battery_discharge_eff: float = 0.95

    # 初始电量比例（0~1）
    battery_init_soc: float = 0.5

    # 充放电策略：由电价分档决定（简单阈值规则）
    # True  = 谷段充电、峰段放电（规则式调度，用于构造基线）
    # False = 由策略自行决定（强化学习需要动作维度控制，暂未实现）
    #
    # 说明：当前实现采用**规则式充放电**，即储能本身由电价阈值控制，
    #   强化学习只负责空调/照明/通风的决策。这样处理的原因是：
    #   在不改变动作空间的前提下，储能的作用被固化为"电费转移装置"，
    #   从而使"有储能 vs 无储能"成为可对比的实验条件。
    #   这大幅简化了实验，代价是放弃了对储能调度本身的优化。
    battery_rule_based: bool = True

    # 放电的最低 SOC 保护（低于此值不再放电，避免深度放电）
    battery_min_soc: float = 0.05

    # 平段放电的电量阈值（占容量比例）
    # 平段电价（0.60）高于谷段（0.30），因此在电量充足时放电也划算；
    # 但需保留部分电量给峰段（1.00），故设阈值：
    #   SOC 高于此比例时，平段也放电；低于则保留给峰段。
    battery_flat_reserve_ratio: float = 0.6

    # 照明各档功率比例
    light_power_ratio: List[float] = field(default_factory=lambda: [0.0, 0.30, 1.00])

    # ---------- 场景化能耗权重（问题 Q3，本项目核心机制）----------
    # 使用与文档第 3.3 节一致的分段常数表。
    # 权重取值参考文档表格，后续通过参数扫描和帕累托前沿确定。
    lambda_energy: float = 0.5          # 默认能耗权重（固定权重基线用这个值）

    # 场景化权重表：{场景名: λ_e}
    lambda_by_scenario: dict = field(default_factory=lambda: {
        "workday_day_empty": 1.00,      # SC1 工作日白天（典型无人）
        "workday_evening":   0.50,      # SC2 工作日 17:00-23:00
        "workday_sleep":     0.40,      # SC3 工作日 23:00-07:00
        "friday_evening":    0.50,      # SC4 周五 17:00-23:00
        "sunday_evening":    0.30,      # SC5 周日 17:00-23:00
        "weekend_daytime":   0.50,      # SC6 周末白天
        "holiday":           0.45,      # SC7 法定节假日
    })

    # 权重使用模式：
    #   "fixed"    -> 始终用 lambda_energy（基线 A1 / B3）
    #   "scenario" -> 按场景取权重（本方法 A2 / B4）
    #   "random"   -> 每回合随机取权重（对照 A3）
    weight_mode: str = "scenario"

    # ---------- 分时电价（TOU：Time-of-Use tariff）----------
    #
    # 启用后，能耗代价按当时电价加权计算，E 的单位由 kWh 变为"元"。
    # 这使系统能够做**跨时段的能量调度**：
    #   在低价时段多制冷（预冷 / 蓄冷），在高价时段少用电。
    #
    # 为什么加入这一机制：
    #   在没有电价的场景下，恒温运行近似最优，强化学习没有优化空间
    #   （实测：本方法相对理想恒温控制仅节能约 7%，且结论对参数敏感）。
    #   引入分时电价后，"何时用电"成为可优化的时序决策问题，
    #   而这正是强化学习相对规则控制的优势所在。
    #
    # 电价取值参考国内居民分时电价的典型水平（单位：元/kWh）：
    #   峰段约为平段的 1.7 倍，谷段约为平段的 0.4 倍
    tou_enabled: bool = False        # 是否启用分时电价

    price_valley: float = 0.30       # 谷段电价
    price_flat: float = 0.60         # 平段电价
    price_peak: float = 1.00         # 峰段电价

    # 时段划分（小时）
    peak_hours: tuple = (8, 10, 18, 21)      # 峰段：8-10 时、18-21 时
    valley_hours: tuple = (22, 6)            # 谷段：22 时至次日 6 时
    # 其余时段为平段

    def get_price(self, hour: int) -> float:
        """返回指定小时的电价（元/kWh）。"""
        if not self.tou_enabled:
            return 1.0                # 未启用时返回 1.0，使能耗项退化为纯 kWh
        h = int(hour)
        # 峰段
        ph0, ph1, ph2, ph3 = self.peak_hours
        if ph0 <= h < ph1 or ph2 <= h < ph3:
            return self.price_peak
        # 谷段（跨零点）
        vh0, vh1 = self.valley_hours
        if h >= vh0 or h < vh1:
            return self.price_valley
        return self.price_flat

    def get_tier_name(self, hour: int) -> str:
        """返回电价档位名称（用于统计与可视化）。"""
        if not self.tou_enabled:
            return "flat"
        p = self.get_price(hour)
        if abs(p - self.price_peak) < 1e-9:
            return "peak"
        if abs(p - self.price_valley) < 1e-9:
            return "valley"
        return "flat"

    # ---------- 前视电价（解决"延迟收益被折扣"的问题）----------
    #
    # 问题：直接使用瞬时电价时，"现在多制冷（谷段蓄冷）"的收益要等到
    # 数小时后才体现，在折扣因子 γ 下权重极低；而"现在停机省电"的
    # 舒适度惩罚是即时的。结果算法不愿做跨时段调度。
    #
    # 做法：用于奖励的电价改为"当前电价"与"未来一段时间平均电价"的
    # 加权组合：
    #       p_eff = (1-w) * p_now + w * mean(p_next_H)
    # 当未来电价更高时，p_eff 上升，算法会倾向于"现在多制冷"，
    # 因为现在的制冷能在未来高价时段减少用电。
    #
    # 这在工程上等价于给算法一个"电价趋势"信号，把跨时段调度的
    # 延迟收益部分转化为即时信号。
    lookahead_hours: int = 4        # 前视小时数
    lookahead_weight: float = 0.6   # 前视电价在 p_eff 中的权重

    def get_forward_price(self, hour: int) -> float:
        """返回用于奖励计算的前视电价。

        未启用 TOU 时返回 1.0（退化为纯电量计价）。
        """
        if not self.tou_enabled:
            return 1.0
        p_now = self.get_price(hour)
        if self.lookahead_weight <= 0 or self.lookahead_hours <= 0:
            return p_now

        # 取未来 lookahead_hours 小时的平均电价（含当前小时）
        n = int(self.lookahead_hours)
        future = [self.get_price((int(hour) + k) % 24) for k in range(n + 1)]
        p_future = sum(future) / len(future)

        w = float(self.lookahead_weight)
        return (1.0 - w) * p_now + w * p_future


    def get_energy_weight(self, hour: int, dow: int,
                          is_weekend: bool, is_holiday: bool,
                          occupied: bool, rng=None) -> float:
        """根据时间场景返回能耗权重 λ_e。

        参数：
            hour       当前小时（0-23）
            dow        星期（0=周一 ... 6=周日）
            is_weekend 是否周末
            is_holiday 是否节假日
            occupied   当前是否有人
            rng        随机数发生器（weight_mode="random" 时使用）

        返回：λ_e
        """
        if self.weight_mode == "fixed":
            return self.lambda_energy

        if self.weight_mode == "random":
            # 对照实验 A3：权重随机变化，与时间场景无关
            if rng is None:
                import random
                return random.choice(list(self.lambda_by_scenario.values()))
            return float(rng.choice(list(self.lambda_by_scenario.values())))

        # ---- 场景化权重（weight_mode == "scenario"）----
        W = self.lambda_by_scenario

        if is_holiday:
            return W["holiday"]
        if is_weekend:
            return W["weekend_daytime"]
        # 以下为工作日
        if hour >= self.sleep_start_hour or hour < self.sleep_end_hour:
            return W["workday_sleep"]
        if 17 <= hour < 23:
            if dow == 4:            # 周五
                return W["friday_evening"]
            if dow == 6:            # 周日
                return W["sunday_evening"]
            return W["workday_evening"]
        if 9 <= hour < 17 and not occupied:
            return W["workday_day_empty"]
        return W["workday_evening"]
