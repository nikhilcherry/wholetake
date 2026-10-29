"""Reproducer for F281: cli.read_log crashes with KeyError/TypeError on malformed JSONL records."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from roomstamp import cli


def main() -> None:
    tmp = tempfile.mkdtemp(prefix="test_F281_")
    try:
        log_path = os.path.join(tmp, "session_malformed.jsonl")
        records = [
            {"foo": "bar"},  # missing 'type'
            [1, 2, 3],  # non-dict JSON list
            "just a string",  # non-dict JSON string
            123,  # non-dict JSON int
            None,  # non-dict JSON null
            True,  # non-dict JSON bool
            {"type": "session", "t_start": 1000, "warmup": {}},  # missing warmup.t
            {"type": "session", "t_start": 1000, "warmup": "not-a-dict"},  # non-dict warmup
            {"type": "session", "t_start": 1000, "warmup": {"t": [1, 2]}},  # unhashable warmup.t
            {"type": "session", "t_start": 1000, "warmup": {"t": True}},  # bool warmup.t
            {"type": "session", "t_start": 1000, "warmup": {"t": 999, "anchored": False}},  # valid session
            {"type": "sec", "anchored": False},  # missing 't'
            {"type": "sec", "t": [1000]},  # unhashable 't'
            {"type": "sec", "t": False},  # bool 't'
            {"type": "sec", "t": "1000"},  # string 't'
            {"type": "sec", "t": 1000, "anchored": False},  # valid sec
        ]
        with open(log_path, "w", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec) + "\n")

        try:
            header, secs = cli.read_log(log_path)
        except Exception as e:
            print(f"FAIL test_read_log_malformed_jsonl: read_log crashed with {type(e).__name__} on malformed JSONL record: {e}")
            sys.exit(1)

        if not isinstance(header, dict) or header.get("t_start") != 1000:
            print(f"FAIL test_read_log_malformed_jsonl: unexpected header {header!r}")
            sys.exit(1)
        if set(secs.keys()) != {999, 1000}:
            print(f"FAIL test_read_log_malformed_jsonl: unexpected secs keys {set(secs.keys())!r}")
            sys.exit(1)

        print("PASS test_read_log_malformed_jsonl")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
