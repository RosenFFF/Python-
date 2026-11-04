"""启动一个独立数据目录的演示实例（用于浏览器验收和录制），不碰 data/ 下的正式数据。

用法：python tools/run_demo_server.py [--port 8100] [--fresh]
--fresh 会清空演示目录重新生成数据。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = ROOT / "data" / "demo-instance"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--fresh", action="store_true")
    args = parser.parse_args()
    if args.fresh and DEMO_DIR.exists():
        shutil.rmtree(DEMO_DIR)
    os.environ.update({
        "YIHU_ENV": "dev", "YIHU_DEMO_MODE": "true", "YIHU_DATA_DIR": str(DEMO_DIR / "data"),
        "YIHU_BACKUP_DIR": str(DEMO_DIR / "backups"), "YIHU_BCRYPT_ROUNDS": "6",
    })
    sys.path.insert(0, str(ROOT / "backend"))
    os.chdir(ROOT / "backend")

    import uvicorn

    from app.db.bootstrap import init_database
    from app.db.session import new_session
    from app.seed.demo_data import seed_demo
    from app.services import audit_service, backup_service

    init_database()
    db = new_session()
    result = seed_demo(db)
    db.commit()
    db.close()
    if result["seeded"]:
        backup_service.create_backup(audit_service.SYSTEM, "SEED")
    uvicorn.run("app.main:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
