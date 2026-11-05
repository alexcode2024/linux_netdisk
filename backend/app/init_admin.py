from __future__ import annotations

import argparse
import getpass
import os

from sqlalchemy import select

from .config import load_settings
from .database import SessionLocal, User, init_db
from .security import hash_password


def main() -> None:
    parser = argparse.ArgumentParser(description="初始化或重置 NAS 管理员账户")
    parser.add_argument("--username", default=os.environ.get("ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
    args = parser.parse_args()
    load_settings()
    password = args.password or getpass.getpass("管理员密码（不会显示）: ")
    if len(password) < 12:
        raise SystemExit("管理员密码至少需要 12 个字符")
    init_db()
    db = SessionLocal()
    try:
        user = db.scalar(select(User).where(User.username == args.username))
        if user is None:
            user = User(username=args.username, password_hash=hash_password(password), is_active=True)
            db.add(user)
        else:
            user.password_hash = hash_password(password)
            user.is_active = True
        db.commit()
        print(f"管理员账户已保存: {args.username}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
