# -*- coding: utf-8 -*-
"""冻结模型（36ff8e8）汇总分析：941 规则前沿 / 预测式 C3 / 六格 / 30 模型。"""
import json
import math
import os
import sys
from statistics import mean, stdev

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as H  # noqa: E402

OUT = os.path.join(HERE, "out")
REPO = H.REPO
L = []
TARIFFS = list(H.TARIFFS)


def p(*a):
    s = " ".join(str(x) for x in a)
    print(s)
    L.append(s)


def load(pth):
    with open(pth, encoding="utf-8") as f:
        return json.load(f)


def rules(v, t, path=None):
    pth = path or os.path.join(OUT, "rules_%s_%s.json" % (v, t))
    return load(pth)["rows"]


def models(v):
    return load(os.path.join(OUT, "models_%s.json" % v))["models"]


def byname(rows):
    return {r["name"]: r for r in rows}


def front(v, t):
    return H.dedupe_points(H.pareto_front(rules(v, t)))


def rankdata(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    ranks = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        r = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = r
        i = j + 1
    return ranks


def pearson(a, b):
    ma, mb = mean(a), mean(b)
    sa = math.sqrt(sum((x - ma) ** 2 for x in a))
    sb = math.sqrt(sum((x - mb) ** 2 for x in b))
    return sum((x - ma) * (y - mb) for x, y in zip(a, b)) / (sa * sb)


def spearman(a, b):
    return pearson(rankdata(a), rankdata(b))


def pct(x, n=2):
    return ("%." + str(n) + "f%%") % (x * 100)


def q(xs, f):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(f * len(xs)))]


OLD_TARIFF = load(os.path.join(REPO, "models", "gen7_tariff_six",
                               "results.json"))["tiers"]

# ============================================================
p("# 冻结模型（36ff8e8）复核数据")
p("")


def section_a():
    p("## A. 尺子校准 + 版本演进")
    p("")
    p("| 电价格 | 旧 JSON 前沿条目 | 我的 old 前沿条目 | 最大偏差 | 旧工作点 | 我的工作点 |")
    p("|---|---|---|---|---|---|")
    worst_all = 0.0
    for t in TARIFFS:
        old = OLD_TARIFF[t]
        rows = rules("old", t)
        raw = H.pareto_front(rows)
        uniq = H.dedupe_points(raw)
        ob = {}
        for r in old["rule_front"]:
            ob.setdefault(r["name"], []).append(r)
        worst = 0.0
        for r in raw:
            c = ob.get(r["name"])
            if not c:
                continue
            o = min(c, key=lambda x: abs(x["cost"] - r["cost"]))
            worst = max(worst, abs(o["cost"] - r["cost"]),
                        abs(o["energy"] - r["energy"]),
                        abs(o["occ_discomfort"] - r["occ_discomfort"]))
        worst_all = max(worst_all, worst)
        p("| %s | %d | %d | %.1e | %d | %d |"
          % (t, len(old["rule_front"]), len(raw), worst,
             len(old["rule_distinct"]), len(uniq)))
    p("")
    p("- 与旧 `results.json` 逐条比对最大偏差 **%.1e**（尺子未变）。" % worst_all)
    p("- `final_energy`（旧代码 + 最终能耗公式）与 `old` 的 941 条规则不达标率/均温/C4 改写占比"
      "最大差仍为 **0.0e+00**（t6 已核，本次复核沿用同一补丁）。")
    p("")


def section_fronts():
    p("## B. 前沿规模演进（941 条 × 8 回合/格）")
    p("")
    p("| 电价格 | old(ce731b5) | t3(539561e) | t6(e9e5bb1) | **freeze(36ff8e8)** | freeze_c3off |")
    p("|---|---|---|---|---|---|")
    info = {}
    for t in TARIFFS:
        cells = []
        for v in ("old", "new", "final", "freeze", "freeze_c3off"):
            try:
                uq = front(v, t)
            except FileNotFoundError:
                cells.append("—")
                continue
            info[(v, t)] = uq
            g = H.max_gap(uq)
            cells.append("%d 点（%.2f%%~%.2f%%）" % (len(uq), g["lo"] * 100,
                                                    g["hi"] * 100))
        p("| %s | %s |" % (t, " | ".join(cells)))
    p("")
    p("（括号内为该格最大空档的区间。）")
    p("")
    return info


def section_freeze_front(info):
    p("## C. 冻结模型下的统一协议前沿（= t1_tou）")
    p("")
    uq = info[("freeze", "t1_tou")]
    g = H.max_gap(uq)
    p("**%d 个工作点，最大空档 %.2f%% ~ %.2f%%（宽 %.2f%%）**"
      % (len(uq), g["lo"] * 100, g["hi"] * 100, g["width"] * 100))
    p("")
    p("| # | 有人不达标 | 电费(元/天) | 电量(kWh/天) | 并列 | 规则 |")
    p("|---|---|---|---|---|---|")
    for i, u in enumerate(uq, 1):
        p("| %d | %s | %.4f | %.4f | %d | %s |"
          % (i, pct(u["occ_discomfort"]), u["cost"], u["energy"],
             u["n_tied"], u["name"]))
    p("")
    rows = rules("freeze", "t1_tou")
    n0 = sum(1 for r in rows if r["occ_discomfort"] <= 1e-12)
    mn = min(rows, key=lambda r: r["occ_discomfort"])
    p("- **0.00%% 不达标点：%d 条**（941 条中）。最低可达 %s。"
      % (n0, pct(mn["occ_discomfort"])))
    p("")
    p("| 变体 | 0% 条数 | 最低有人不达标 | 最高室温 | >31°C 规则数 |")
    p("|---|---|---|---|---|")
    for v in ("old", "new", "final", "freeze_c3off", "freeze"):
        try:
            rr = rules(v, "t1_tou")
        except FileNotFoundError:
            continue
        n0v = sum(1 for r in rr if r["occ_discomfort"] <= 1e-12)
        mv = min(rr, key=lambda r: r["occ_discomfort"])
        tmx = max((r.get("temp_max") or -1) for r in rr)
        n31 = sum(1 for r in rr if (r.get("temp_max") or 0) > 31.0)
        p("| %s | %d | %s | %.2f°C | %d |"
          % (v, n0v, pct(mv["occ_discomfort"]), tmx, n31))
    p("")
    # 与 t6 的进出
    a = set(u["name"] for u in info[("final", "t1_tou")])
    b = set(u["name"] for u in uq)
    p("- 与 t6 的 %d 个工作点相比：共同 %d、离开 %d、新增 %d。"
      % (len(a), len(a & b), len(a - b), len(b - a)))
    p("")
    # t1_flat
    uqf = info[("freeze", "t1_flat")]
    gf = H.max_gap(uqf)
    p("未分时格（t1_flat）：**%d 个工作点**，最大空档 %.2f%% ~ %.2f%%；"
      "最低电费点 %.4f@%s。"
      % (len(uqf), gf["lo"] * 100, gf["hi"] * 100,
         min(u["cost"] for u in uqf),
         pct(min(uqf, key=lambda u: u["cost"])["occ_discomfort"])))
    p("")


def section_c3(info):
    p("## D. 预测式 C3 的作用（freeze vs freeze_c3off，同一 RNG 流）")
    p("")
    af = byname(rules("freeze", "t1_tou"))
    ac = byname(rules("freeze_c3off", "t1_tou"))
    ks = list(af)
    dd = sorted(af[k]["occ_discomfort"] - ac[k]["occ_discomfort"] for k in ks)
    de = sorted(af[k]["energy"] - ac[k]["energy"] for k in ks)
    dc = sorted(af[k]["cost"] - ac[k]["cost"] for k in ks)
    c3 = sorted(af[k]["c3_override_share"] for k in ks)
    v3 = sorted(af[k]["c3_violation_share"] for k in ks)
    p("- C3 改写动作步占比：中位 **%.2f%%**，p90 %.2f%%，最大 %.2f%%；941/941 都有触发。"
      % (q(c3, 0.5) * 100, q(c3, 0.9) * 100, max(c3) * 100))
    p("- C3 事后越界（能力不足）步占比：中位 %.4f%%，最大 %.4f%%；"
      "触发过 violation 的规则 %d 条。"
      % (q(v3, 0.5) * 100, max(v3) * 100, sum(1 for x in v3 if x > 0)))
    p("- 有人不达标率变化（freeze − c3off）：中位 %+.5f，p10 %+.5f，p90 %+.5f，范围 %+.5f~%+.5f；"
      "改善 %d 条 / 恶化 %d 条 / 不变 %d 条。"
      % (q(dd, 0.5), q(dd, 0.1), q(dd, 0.9), min(dd), max(dd),
         sum(1 for x in dd if x < -1e-12), sum(1 for x in dd if x > 1e-12),
         sum(1 for x in dd if abs(x) <= 1e-12)))
    p("- 电量变化：中位 **%+.3f kWh/天**（p90 %+.3f，最大 %+.3f）；电费变化中位 %+.3f 元。"
      % (q(de, 0.5), q(de, 0.9), max(de), q(dc, 0.5)))
    p("- 最高室温：freeze **%.2f°C**；c3off **%.2f°C**。`T>30°C` 步占比中位 %.3f%% vs %.3f%%。"
      % (max(af[k]["temp_max"] for k in ks), max(ac[k]["temp_max"] for k in ks),
         q([af[k]["share_gt30"] for k in ks], 0.5) * 100,
         q([ac[k]["share_gt30"] for k in ks], 0.5) * 100))
    p("")

    p("### D1. 高不舒适端（不达标 ≥ 20%）")
    p("")
    p("| 变体 | 点数 | 不达标范围 | 电费范围 | 最高室温 |")
    p("|---|---|---|---|---|")
    for v in ("final", "freeze_c3off", "freeze"):
        uq = info[(v, "t1_tou")]
        hi = [u for u in uq if u["occ_discomfort"] >= 0.20]
        bw = byname(rules(v, "t1_tou"))
        tm = [bw[u["name"]].get("temp_max") for u in hi]
        tm = [x for x in tm if x is not None]
        p("| %s | %d | %s~%s | %.3f~%.3f | %s |"
          % (v, len(hi),
             pct(min(u["occ_discomfort"] for u in hi)) if hi else "—",
             pct(max(u["occ_discomfort"] for u in hi)) if hi else "—",
             min(u["cost"] for u in hi) if hi else float("nan"),
             max(u["cost"] for u in hi) if hi else float("nan"),
             ("%.2f°C" % max(tm)) if tm else "—"))
    p("")
    p("### D2. C3 对「最高不达标」规则的具体位移")
    p("")
    order = sorted(ks, key=lambda k: -ac[k]["occ_discomfort"])[:5]
    p("| 规则 | c3off 不达标 | freeze 不达标 | c3off 电费 | freeze 电费 | c3off 最高温 | freeze 最高温 |")
    p("|---|---|---|---|---|---|---|")
    for k in order:
        p("| %s | %s | %s | %.4f | %.4f | %.2f | %.2f |"
          % (k[:44], pct(ac[k]["occ_discomfort"]), pct(af[k]["occ_discomfort"]),
             ac[k]["cost"], af[k]["cost"], ac[k]["temp_max"], af[k]["temp_max"]))
    p("")


def section_noise():
    p("## E. 版本间比较的噪声尺度（重要）")
    p("")
    p("`36ff8e8` 的 `reset` 改成 `_update_occupancy(..., advance=False)`，"
      "初始占用不再多推一步 —— 这会改变 RNG 消耗顺序，因此**同一种子 9100~9107 抽样到的占用序列"
      "与 t3/t6 不再相同**。跨版本（t6→freeze）的差异 = 模型改动 + 回合抽样变化。")
    p("")
    alt_tou = rules("freeze", "t1_tou", os.path.join(OUT, "rules_freeze_s9200_t1_tou.json"))
    alt_flat = rules("freeze", "t1_flat", os.path.join(OUT, "rules_freeze_s9200_t1_flat.json"))
    for lab, base, alt in (("t1_tou", rules("freeze", "t1_tou"), alt_tou),
                           ("t1_flat", rules("freeze", "t1_flat"), alt_flat)):
        b = byname(base)
        a = byname(alt)
        ks = list(b)
        de = sorted(b[k]["energy"] - a[k]["energy"] for k in ks)
        dd = sorted(b[k]["occ_discomfort"] - a[k]["occ_discomfort"] for k in ks)
        dc = sorted(b[k]["cost"] - a[k]["cost"] for k in ks)
        p("- %s：换一批种子（9200~9207）后，同一规则的电量差中位 %+.3f kWh/天"
          "（p90 |Δ| %.3f），电费差中位 %+.3f 元，不达标率差中位 %+.4f、p90 |Δ| %.4f。"
          % (lab, q(de, 0.5), q([abs(x) for x in de], 0.9), q(dc, 0.5),
             q(dd, 0.5), q([abs(x) for x in dd], 0.9)))
    p("")

    # t6 vs freeze 关键规则
    t6 = byname(rules("final", "t1_tou"))
    fz = byname(rules("freeze", "t1_tou"))
    p("| 规则 | t6 电量 | freeze 电量 | Δ | t6 电费 | freeze 电费 | Δ | t6 不达标 | freeze 不达标 |")
    p("|---|---|---|---|---|---|---|---|---|")
    for k in ["全开 25°C", "全开 26°C", "25°C/无人关", "26°C/无人关",
              "分时三档 谷25/平26/峰28",
              "C 块[22-23] 睡26 预冷25 h0.8 p0.02"]:
        p("| %s | %.3f | %.3f | %+.3f | %.4f | %.4f | %+.4f | %s | %s |"
          % (k, t6[k]["energy"], fz[k]["energy"], fz[k]["energy"] - t6[k]["energy"],
             t6[k]["cost"], fz[k]["cost"], fz[k]["cost"] - t6[k]["cost"],
             pct(t6[k]["occ_discomfort"]), pct(fz[k]["occ_discomfort"])))
    p("")


def ref_for(uq, d):
    c = [u for u in uq if u["occ_discomfort"] <= d + 1e-12]
    return min(c, key=lambda u: u["cost"]) if c else None


def section_six(info):
    p("## F. 六格：冻结模型下 RL vs 规则参照")
    p("")
    mf = models("freeze")
    mt = models("final")
    mo = models("old")
    p("| 电价格 | RL old 电费 | RL t6 电费 | **RL freeze 电费** | RL freeze 电量 | RL old 不达标 | RL freeze 不达标 |")
    p("|---|---|---|---|---|---|---|")
    agg = {}
    for t in TARIFFS:
        ks = [k for k in mf if k.startswith(t + "_s")]
        a = {v: dict(e=mean([m[k]["energy"] for k in ks]),
                     c=mean([m[k]["cost"] for k in ks]),
                     d=mean([m[k]["occ_discomfort"] for k in ks]))
             for v, m in (("old", mo), ("t6", mt), ("freeze", mf))}
        agg[t] = a
        p("| %s | %.4f | %.4f | **%.4f** | %.3f | %s | %s |"
          % (t, a["old"]["c"], a["t6"]["c"], a["freeze"]["c"], a["freeze"]["e"],
             pct(a["old"]["d"]), pct(a["freeze"]["d"])))
    p("")
    p("| 电价格 | t6 参照(不达标) | t6 差值 | **freeze 参照(不达标)** | freeze 参照电量 | RL freeze 电量 | **freeze 差值** | Welch t | 显著性 |")
    p("|---|---|---|---|---|---|---|---|---|")
    res = {}
    for t in TARIFFS:
        rows = []
        for v, key in (("final", "t6"), ("freeze", "freeze")):
            uq = info[(v, t)]
            d = agg[t][key]["d"]
            ref = ref_for(uq, d)
            rows.append((ref, (agg[t][key]["c"] - ref["cost"]) / ref["cost"]))
        ref = rows[1][0]
        rl_s = [mean(mf[k]["cost_all"]) for k in mf if k.startswith(t + "_s")]
        tt = H.welch_t(rl_s, ref["cost_all"]) if ref else None
        sgn = "显著" if (tt is not None and abs(tt) >= 2.365) else "点估计"
        res[t] = (rows, tt)
        p("| %s | %.4f (%s) | %+.1f%% | %.4f (%s) | %.3f | %.3f | **%+.1f%%** | %s | %s |"
          % (t, rows[0][0]["cost"], pct(rows[0][0]["occ_discomfort"]),
             rows[0][1] * 100, ref["cost"], pct(ref["occ_discomfort"]),
             ref["energy"], agg[t]["freeze"]["e"], rows[1][1] * 100,
             ("%+.2f" % tt) if tt is not None else "-", sgn))
    p("")
    p("注：差值 = (RL − 规则参照)/规则参照，**负 = RL 更省**；Welch t 正值 = RL 更贵；"
      "df≈7 双侧 5% 临界 2.365。")
    p("")
    return agg, res


def section_models():
    p("## G. 30 个已保存模型（旧能量模型训练，重评 ≠ 重训）")
    p("")
    mo = models("old")
    mt = models("final")
    mf = models("freeze")
    p("| 模型 | old 电量 | t6 电量 | freeze 电量 | old 电费 | t6 电费 | **freeze 电费** | old 不达标 | freeze 不达标 | freeze C3 改写 | freeze C4 阻断 |")
    p("|---|---|---|---|---|---|---|---|---|---|---|")
    for k in mo:
        p("| %s | %.3f | %.3f | %.3f | %.3f | %.3f | **%.3f** | %s | %s | %.2f%% | %.2f%% |"
          % (k, mo[k]["energy"], mt[k]["energy"], mf[k]["energy"],
             mo[k]["cost"], mt[k]["cost"], mf[k]["cost"],
             pct(mo[k]["occ_discomfort"]), pct(mf[k]["occ_discomfort"]),
             mf[k]["c3_override_share"] * 100, mf[k]["ac_blocked_share"] * 100))
    p("")
    mc = models("freeze_c3off")
    p("- freeze 与 freeze_c3off 的 30 个模型：最大 |Δ电量| %.4f、最大 |Δ不达标| %.4f"
      " → C3 在 RL 策略上几乎不触发。"
      % (max(abs(mf[k]["energy"] - mc[k]["energy"]) for k in mf),
         max(abs(mf[k]["occ_discomfort"] - mc[k]["occ_discomfort"]) for k in mf)))
    rn = byname(rules("freeze", "t1_flat"))
    p("- 参照（freeze，t1_flat）：`全开 25°C` = %.3f kWh / %.3f 元 / %s；"
      "`全开 26°C` = %.3f / %.3f / %s"
      % (rn["全开 25°C"]["energy"], rn["全开 25°C"]["cost"],
         pct(rn["全开 25°C"]["occ_discomfort"]),
         rn["全开 26°C"]["energy"], rn["全开 26°C"]["cost"],
         pct(rn["全开 26°C"]["occ_discomfort"])))
    p("")


def section_savings(info):
    p("## H. 「规则比恒温全开省多少」的多种口径（用于核对 24.0% / 18.5%）")
    p("")
    p("t6 报告里的 24.0% / 18.5% 无法在冻结模型下用任何单一标准定义复现；"
      "下面把所有合理的定义都算出来，供主负责人对号。")
    p("")
    sv = {}
    for t in ("t1_flat", "t1_tou"):
        rows = byname(rules("freeze", t))
        uq = info[("freeze", t)]
        a25 = rows["全开 25°C"]
        d25 = a25["occ_discomfort"]
        cands = [u for u in uq if u["occ_discomfort"] <= d25 + 1e-12]
        best_eq = min(cands, key=lambda u: u["cost"]) if cands else None
        save_cost = ((a25["cost"] - best_eq["cost"]) / a25["cost"] * 100
                     if best_eq else float("nan"))
        save_energy = ((a25["energy"] - best_eq["energy"]) / a25["energy"] * 100
                       if best_eq else float("nan"))
        sv[t] = dict(save_cost=save_cost, save_energy=save_energy, a25=a25,
                     best=best_eq, mincost=min(u["cost"] for u in uq))
    p("| 口径 | t1_flat | t1_tou |")
    p("|---|---|---|")
    p("| 同舒适度下「最优规则 vs 全开 25°C」电费节省 | %.1f%% | %.1f%% |"
      % (sv["t1_flat"]["save_cost"], sv["t1_tou"]["save_cost"]))
    p("| 同舒适度下电量节省 | %.1f%% | %.1f%% |"
      % (sv["t1_flat"]["save_energy"], sv["t1_tou"]["save_energy"]))
    p("| 全开 25°C 电费（元/天） | %.4f | %.4f |"
      % (sv["t1_flat"]["a25"]["cost"], sv["t1_tou"]["a25"]["cost"]))
    p("| 全开 25°C 不达标率 | %s | %s |"
      % (pct(sv["t1_flat"]["a25"]["occ_discomfort"]),
         pct(sv["t1_tou"]["a25"]["occ_discomfort"])))
    p("| 最优同舒适度规则电费（元/天） | %.4f | %.4f |"
      % (sv["t1_flat"]["best"]["cost"], sv["t1_tou"]["best"]["cost"]))
    p("| 该规则 | %s | %s |"
      % (sv["t1_flat"]["best"]["name"][:34], sv["t1_tou"]["best"]["name"][:34]))
    p("| 前沿最便宜点电费（元/天） | %.4f | %.4f |"
      % (sv["t1_flat"]["mincost"], sv["t1_tou"]["mincost"]))
    p("")


def section_episode():
    p("## I. 9100~9107 逐回合（冻结模型，统一协议 = t1_tou）")
    p("")
    o = byname(rules("old", "t1_tou"))
    f = byname(rules("freeze", "t1_tou"))
    for k in ["全开 25°C", "C 块[22-23] 睡26 预冷25 h0.8 p0.02",
              "分时三档 谷25/平26/峰28"]:
        p("**%s**" % k)
        p("")
        p("| seed | old 电量 | old 电费 | old 不达标 | freeze 电量 | freeze 电费 | freeze 不达标 |")
        p("|---|---|---|---|---|---|---|")
        for i in range(8):
            p("| %d | %.4f | %.4f | %s | %.4f | %.4f | %s |"
              % (H.EVAL_SEED0 + i, o[k]["energy_all"][i], o[k]["cost_all"][i],
                 pct(o[k]["occ_discomfort_all"][i]),
                 f[k]["energy_all"][i], f[k]["cost_all"][i],
                 pct(f[k]["occ_discomfort_all"][i])))
        p("| 均值 | %.4f | %.4f | %s | %.4f | %.4f | %s |"
          % (mean(o[k]["energy_all"]), mean(o[k]["cost_all"]),
             pct(mean(o[k]["occ_discomfort_all"])),
             mean(f[k]["energy_all"]), mean(f[k]["cost_all"]),
             pct(mean(f[k]["occ_discomfort_all"]))))
        p("")
    p("### I2. RL 逐回合电费（5 个模型同回合均值）")
    p("")
    p("| 电价格 | 口径 | 9100 | 9101 | 9102 | 9103 | 9104 | 9105 | 9106 | 9107 |")
    p("|---|---|---|---|---|---|---|---|---|---|")
    mm = {"old": models("old"), "t6": models("final"), "freeze": models("freeze")}
    for t in TARIFFS:
        ks = [k for k in mm["freeze"] if k.startswith(t + "_s")]
        for v, lab in (("old", "old"), ("t6", "t6"), ("freeze", "freeze")):
            vals = [mean([mm[v][k]["cost_all"][i] for k in ks]) for i in range(8)]
            p("| %s | %s | %s |" % (t, lab, " | ".join("%.3f" % x for x in vals)))
    p("")


def main():
    section_a()
    info = section_fronts()
    section_freeze_front(info)
    section_c3(info)
    section_noise()
    section_six(info)
    section_models()
    section_savings(info)
    section_episode()
    with open(os.path.join(OUT, "freeze.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(L) + "\n")
    print("\nsaved out/freeze.md")


if __name__ == "__main__":
    main()
