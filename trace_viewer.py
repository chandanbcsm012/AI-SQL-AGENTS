"""Filters logs/app.log by trace_id to reconstruct one request's full flow.

Usage: uv run python trace_viewer.py <trace_id>
"""
import json
import sys
from pathlib import Path

LOG_PATH = Path(__file__).parent / "logs" / "app.log"


def view_trace(trace_id: str) -> list[dict]:
    if not LOG_PATH.exists():
        return []
    lines = []
    for line in LOG_PATH.read_text().splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("trace_id") == trace_id:
            lines.append(entry)
    return lines


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: uv run python trace_viewer.py <trace_id>")
        sys.exit(1)

    for entry in view_trace(sys.argv[1]):
        print(
            f"[{entry.get('timestamp')}] {entry.get('step'):<20} "
            f"attempt={entry.get('attempt')} status={entry.get('status')} "
            f"latency_ms={entry.get('latency_ms')} error={entry.get('error')}"
        )
