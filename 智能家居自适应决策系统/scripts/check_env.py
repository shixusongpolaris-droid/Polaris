r"""
环境自检脚本 —— 确认 Python 环境装对了、能用

用途：
    在虚拟环境里运行本脚本，检查关键依赖是否都已正确安装。

运行方式（命令行）：
    cd /d "D:\Dev\Projects\Polaris\智能家居自适应决策系统"
    .venv\Scripts\activate
    python scripts\check_env.py

如果中文显示为乱码，先执行：
    chcp 65001
或直接设环境变量：
    set PYTHONUTF8=1
"""

import sys
import platform

# 强制标准输出使用 UTF-8，避免 Windows 终端中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass


def main():
    print("=" * 56)
    print(" 环境自检 —— 智能家居自适应决策系统")
    print("=" * 56)

    # ---------- 1. 解释器信息 ----------
    print("\n[1] Python 解释器")
    print(f"    版本   : {sys.version.split()[0]}")
    print(f"    路径   : {sys.executable}")
    print(f"    系统   : {platform.system()} {platform.release()} ({platform.machine()})")

    # 检查是否在虚拟环境里
    in_venv = sys.prefix != sys.base_prefix
    print(f"    虚拟环境: {'是' if in_venv else '否  <-- 注意：建议在 .venv 里运行'}")
    if not in_venv:
        print("           激活方法: .venv\\Scripts\\activate")

    # ---------- 2. 逐个检查依赖包 ----------
    print("\n[2] 依赖包检查")

    # (导入名, 显示名, 用途)
    packages = [
        ("numpy",             "numpy",             "数值计算基础"),
        ("pandas",            "pandas",            "表格与时间序列"),
        ("matplotlib",        "matplotlib",        "绘图"),
        ("sklearn",           "scikit-learn",      "数据预处理与指标"),
        ("gymnasium",         "gymnasium",         "强化学习环境接口"),
        ("stable_baselines3", "stable-baselines3", "强化学习算法库"),
        ("torch",             "torch (PyTorch)",   "深度学习后端"),
        ("paho.mqtt",         "paho-mqtt",         "MQTT 通信"),
        ("serial",            "pyserial",          "串口通信"),
        ("jupyter",           "jupyter",           "Notebook 环境"),
    ]

    ok, missing = 0, []
    for mod_name, show_name, purpose in packages:
        try:
            mod = __import__(mod_name)
            ver = getattr(mod, "__version__", "版本未知")
            print(f"    [OK]   {show_name:<20} {ver:<12} {purpose}")
            ok += 1
        except ImportError:
            print(f"    [缺失] {show_name:<20} {'':<12} {purpose}")
            missing.append(mod_name)

    # ---------- 3. 汇总 ----------
    print("\n[3] 汇总")
    print(f"    已安装: {ok} / {len(packages)}")

    if missing:
        print(f"    缺失  : {', '.join(missing)}")
        print("\n    补装命令（在项目目录下运行）：")
        print("        .venv\\Scripts\\activate")
        print("        python -m pip install -r requirements.txt")
    else:
        print("    全部就绪，可以开始写代码了。")

    # ---------- 4. 下一步提示 ----------
    print("\n[4] 建议的下一步")
    print("    1. 硬件到货前：用假数据把数据管道和 MDP 环境框架搭好")
    print("    2. 先做基线（规则控制），再有对比基准去衡量强化学习的效果")
    print("    3. 参考 requirements.txt 末尾的说明安装仿真环境（可选）")
    print("=" * 56)


if __name__ == "__main__":
    main()
