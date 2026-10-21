# -*- coding: utf-8 -*-
"""回流核对：把文档引用的关键聚合数字与 out/*.json 对齐。"""
import json
import math
import os
import sys
from statistics import mean

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness as H  # noqa: E402

OUT = os.path.join(HERE, "out")
CHECKS = []


def load(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def rules(v, t):
    return load(os.path.join(OUT, "rules_%s_%s.json" % (v, t)))["rows"]


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
    chk("final front %s" % t, len(fr("final", t)), 20 if flat else 33, 0)
    chk("final_energy front %s" % t, len(fr("final_energy", t)),
        27 if flat else 38, 0)
    chk("final_c3off front %s" % t, len(fr("final_c3off", t)),
        20 if flat else 30, 0)

# 最大空档
g = H.max_gap(fr("final", "t1_tou"))
chk("gap lo", g["lo"], 0.1932, 5e-4)
chk("gap hi", g["hi"], 0.3077, 5e-4)
gf = H.max_gap(fr("final", "t1_flat"))
chk("flat gap lo", gf["lo"], 0.4772, 5e-4)
chk("flat gap hi", gf["hi"], 0.5980, 5e-4)

# 关键规则
d = {r["name"]: r for r in rules("final", "t1_tou")}
chk("全开25 cost", d["全开 25°C"]["cost"], 3.6718)
chk("全开25 energy", d["全开 25°C"]["energy"], 6.3676)
chk("全开26 cost", d["全开 26°C"]["cost"], 2.8675)
chk("全开26 energy", d["全开 26°C"]["energy"], 4.8747)
chk("C块22-23 cost", d["C 块[22-23] 睡26 预冷25 h0.8 p0.02"]["cost"], 3.4958)
chk("分时三档 cost", d["分时三档 谷25/平26/峰28"]["cost"], 1.7171)
d2 = {r["name"]: r for r in rules("final", "t1_flat")}
chk("t1flat 全开25 cost", d2["全开 25°C"]["cost"], 3.9288)
chk("t1flat C块 cost", d2["C 块[22-23] 睡26 预冷25 h0.8 p0.02"]["cost"], 3.6104)
chk("t1flat cheapest", min(r["cost"] for r in d2.values()), 1.5106)
# 0% 计数
for v, want in (("old", 4), ("final_energy", 4), ("final_c3off", 0),
                ("final", 0), ("final_noc4_noc3", 57)):
    n = sum(1 for r in rules(v, "t1_tou") if r["occ_discomfort"] <= 1e-12)
    chk("zero count %s" % v, n, want, 0)

# C3 统计
af = {r["name"]: r for r in rules("final", "t1_tou")}
ac = {r["name"]: r for r in rules("final_c3off", "t1_tou")}
ks = list(af)
dd = sorted(af[k]["occ_discomfort"] - ac[k]["occ_discomfort"] for k in ks)
de = sorted(af[k]["energy"] - ac[k]["energy"] for k in ks)
dc = sorted(af[k]["cost"] - ac[k]["cost"] for k in ks)
c3 = sorted(af[k]["c3_override_share"] for k in ks)
chk("c3 median", c3[len(c3) // 2], 0.0102, 5e-4)
chk("c3 max", c3[-1], 0.0123, 5e-4)
chk("c3 improved", sum(1 for x in dd if x < -1e-12), 695, 0)
chk("c3 worsened", sum(1 for x in dd if x > 1e-12), 206, 0)
chk("c3 unchanged", sum(1 for x in dd if abs(x) <= 1e-12), 40, 0)
chk("c3 d median", dd[len(dd) // 2], -0.0008, 5e-4)
chk("c3 d min", dd[0], -0.0074, 5e-4)
chk("c3 d max", dd[-1], 0.0049, 5e-4)
chk("c3 e median", de[len(de) // 2], 0.271, 5e-3)
chk("c3 e max", de[-1], 0.308, 5e-3)
chk("c3 cost median", dc[len(dc) // 2], 0.167, 5e-3)
chk("tmax final", max(af[k]["temp_max"] for k in ks), 30.30, 0.01)
chk("tmax c3off", max(ac[k]["temp_max"] for k in ks), 31.71, 0.01)
chk("n>31 final", sum(1 for k in ks if af[k]["temp_max"] > 31.0), 0, 0)
chk("n>31 c3off", sum(1 for k in ks if ac[k]["temp_max"] > 31.0), 941, 0)

# S1b
o = {r["name"]: r for r in rules("old", "t1_tou")}
fe = {r["name"]: r for r in rules("final_energy", "t1_tou")}
er = sorted(fe[k]["energy"] / o[k]["energy"] for k in o)
cr = sorted(fe[k]["cost"] / o[k]["cost"] for k in o)
chk("energy ratio median", er[len(er) // 2], 1.295, 5e-3)
chk("cost ratio median", cr[len(cr) // 2], 1.386, 5e-3)

# 六格
want = {"t1_flat": (-0.038, -0.69), "t1_tou": (0.147, 2.57),
        "t2_flat": (0.093, 2.09), "t2_tou": (0.075, 0.91),
        "t3_flat": (-0.008, -0.21), "t3_tou": (0.024, 0.58)}
ref_cost = {"t1_flat": 3.5753, "t1_tou": 3.4634, "t2_flat": 3.8650,
            "t2_tou": 3.8332, "t3_flat": 5.1772, "t3_tou": 5.3861}
mm = models("final")
for t in H.TARIFFS:
    ks2 = [k for k in mm if k.startswith(t + "_s")]
    dmean = mean([mm[k]["occ_discomfort"] for k in ks2])
    cmean = mean([mm[k]["cost"] for k in ks2])
    uq = fr("final", t)
    cands = [u for u in uq if u["occ_discomfort"] <= dmean + 1e-12]
    ref = min(cands, key=lambda u: u["cost"])
    diff = (cmean - ref["cost"]) / ref["cost"]
    rl_s = [mean(mm[k]["cost_all"]) for k in ks2]
    tt = H.welch_t(rl_s, ref["cost_all"])
    chk("six %s diff" % t, diff, want[t][0], 1e-3)
    chk("six %s t" % t, tt, want[t][1], 0.02)
    chk("six %s ref" % t, ref["cost"], ref_cost[t], 5e-4)

# 模型均值
rl_energy = {"t1_flat": 5.577, "t1_tou": 6.639, "t2_flat": 6.332,
             "t2_tou": 6.284, "t3_flat": 5.602, "t3_tou": 5.758}
for t, v in rl_energy.items():
    ks2 = [k for k in mm if k.startswith(t + "_s")]
    chk("model energy %s" % t, mean([mm[k]["energy"] for k in ks2]), v, 5e-4)

# 进出（t3 -> final）
s3 = set(u["name"] for u in fr("new", "t1_tou"))
sf = set(u["name"] for u in fr("final", "t1_tou"))
chk("common t3/final", len(s3 & sf), 20, 0)
chk("left t3/final", len(s3 - sf), 2, 0)
chk("new t3/final", len(sf - s3), 13, 0)
print("left:", sorted(s3 - sf))
so = set(u["name"] for u in fr("old", "t1_tou"))
chk("common old/final", len(so & sf), 5, 0)
chk("left old/final", len(so - sf), 7, 0)
chk("new old/final", len(sf - so), 28, 0)

bad = [c for c in CHECKS if not c[0]]
print("checks: %d, failed: %d" % (len(CHECKS), len(bad)))
for ok, label, got, wantv in bad:
    print("  FAIL %-28s got=%.6f want=%.6f" % (label, got, wantv))
if not bad:
    print("ALL OK")
