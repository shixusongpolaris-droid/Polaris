"""初始化数据库：建表、写入维修工、导入历史工单，可选生成演示实时工单。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.config import DB_PATH, HISTORY_CSV  # noqa: E402
from app.repository import Repository  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-history", action="store_true", help="重新导入历史 CSV")
    parser.add_argument("--seed-live", action="store_true", help="没有实时工单时生成演示工单")
    parser.add_argument("--seed-community", action="store_true", help="没有社区/停车数据时生成演示数据")
    parser.add_argument("--reset", action="store_true", help="删除数据库后重建")
    args = parser.parse_args()

    if args.reset:
        db.reset_database()
    db.initialize(seed_history=True, force_history=args.force_history)
    repo = Repository()
    count = repo.history_count()
    if count == 0:
        print(f"警告：未导入历史工单，请先运行 python scripts/generate_data.py（CSV：{HISTORY_CSV}）")
    else:
        print(f"数据库：{DB_PATH}")
        print(f"历史工单：{count} 条；维修工：{len(repo.list_workers())} 人")
    if args.seed_live:
        import subprocess

        subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_live.py")], check=False)
    if args.seed_community:
        import subprocess

        subprocess.run([sys.executable, str(ROOT / "scripts" / "seed_community.py")], check=False)


if __name__ == "__main__":
    main()
