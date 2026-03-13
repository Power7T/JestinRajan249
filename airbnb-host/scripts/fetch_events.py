#!/usr/bin/env python3
"""
fetch_events.py — fetch local events for a given location and date range.
Used by the /price-tip command in the airbnb-host OpenClaw skill.

Usage:
    python3 fetch_events.py --location "Austin, TX" --dates "July 4-7 2025"

Outputs JSON to stdout:
    {"events": [...], "source": "serpapi|brave|none", "note": "..."}

Environment variables (checked in order, first found wins):
    SERPAPI_KEY   — SerpAPI Google Events search
    BRAVE_API_KEY — Brave Search API (fallback)

Neither key is required. If neither is set, returns empty events with a note.
"""

import os
import sys
import json
import argparse
import urllib.request
import urllib.parse


def fetch_via_serpapi(location: str, dates: str, key: str) -> dict:
    query = urllib.parse.quote(f"events in {location} {dates}")
    url = (
        f"https://serpapi.com/search.json"
        f"?engine=google_events&q={query}&api_key={key}"
    )
    with urllib.request.urlopen(url, timeout=10) as resp:
        data = json.loads(resp.read())
    events = [
        {
            "name": e.get("title"),
            "date": e.get("date", {}).get("when"),
            "venue": e.get("venue"),
        }
        for e in data.get("events_results", [])[:10]
    ]
    return {"events": events, "source": "serpapi"}


def fetch_via_brave(location: str, dates: str, key: str) -> dict:
    query = urllib.parse.quote(f"events {location} {dates}")
    url = f"https://api.search.brave.com/res/v1/web/search?q={query}&count=10"
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": key,
        },
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = json.loads(resp.read())
    results = data.get("web", {}).get("results", [])
    events = [
        {
            "name": r.get("title"),
            "url": r.get("url"),
            "description": r.get("description"),
        }
        for r in results[:8]
    ]
    return {"events": events, "source": "brave"}


def main():
    parser = argparse.ArgumentParser(
        description="Fetch local events for Airbnb pricing decisions"
    )
    parser.add_argument("--location", required=True, help="City and state/country")
    parser.add_argument("--dates", required=True, help="Date range or month")
    args = parser.parse_args()

    serpapi_key = os.environ.get("SERPAPI_KEY")
    brave_key = os.environ.get("BRAVE_API_KEY")

    try:
        if serpapi_key:
            result = fetch_via_serpapi(args.location, args.dates, serpapi_key)
        elif brave_key:
            result = fetch_via_brave(args.location, args.dates, brave_key)
        else:
            result = {
                "events": [],
                "source": "none",
                "note": (
                    "No API key set. Set SERPAPI_KEY or BRAVE_API_KEY for live "
                    "event data. Pricing recommendation will use AI training knowledge."
                ),
            }
    except Exception as e:
        result = {
            "events": [],
            "source": "error",
            "note": f"Event fetch failed: {e}. Falling back to AI training knowledge.",
        }

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
