#!/usr/bin/env python3
"""Claude plan limits as JSON lines every 60s, from the endpoint behind Claude Code's /usage.

Reads the OAuth token Claude Code keeps in ~/.claude/.credentials.json on every poll and never
refreshes it (that would rotate the refresh token under Claude Code). On failure the last good
numbers are repeated with an "error": login (token missing/expired) or offline.
"""

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
URL = "https://api.anthropic.com/api/oauth/usage"
INTERVAL = 60


def window(data: dict, key: str, kind: str) -> dict | None:
    limit = data.get(key) or {}
    if limit.get("utilization") is None:
        return None
    severity = next((item.get("severity") for item in data.get("limits") or [] if item.get("kind") == kind), "normal")
    resets = limit.get("resets_at")
    return {
        "pct": round(limit["utilization"]),
        "resets": int(datetime.fromisoformat(resets).timestamp()) if resets else 0,
        "severity": severity or "normal",
    }


def parse(data: dict) -> dict:
    rows = (data.get("seven_day_breakdown") or {}).get("rows") or []
    return {
        "session": window(data, "five_hour", "session"),
        "week": window(data, "seven_day", "weekly_all"),
        "sources": [[row["display_name"], row["percent"]] for row in rows if row.get("percent")],
    }


def poll() -> dict:
    try:
        oauth = json.loads(CREDENTIALS.read_text())["claudeAiOauth"]
    except (OSError, ValueError, KeyError):
        return {"error": "login"}
    if oauth.get("expiresAt", 0) / 1000 < time.time():
        return {"error": "login", "plan": oauth.get("subscriptionType", "")}
    request = urllib.request.Request(URL, headers={
        "Authorization": f"Bearer {oauth['accessToken']}",
        "anthropic-beta": "oauth-2025-04-20",
        "User-Agent": "fabric-bar",
    })
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            data = json.load(response)
    except urllib.error.HTTPError as error:
        return {"error": "login" if error.code in (401, 403) else "offline"}
    except (OSError, ValueError):
        return {"error": "offline"}
    return {**parse(data), "plan": oauth.get("subscriptionType", ""), "at": int(time.time()), "error": ""}


def main() -> None:
    last: dict = {}
    while True:
        last = {**last, **poll()}
        print(json.dumps(last), flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
