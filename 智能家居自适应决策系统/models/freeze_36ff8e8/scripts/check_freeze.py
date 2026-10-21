# -*- coding: utf-8 -*-
"""回流核对：冻结模型报告引用的关键数字与 out/*.json 对齐。"""
import json
import os
import sys
from statistics import mean, stdev

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as H  # noqa: E402

OUT = os.path.join(HERE, "out")
CHECKS = []


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def rules(v, t, path=None):
    pth = path or os.path.join(OUT, "rules_%s_%s.json" % (v, t))
    return load(pth)["rows"]


def fr(v, t):
    return H.dedupe_points(H.pareto_front(rules(v, t)))


def models(v):
    return load(os.path.join(OUT, "models_%s.json" % v))["models"]


def chk(label, got, want, tol=5e-4):
    ok = abs(got - want) <= tol
    CHECKS.append((ok, label, got, want))


# 前沿点数
for t in H.TARIFFS:
    flat = t.endswith("flat")
    chk("freeze front %s" % t, len(fr("freeze", t)), 23 if flat else 32, 0)
    chk("freeze_c3off front %s" % t, len(fr("freeze_c3off", t)),
        (21 if flat else (28 if t == "t3_tou" else 29)), 0)

# 最大空档
g = H.max_gap(fr("freeze", "t1_tou"))
chk("gap lo", g["lo"], 0.2026, 5e-4)
chk("gap hi", g["hi"], 0.3160, 5e-4)
gf = H.max_gap(fr("freeze", "t1_flat"))
chk("flat gap lo", gf["lo"], 0.4484, 5e-4)
chk("flat gap hi", gf["hi"], 0.5668, 5e-4)

# 前沿家族构成
fam = {}
for u in fr("freeze", "t1_tou"):
    n = u["name"]
    k = ("C" if n.startswith("C 块") else "D" if n.startswith("D ")
         else "B" if n.startswith("B ") else "A" if n.startswith("A ")
         else "base")
    fam[k] = fam.get(k, 0) + 1
print("family:", fam)
chk("C fam", fam.get("C", 0), 11, 0)
chk("B fam", fam.get("B", 0), 10, 0)
chk("D fam", fam.get("D", 0), 4, 0)
chk("A fam", fam.get("A", 0), 5, 0)

# 关键规则
d = {r["name"]: r for r in rules("freeze", "t1_tou")}
chk("全开25 cost", d["全开 25°C"]["cost"], 3.4738)
chk("全开25 energy", d["全开 25°C"]["energy"], 6.0568)
chk("全开25 d", d["全开 25°C"]["occ_discomfort"], 0.0)
chk("全开26 cost", d["全开 26°C"]["cost"], 2.7146)
chk("全开26 energy", d["全开 26°C"]["energy"], 4.6280)
chk("全开26 d", d["全开 26°C"]["occ_discomfort"], 0.4986)
chk("C块 cost", d["C 块[22-23] 睡26 预冷25 h0.8 p0.02"]["cost"], 3.2956)
chk("C块 energy", d["C 块[22-23] 睡26 预冷25 h0.8 p0.02"]["energy"], 5.5314)
chk("分时三档 cost", d["分时三档 谷25/平26/峰28"]["cost"], 1.6919)
chk("分时三档 energy", d["分时三档 谷25/平26/峰28"]["energy"], 3.1995)
chk("分时三档 d", d["分时三档 谷25/平26/峰28"]["occ_discomfort"], 0.4857)
d2 = {r["name"]: r for r in rules("freeze", "t1_flat")}
chk("t1flat 全开25 cost", d2["全开 25°C"]["cost"], 3.7370)
chk("t1flat C块 cost", d2["C 块[22-23] 睡26 预冷25 h0.8 p0.02"]["cost"], 3.4129)

# 0% 计数
for v, want in (("old", 4), ("final", 0), ("freeze_c3off", 0), ("freeze", 57)):
    n0 = sum(1 for r in rules(v, "t1_tou") if r["occ_discomfort"] <= 1e-12)
    chk("zero %s" % v, n0, want, 0)

# C3 统计
af = {r["name"]: r for r in rules("freeze", "t1_tou")}
ac = {r["name"]: r for r in rules("freeze_c3off", "t1_tou")}
ks = list(af)
dd = sorted(af[k]["occ_discomfort"] - ac[k]["occ_discomfort"] for k in ks)
de = sorted(af[k]["energy"] - ac[k]["energy"] for k in ks)
dc = sorted(af[k]["cost"] - ac[k]["cost"] for k in ks)
c3 = sorted(af[k]["c3_override_share"] for k in ks)
chk("c3 median", c3[len(c3) // 2], 0.0149, 5e-4)
chk("c3 max", c3[-1], 0.0186, 5e-4)
chk("c3 improved", sum(1 for x in dd if x < -1e-12), 638, 0)
chk("c3 worsened", sum(1 for x in dd if x > 1e-12), 194, 0)
chk("c3 unchanged", sum(1 for x in dd if abs(x) <= 1e-12), 109, 0)
chk("c3 d median", dd[len(dd) // 2], -0.00057, 5e-5)
chk("c3 e median", de[len(de) // 2], 0.401, 5e-3)
chk("c3 cost median", dc[len(dc) // 2], 0.247, 5e-3)
chk("tmax freeze", max(af[k]["temp_max"] for k in ks), 30.00, 0.01)
chk("tmax c3off", max(ac[k]["temp_max"] for k in ks), 31.71, 0.01)
chk("violations", sum(1 for k in ks if af[k]["c3_violation_share"] > 0), 0, 0)
s30 = sorted(af[k]["share_gt30"] for k in ks)
c30 = sorted(ac[k]["share_gt30"] for k in ks)
chk("share>30 freeze median", s30[len(s30) // 2], 0.0, 1e-6)
chk("share>30 c3off median", c30[len(c30) // 2], 0.09691, 5e-4)

# 高不舒适端
for v, want in (("final", 7), ("freeze_c3off", 9), ("freeze", 12)):
    hi = [u for u in fr(v, "t1_tou") if u["occ_discomfort"] >= 0.20]
    chk("high-d %s" % v, len(hi), want, 0)

# 六格
want_diff = {"t1_flat": -0.046, "t1_tou": 0.118, "t2_flat": 0.084,
             "t2_tou": 0.059, "t3_flat": -0.039, "t3_tou": -0.010}
want_t = {"t1_flat": -0.79, "t1_tou": 1.87, "t2_flat": 1.76,
          "t2_tou": 0.67, "t3_flat": -0.87, "t3_tou": -0.21}
want_ref = {"t1_flat": 3.3040, "t1_tou": 3.2552, "t2_flat": 3.5718,
            "t2_tou": 3.5718, "t3_flat": 4.9105, "t3_tou": 5.0788}
want_cost = {"t1_flat": 3.1519, "t1_tou": 3.6382, "t2_flat": 3.8712,
             "t2_tou": 3.7820, "t3_flat": 4.7179, "t3_tou": 5.0287}
mf = models("freeze")
for t in H.TARIFFS:
    ks2 = [k for k in mf if k.startswith(t + "_s")]
    cost = mean([mf[k]["cost"] for k in ks2])
    dmean = mean([mf[k]["occ_discomfort"] for k in ks2])
    cands = [u for u in fr("freeze", t) if u["occ_discomfort"] <= dmean + 1e-12]
    ref = min(cands, key=lambda u: u["cost"])
    diff = (cost - ref["cost"]) / ref["cost"]
    rl_s = [mean(mf[k]["cost_all"]) for k in ks2]
    tt = H.welch_t(rl_s, ref["cost_all"])
    chk("six %s cost" % t, cost, want_cost[t])
    chk("six %s diff" % t, diff, want_diff[t], 1e-3)
    chk("six %s t" % t, tt, want_t[t], 0.02)
    chk("six %s ref" % t, ref["cost"], want_ref[t])

# 模型/规则倍数
mt = models("final")
ratio = [mean([mf[k]["cost"] for k in mf if k.startswith(t + "_s")])
         / mean([mt[k]["cost"] for k in mt if k.startswith(t + "_s")])
         for t in H.TARIFFS]
chk("model ratio min", min(ratio), 0.911, 2e-3)
chk("model ratio max", max(ratio), 0.918, 2e-3)

# setpoint
sp = load(os.path.join(OUT, "setpoint_freeze.json"))
for k, want in (("23", 9.020), ("24", 7.527), ("25", 6.057), ("26", 4.628),
                ("27", 3.562), ("28", 2.676), ("29", 1.971)):
    chk("setpoint %s" % k, sp[k]["energy"], want, 5e-3)
chk("25->26 drop", (sp["25"]["energy"] - sp["26"]["energy"]) / sp["25"]["energy"],
    0.236, 2e-3)
chk("24->25 drop", (sp["24"]["energy"] - sp["25"]["energy"]) / sp["24"]["energy"],
    0.195, 2e-3)

# 进出（t6 -> freeze）
s6 = set(u["name"] for u in fr("final", "t1_tou"))
sf = set(u["name"] for u in fr("freeze", "t1_tou"))
chk("common t6/freeze", len(s6 & sf), 24, 0)
chk("left", len(s6 - sf), 9, 0)
chk("new", len(sf - s6), 8, 0)

# 占用率统计（文档 §5）
def occ_stats(root, lo=9100, hi=9140):
    import subprocess
    return None

bad = [c for c in CHECKS if not c[0]]
print("checks: %d, failed: %d" % (len(CHECKS), len(bad)))
for ok, label, got, want in bad:
    print("  FAIL %-30s got=%.6f want=%.6f" % (label, got, want))
if not bad:
    print("ALL OK")
