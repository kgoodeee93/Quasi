"""Command line.

  python -m quasi connect google          one-time Google sign-in (Calendar + Gmail)
  python -m quasi ask "what's on my calendar today?"
  python -m quasi chat                    multi-turn conversation
  python -m quasi brief [--email]         morning brief; --email sends it to your own inbox
"""
from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    from .agents import build_agent
    from .agents.approvals import cli_approver
    from .config import Settings
    from .integrations.google_auth import GoogleAuthError

    if not argv or argv[0] in {"-h", "--help", "help"}:
        print(__doc__)
        return 0
    settings = Settings.load()
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "connect" and rest[:1] == ["google"]:
            from .integrations.gcal import GoogleCalendar
            from .integrations.gmail import Gmail
            cal = GoogleCalendar(settings)
            cal.connect()
            print(f"Google connected as {Gmail(settings).me} (calendar timezone {cal.tz}). "
                  f"Token saved to {settings.google_token}.")
            return 0
        if cmd == "brief":
            from .agents import run_brief
            brief, path, sent = run_brief(settings, email="--email" in rest)
            print(brief.text)
            print(f"\n(saved to {path}{'; emailed to ' + sent['to'] if sent else ''})")
            return 0
        agent = build_agent(settings, approver=cli_approver)
        if cmd == "ask" and rest:
            print(agent.ask(" ".join(rest)).text)
            return 0
        if cmd == "chat":
            history = None
            print("Quasi. Ask about your calendar or email; empty line to quit.")
            while (q := input("\nyou › ").strip()):
                res = agent.ask(q, history)
                history = res.messages
                print(f"\nquasi › {res.text}")
            return 0
    except GoogleAuthError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(__doc__)
    return 1


def cli() -> None:  # console-script entry point: `quasi ask "..."`
    sys.exit(main(sys.argv[1:]))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
