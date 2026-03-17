import sys
from pathlib import Path

from bt_api_py.functions.ib_web_session import (
    ensure_authenticated_session,
    load_ib_web_settings,
    upsert_env_file,
)

project_root = Path(__file__).parent.parent


def main() -> int:
    settings = load_ib_web_settings()
    result = ensure_authenticated_session()
    env_path = project_root / ".env"
    updates = {
        "IB_WEB_COOKIE_SOURCE": f"file:{settings['cookie_output_relative']}",
        "IB_WEB_COOKIE_BROWSER": "",
        "IB_WEB_COOKIE_OUTPUT": str(settings["cookie_output_relative"]),
    }
    account_id = str(result.get("account_id") or "")
    if account_id:
        updates["IB_WEB_ACCOUNT_ID"] = account_id
    upsert_env_file(env_path, updates)
    print(f"COOKIE_FILE {result['cookie_output']}")
    print(f"ACCOUNT_ID {account_id}")
    print(f"AUTH_STATUS {result['status_code']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
