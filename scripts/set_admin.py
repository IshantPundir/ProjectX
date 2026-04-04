#!/usr/bin/env python3
"""Set or unset the is_projectx_admin flag on a Supabase Auth user.

Usage:
    python scripts/set_admin.py <email>              # Set admin flag
    python scripts/set_admin.py <email> --revoke      # Remove admin flag

Requires SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY environment variables,
or a .env file at backend/nexus/.env.
"""

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

# Try loading .env from backend/nexus/.env
env_path = Path(__file__).parent.parent / "backend" / "nexus" / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())

SUPABASE_URL = os.environ.get("SUPABASE_URL", "http://127.0.0.1:54321")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def api(method: str, path: str, data: dict | None = None) -> dict:
    url = f"{SUPABASE_URL}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        url,
        method=method,
        data=body,
        headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def find_user(email: str) -> dict | None:
    result = api("GET", "/auth/v1/admin/users")
    for user in result.get("users", []):
        if user["email"] == email:
            return user
    return None


def main():
    parser = argparse.ArgumentParser(description="Set ProjectX admin flag on a user")
    parser.add_argument("email", help="User email address")
    parser.add_argument("--revoke", action="store_true", help="Remove admin flag instead of setting it")
    args = parser.parse_args()

    if not SERVICE_KEY:
        print("Error: SUPABASE_SERVICE_ROLE_KEY not set", file=sys.stderr)
        sys.exit(1)

    user = find_user(args.email)
    if not user:
        print(f"Error: No user found with email '{args.email}'", file=sys.stderr)
        sys.exit(1)

    flag = not args.revoke
    api("PUT", f"/auth/v1/admin/users/{user['id']}", {
        "app_metadata": {"is_projectx_admin": flag},
    })

    action = "granted" if flag else "revoked"
    print(f"Done: {args.email} → is_projectx_admin {action}")


if __name__ == "__main__":
    main()
