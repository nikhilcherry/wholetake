"""CLI entrypoint for simx: `serve` the simulated X server or `seed-clip` a video post."""
import argparse
import json
import os
import signal
import sys
import urllib.error
import urllib.request
from pathlib import Path

from simx.server import SimXServer
from simx.store import Store


def cmd_serve(args):
    store = Store(args.data)
    srv = SimXServer((args.host, args.port), store)
    host, port = srv.server_address
    print(f"simx listening on http://{host}:{port}  (data: {args.data})", flush=True)

    def shutdown(*_):
        srv.shutdown()

    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


def _http_call(url: str, token: str, data: bytes, content_type: str) -> dict:
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
        },
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def cmd_seed_clip(args):
    clip_path = Path(args.file)
    if not clip_path.is_file():
        print(f"ERROR: file not found: {clip_path}", file=sys.stderr)
        sys.exit(1)
    if len(args.text) > 280:
        print(f"ERROR: text exceeds 280 chars ({len(args.text)})", file=sys.stderr)
        sys.exit(1)

    base = (args.api_base or os.environ.get("X_API_BASE") or "http://127.0.0.1:8791").rstrip("/")
    token = f"sim-{args.user.lstrip('@')}"
    raw_video = clip_path.read_bytes()

    try:
        up = _http_call(
            f"{base}/2/media/upload?media_category=tweet_video",
            token=token,
            data=raw_video,
            content_type="video/mp4",
        )
        media_id = up["data"]["id"]
        tw = _http_call(
            f"{base}/2/tweets",
            token=token,
            data=json.dumps({"text": args.text, "media": {"media_ids": [media_id]}}).encode("utf-8"),
            content_type="application/json",
        )
        print(tw["data"]["id"], flush=True)
        return
    except (urllib.error.URLError, ConnectionRefusedError):
        # Fallback if server is not currently running: write directly into local store
        store = Store(args.data)
        user = store.user_by_username(args.user)
        if not user:
            print(f"ERROR: unknown user {args.user}", file=sys.stderr)
            sys.exit(1)
        m = store.save_media(raw_video)
        tw = store.create_tweet(author_id=user["id"], text=args.text, media_ids=[m["id"]])
        print(tw["id"], flush=True)


def main():
    ap = argparse.ArgumentParser(prog="python -m simx", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sp_serve = sub.add_parser("serve", help="Run the simulated X HTTP server")
    sp_serve.add_argument("--host", default="127.0.0.1")
    sp_serve.add_argument("--port", type=int, default=8791)
    sp_serve.add_argument("--data", default="data/simx")
    sp_serve.set_defaults(func=cmd_serve)

    sp_seed = sub.add_parser("seed-clip", help="Upload an mp4 clip and post a tweet with it")
    sp_seed.add_argument("file", help="Path to video/mp4 file")
    sp_seed.add_argument("--as", dest="user", default="clipper", help="Username to post as (default: clipper)")
    sp_seed.add_argument("--text", required=True, help="Tweet text (<= 280 chars)")
    sp_seed.add_argument("--data", default="data/simx", help="Fallback data directory if server is offline")
    sp_seed.add_argument("--api-base", default=None, help="Override X_API_BASE")
    sp_seed.set_defaults(func=cmd_seed_clip)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
