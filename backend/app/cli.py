import argparse
import getpass
from datetime import date

from argon2 import PasswordHasher

from app.services.jobs import run_all_markets
from app.services.research_data_refresh import refresh
from app.services.screenshot_digitizer import digitize_all


def main() -> None:
    parser = argparse.ArgumentParser(description="杠杆 ETF 策略系统维护工具")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash-password")
    sub.add_parser("recompute")
    daily = sub.add_parser("update-desk")
    daily.add_argument("--sync-quotes", action="store_true", help="同步实盘账本估值行情，不写成交")
    daily.add_argument("--account", help="独立模拟账户配置文件")
    research = sub.add_parser("refresh-research")
    research.add_argument("--through", type=date.fromisoformat, help="指定已收盘的美股交易日，YYYY-MM-DD")
    digitize = sub.add_parser("digitize-screenshots")
    digitize.add_argument("directory")
    args = parser.parse_args()
    if args.command == "hash-password":
        print(PasswordHasher().hash(getpass.getpass("Password: ")))
    elif args.command == "recompute":
        print(run_all_markets())
    elif args.command == "update-desk":
        import json

        from app.services.daily_update import run
        result = run(sync_quotes=args.sync_quotes)
        if args.account:
            from pathlib import Path

            from app.services.shadow_account import update
            account_path = Path(args.account)
            from app.services.account_observation import capture
            from app.services.daily_update import _save
            from app.services.research_cockpit import _research_output

            receipt = _research_output() / "forward_data" / "daily_update.json"
            try:
                account = update(account_path, account_path.parent / "shadow.json")
                observation = capture(json.loads(account_path.read_text(encoding="utf8")), account)
                _save(receipt, {**{k: result[k] for k in ("status", "started_at", "completed_at", "data_date")},
                                "observation_version": observation["version"],
                                "recorded_at_utc": observation["audit"]["latest"]["recorded_at_utc"],
                                "signal_before_next_open": observation["audit"]["latest"]["signal_before_next_open"]})
            except Exception as exc:
                _save(receipt, {"status": "failed", "stage": "account_observation",
                                "data_date": result["data_date"], "message": str(exc)})
                raise
        print(json.dumps({k: result[k] for k in ("status", "data_date", "completed_at")}, ensure_ascii=False))
    elif args.command == "refresh-research":
        print(refresh(args.through))
    elif args.command == "digitize-screenshots":
        print(digitize_all(args.directory))


if __name__ == "__main__":
    main()
