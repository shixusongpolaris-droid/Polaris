"""生成演示用的实时工单（只运行一次，已有工单时跳过）。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.coordinator import WorkOrderCoordinator  # noqa: E402
from app.repository import Repository  # noqa: E402


def add(coord: WorkOrderCoordinator, desc: str, reporter: str, building: str, unit: str,
        created: datetime, accept_after: int | None = None, complete_after: int | None = None,
        photos: int = 0, note: str = "") -> str:
    trace = coord.submit(desc, reporter_name=reporter, building=building, unit=unit, now=created)
    ticket_id = trace["ticket"]["id"]
    if accept_after is not None:
        coord.accept(ticket_id, now=created + timedelta(minutes=accept_after))
    if complete_after is not None:
        coord.complete(ticket_id, result_note=note, photos_count=photos,
                       now=created + timedelta(minutes=complete_after))
    return ticket_id


def main() -> None:
    repo = Repository()
    if repo.stats_live()["total"] > 0:
        print("已存在实时工单，跳过种子数据。")
        return
    coord = WorkOrderCoordinator(repo)
    now = datetime.now().replace(second=0, microsecond=0)

    add(coord, "3栋2单元电梯门关不上，有人被困在轿厢里，老人和孩子都在里面", "李女士",
        "3栋", "2单元", now - timedelta(hours=7), accept_after=4, complete_after=18, photos=2,
        note="已救援被困人员，电梯门机复位，现场照片2张，困人救援记录已上传。")

    add(coord, "5栋1单元水管爆裂，楼道积水已经流到电梯口", "王女士",
        "5栋", "1单元", now - timedelta(hours=4), accept_after=26, complete_after=38, photos=0,
        note="已更换爆裂阀门，关闭总阀。")

    add(coord, "8栋2单元配电箱跳闸，整层停电，冰箱也停了", "张先生",
        "8栋", "2单元", now - timedelta(minutes=50), accept_after=9, photos=0)

    add(coord, "12栋3单元楼道灯不亮，晚上上下楼看不见", "陈阿姨",
        "12栋", "3单元", now - timedelta(minutes=25))

    add(coord, "9栋1单元门口垃圾堆积，异味很大", "赵先生",
        "9栋", "1单元", now - timedelta(hours=6), accept_after=12, complete_after=70, photos=2,
        note="已清理垃圾并完成消杀，现场照片2张，异味消除。")

    add(coord, "7栋1单元卫生间地漏不下水，地面反水", "周女士",
        "7栋", "1单元", now - timedelta(hours=5), accept_after=15, complete_after=95, photos=2,
        note="已疏通下水管道并清理返水，排水恢复正常，现场照片2张。")

    add(coord, "6栋2单元门禁刷卡没反应，单元门打不开", "孙先生",
        "6栋", "2单元", now - timedelta(minutes=40))

    add(coord, "4栋1单元家里闻到很重的煤气味，请马上来处理", "刘女士",
        "4栋", "1单元", now - timedelta(minutes=16))

    # 对在途工单做一次巡检，生成真实预警
    coord.scan(now)
    stats = repo.stats_live()
    print(f"已生成演示实时工单 {stats['total']} 条：待处理 {stats['open']}，已闭环 {stats['closed']}，"
          f"退回 {stats['returned']}，超时 {stats['timeout']}。")


if __name__ == "__main__":
    main()
