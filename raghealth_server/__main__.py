"""CLI: python -m raghealth_server {run|create-workspace|set-slack}"""
import argparse
import sys

from . import store


def main() -> int:
    p = argparse.ArgumentParser(prog="raghealth-server")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="start the server")
    r.add_argument("--host", default="0.0.0.0")
    r.add_argument("--port", type=int, default=8080)

    c = sub.add_parser("create-workspace", help="create a workspace + API key")
    c.add_argument("name")
    c.add_argument("--slack-webhook", help="Slack incoming webhook URL for alerts")

    s = sub.add_parser("set-slack", help="set/replace a workspace's Slack webhook")
    s.add_argument("name")
    s.add_argument("webhook")

    args = p.parse_args()
    if args.cmd == "run":
        import uvicorn
        uvicorn.run("raghealth_server.app:app", host=args.host, port=args.port)
        return 0
    conn = store.connect()
    if args.cmd == "create-workspace":
        ws = store.create_workspace(conn, args.name, args.slack_webhook)
        print(f"workspace:       {ws['name']}")
        print(f"API key:         {ws['api_key']}")
        print(f"dashboard path:  /d/{ws['dashboard_token']}")
        print("\nAgent config (raghealth.yaml):")
        print(f"  push:\n    url: http://YOUR-SERVER:8080\n"
              f"    api_key: env:RAGHEALTH_API_KEY\n    kb: production-docs")
        return 0
    if args.cmd == "set-slack":
        store.set_slack_webhook(conn, args.name, args.webhook)
        print(f"slack webhook set for {args.name}")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
