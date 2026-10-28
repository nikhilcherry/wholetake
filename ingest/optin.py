"""Opt-in: a channel is witnessed only while its bio (or the live stream's description) carries its claim code.

    python -m ingest.optin claim "https://www.youtube.com/@somecreator"   # prints the code to paste in the bio
    python -m ingest.optin check "https://www.youtube.com/@somecreator/live"

The code is HMAC(secret, channel_id), so it needs no database and can't be guessed for someone else's
channel; only whoever controls the channel can put it in the bio. No install, nothing to log in to.
witness.py checks before every connect and once an hour while live; each check is appended to
<stream dir>/optin.jsonl so the record carries its own consent trail.
"""
import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BIN = Path(sys.executable).parent
KEY_FILE = Path(os.environ.get("WT_CLAIM_KEY_FILE", "data/claim.key"))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def secret():
    if not KEY_FILE.exists():
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.write(fd, secrets.token_hex(32).encode())
        os.close(fd)
    return KEY_FILE.read_bytes().strip()


def claim_code(channel_id):
    mac = hmac.new(secret(), f"wholetake-claim:{channel_id}".encode(), hashlib.sha256).digest()
    return "wholetake-" + base64.b32encode(mac).decode()[:8].lower()


def ytdlp_json(url, *extra):
    # -4: YouTube bot-checks this connection's IPv6 range; node: no deno here
    out = subprocess.run([str(BIN / "yt-dlp"), "-4", "--js-runtimes", "node", "-J", "--skip-download", *extra, url],
                         capture_output=True, text=True, timeout=120)
    if out.returncode != 0:
        raise RuntimeError(f"yt-dlp failed for {url}: {out.stderr.strip().splitlines()[-1:]}")
    return json.loads(out.stdout)


def resolve(url):
    """Channel identity + both places a code may live: the channel bio and (for a live URL) the stream description."""
    d = ytdlp_json(url, "--flat-playlist", "--playlist-items", "0")
    channel_id = d.get("channel_id") or d.get("id")
    if d.get("id") == channel_id:  # the URL was the channel itself
        bio, stream_desc = d.get("description") or "", ""
    else:
        stream_desc = d.get("description") or ""
        bio = ytdlp_json(f"https://www.youtube.com/channel/{channel_id}", "--flat-playlist", "--playlist-items", "0").get("description") or ""
    return {"channel_id": channel_id, "channel": d.get("channel") or d.get("title"), "bio": bio, "stream_description": stream_desc}


def check(url):
    info = resolve(url)
    code = claim_code(info["channel_id"])
    where = "bio" if code in info["bio"] else "stream_description" if code in info["stream_description"] else None
    return {"ok": where is not None, "where": where, "code": code, "channel_id": info["channel_id"],
            "channel": info["channel"], "url": url, "checked_utc": utc_now()}


def log_check(stream_dir, result):
    with open(Path(stream_dir) / "optin.jsonl", "a") as f:
        f.write(json.dumps(result, sort_keys=True) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["claim", "check"])
    ap.add_argument("url")
    a = ap.parse_args()
    if a.action == "claim":
        info = resolve(a.url)
        print(f"{info['channel']} ({info['channel_id']})\nadd this to the channel description: {claim_code(info['channel_id'])}")
        return
    r = check(a.url)
    print(f"{'OPTED IN' if r['ok'] else 'NOT OPTED IN'}: {r['channel']} ({r['channel_id']}) code {r['code']}"
          + (f" found in {r['where']}" if r["ok"] else " not found in bio or stream description"))
    sys.exit(0 if r["ok"] else 1)


if __name__ == "__main__":
    main()
