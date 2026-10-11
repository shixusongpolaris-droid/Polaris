========================================================
 D:\Dev\Projects\Polaris\智能家居自适应决策系统
 ——  大创项目主目录
========================================================

项目名称：基于强化学习与多目标优化的智能家居自适应决策系统

说明：本项目是 GitHub 仓库 Polaris 的一个子项目。
仓库地址：https://github.com/shixusongpolaris-droid/Polaris
因此本目录的上级目录 Polaris\ 才是 git 仓库根目录，
在本目录内执行 git 命令时，git 会自动向上找到仓库根。

【目录内容】

  .venv\          本项目专用虚拟环境（Python 解释器和依赖包）
                  激活方法：.venv\Scripts\activate
                  删除此文件夹 = 彻底卸载本项目环境，不影响其他项目

  data\           数据（详见 data\README.txt）
  src\            源代码（详见 src\README.txt）
  scripts\        入口脚本：训练、评估、跑仿真
  notebooks\      探索性分析、画图（Jupyter）
  models\         训练好的模型文件、训练日志、实验结果

【硬件与软件环境】
  硬件（已下单）：
    - ESP32-S3-N16R8 开发板 ×1
    - SHT31 温湿度传感器（串口版）×1
    - BH1750 / GY-302 光照传感器 ×1
    - HC-SR501 PIR 人体活动传感器 ×2
    - LD2410C 毫米波雷达人体存在传感器 ×1
    - 830 孔面包板套件（含杜邦线、电源模块）×1

  传感器接线（ESP32-S3）：
    PIR #1        OUT → GPIO4
    PIR #2        OUT → GPIO5
    LD2410C       OUT → GPIO6
    BH1750        SDA → GPIO8, SCL → GPIO9
    SHT31         TX  → GPIO17, RX → GPIO18
    禁用引脚：GPIO26-37（Flash/PSRAM）、GPIO19/20（USB）、
              GPIO43/44（烧录串口）、GPIO0/3/45/46（strapping）

【研究路线】
  环境感知 → 数据建模（MDP）→ 智能决策（强化学习）→ 系统验证
========================================================
