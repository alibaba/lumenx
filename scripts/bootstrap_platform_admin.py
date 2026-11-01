"""Create the first platform admin before invite-only registration is enabled."""

from __future__ import annotations

import argparse
import os
from getpass import getpass

from src.apps.platform.config import PlatformConfig
from src.apps.platform.service import PlatformError, PlatformService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True, help="sqlite:/// database URL")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password-env", default="LUMENX_BOOTSTRAP_ADMIN_PASSWORD")
    args = parser.parse_args()
    password = os.getenv(args.password_env) or getpass("Admin password (12+ chars): ")
    service = PlatformService(PlatformConfig(enabled=True, database_url=args.database, registration_invite_required=False))
    try:
        user = service.register(args.username, password, role="admin")
    except PlatformError as exc:
        parser.error(f"admin bootstrap failed: {exc.code}")
    print(f"created platform admin {user['username']} ({user['id']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
