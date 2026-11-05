from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="使用 SQLite backup API 一致性备份 NAS Web 数据库")
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if not args.source.is_file():
        raise SystemExit(f"源数据库不存在: {args.source}")
    args.destination.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(args.source)
    destination = sqlite3.connect(args.destination)
    try:
        with destination:
            source.backup(destination)
    finally:
        destination.close()
        source.close()
    print(f"数据库备份完成: {args.destination}")


if __name__ == "__main__":
    main()
