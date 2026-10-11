========================================================
 D:\Dev\Projects\SmartHome\scripts  —  入口脚本
========================================================

【内容说明】
  存放"直接运行"的脚本，把 src 里的模块串起来。
  这样做的好处：src 里是可复用的代码，scripts 里是具体任务的入口。

  计划包含：
    run_sim.py          跑仿真环境，验证 MDP 定义是否正确
    collect_data.py     从 ESP32 采集数据并写入 data\raw\
    train.py            训练强化学习模型
    evaluate.py         评估模型，与基线对比
    make_figures.py     生成论文/报告用的图表

【运行方式】
    cd /d D:\Dev\Projects\SmartHome
    .venv\Scripts\activate
    python scripts\train.py
========================================================
