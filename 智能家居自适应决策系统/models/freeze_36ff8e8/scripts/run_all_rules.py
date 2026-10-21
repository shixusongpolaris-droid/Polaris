# -*- coding: utf-8 -*-
"""并发驱动：3 个 env variant × 6 个电价格，各一个独立进程评估 941 条规则。"""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = r"D:\Dev\Projects\Polaris\智能家居自适应决策系统\.venv\Scripts\python.exe"
OUT = os.path.join(HERE, "out")
os.makedirs(OUT, exist_ok=True)

VARIANTS = os.environ.get("VARIANTS", "old,new_off,new").split(",")
TARIFFS = ["t1_flat", "t1_tou", "t2_flat", "t2_tou", "t3_flat", "t3_tou"]
MAXPROC = int(os.environ.get("MAXPROC", "12"))

env = dict(os.environ, PYTHONUTF8="1")
tasks = []
for v in VARIANTS:
    for t in TARIFFS:
        tasks.append((v, t))

running = []
t0 = time.perf_counter()
while tasks or running:
    while tasks and len(running) < MAXPROC:
        v, t = tasks.pop(0)
        log = open(os.path.join(OUT, "log_%s_%s.txt" % (v, t)), "w",
                   encoding="utf-8")
        p = subprocess.Popen(
            [PY, os.path.join(HERE, "run_rules.py"), "--variant", v,
             "--tariff", t,
             "--out", os.path.join(OUT, "rules_%s_%s.json" % (v, t))],
            stdout=log, stderr=subprocess.STDOUT, cwd=HERE, env=env)
        running.append((p, v, t, log))
        print("start %s/%s" % (v, t), flush=True)
    time.sleep(1.0)
    still = []
    for p, v, t, log in running:
        if p.poll() is None:
            still.append((p, v, t, log))
        else:
            log.close()
            print("done  %s/%s rc=%d  %.0fs" % (v, t, p.returncode,
                                                time.perf_counter() - t0),
                  flush=True)
    running = still
print("ALL DONE %.0fs" % (time.perf_counter() - t0))
