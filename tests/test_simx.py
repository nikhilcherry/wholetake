"""End-to-end contract, Range, rate-limit, persistence, CLI, and UI tests for simx."""
import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from simx.server import SimXServer
from simx.store import Store


@pytest.fixture()
def simx_env(tmp_path):
    os.environ["SIMX_QUIET"] = "1"
    data_dir = tmp_path / "simx_data"
    store = Store(data_dir)
    srv = SimXServer(("127.0.0.1", 0), store)
    port = srv.server_address[1]
    base_url = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield {"base": base_url, "data": data_dir, "srv": srv, "store": store}
    finally:
        srv.shutdown()
        srv.server_close()


def api_req(base: str, path: str, token: str | None = "sim-nikhil", method: str = "GET",
            body: bytes | dict | None = None, headers: dict | None = None):
    hdrs = {}
    if token is not None:
        hdrs["Authorization"] = f"Bearer {token}"
    if isinstance(body, dict):
        data = json.dumps(body).encode("utf-8")
        hdrs["Content-Type"] = "application/json"
    else:
        data = body
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(f"{base}{path}", data=data, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            raw = resp.read()
            ctype = resp.headers.get("Content-Type", "")
            parsed = json.loads(raw.decode("utf-8")) if "application/json" in ctype else raw
            return resp.status, parsed, dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        parsed = json.loads(raw.decode("utf-8")) if raw else {}
        return e.code, parsed, dict(e.headers)


def test_users_and_auth(simx_env):
    base = simx_env["base"]
    st, data, _ = api_req(base, "/2/users/me", token="sim-wholetake")
    assert st == 200
    assert data["data"]["username"] == "wholetake"

    st, data, _ = api_req(base, "/2/users/by/username/clipper", token="sim-nikhil")
    assert st == 200
    assert data["data"]["name"] == "ClipFarm 🔥"

    st, err, _ = api_req(base, "/2/users/me", token="bad-token")
    assert st == 401
    assert err["status"] == 401


def test_create_reply_quote_and_280_limit(simx_env):
    base = simx_env["base"]

    # Over 280 chars rejected with 400
    st, err, _ = api_req(base, "/2/tweets", token="sim-clipper", method="POST", body={"text": "x" * 281})
    assert st == 400
    assert err["title"] == "Invalid Request"

    # Root tweet
    st, root, _ = api_req(base, "/2/tweets", token="sim-clipper", method="POST", body={"text": "Breaking clip!"})
    assert st == 201
    root_id = root["data"]["id"]

    # Reply tweet
    st, rep, _ = api_req(
        base, "/2/tweets", token="sim-nikhil", method="POST",
        body={"text": "@wholetake is this real?", "reply": {"in_reply_to_tweet_id": root_id}},
    )
    assert st == 201
    rep_id = rep["data"]["id"]

    # Quote tweet
    st, qt, _ = api_req(
        base, "/2/tweets", token="sim-shreyas", method="POST",
        body={"text": "Look at this @wholetake", "quote_tweet_id": root_id},
    )
    assert st == 201
    qt_id = qt["data"]["id"]
    assert int(root_id) < int(rep_id) < int(qt_id)

    # Check conversation_id and referenced_tweets
    st, rep_full, _ = api_req(base, f"/2/tweets/{rep_id}?expansions=referenced_tweets.id,author_id")
    assert st == 200
    assert rep_full["data"]["conversation_id"] == root_id
    assert rep_full["data"]["referenced_tweets"] == [{"type": "replied_to", "id": root_id}]
    assert any(t["id"] == root_id for t in rep_full["includes"]["tweets"])


def test_mentions_paging_and_expansions_with_media_and_range(simx_env):
    base = simx_env["base"]
    _, wt_user, _ = api_req(base, "/2/users/by/username/wholetake")
    wt_id = wt_user["data"]["id"]

    # Empty mentions has no "data" key
    st, empty_m, _ = api_req(base, f"/2/users/{wt_id}/mentions")
    assert st == 200
    assert "data" not in empty_m
    assert empty_m["meta"]["result_count"] == 0

    # Upload media as clipper
    fake_mp4 = b"\x00\x00\x00\x18ftypmp42" + b"ABCDEF0123456789" * 64
    st, up, _ = api_req(
        base, "/2/media/upload?media_category=tweet_video", token="sim-clipper",
        method="POST", body=fake_mp4, headers={"Content-Type": "video/mp4"},
    )
    assert st == 200
    media_id = up["data"]["id"]
    media_key = up["data"]["media_key"]

    # Clipper posts video
    st, clip_tw, _ = api_req(
        base, "/2/tweets", token="sim-clipper", method="POST",
        body={"text": "Minister speech clip", "media": {"media_ids": [media_id]}},
    )
    assert st == 201
    clip_tid = clip_tw["data"]["id"]

    # Wholetake mentioning itself should NOT appear in its own mentions
    api_req(base, "/2/tweets", token="sim-wholetake", method="POST", body={"text": "I am @wholetake"})

    # Nikhil replies mentioning @Wholetake (case-insensitive)
    _, m1, _ = api_req(
        base, "/2/tweets", token="sim-nikhil", method="POST",
        body={"text": "hey @WholeTake check this", "reply": {"in_reply_to_tweet_id": clip_tid}},
    )
    m1_id = m1["data"]["id"]

    # Shreyas posts another mention
    _, m2, _ = api_req(
        base, "/2/tweets", token="sim-shreyas", method="POST",
        body={"text": "@wholetake second mention", "reply": {"in_reply_to_tweet_id": clip_tid}},
    )
    m2_id = m2["data"]["id"]

    exp = "author_id,referenced_tweets.id,attachments.media_keys,referenced_tweets.id.attachments.media_keys"
    st, mentions, _ = api_req(
        base, f"/2/users/{wt_id}/mentions?expansions={exp}&media.fields=type,duration_ms,variants",
        token="sim-wholetake",
    )
    assert st == 200
    assert mentions["meta"]["result_count"] == 2
    assert mentions["meta"]["newest_id"] == m2_id
    assert mentions["meta"]["oldest_id"] == m1_id
    assert [x["id"] for x in mentions["data"]] == [m2_id, m1_id]

    inc_media = mentions["includes"]["media"]
    assert len(inc_media) == 1
    assert inc_media[0]["media_key"] == media_key
    video_url = inc_media[0]["variants"][0]["url"]

    # Download full media via variants.url
    with urllib.request.urlopen(video_url) as resp:
        assert resp.status == 200
        assert resp.read() == fake_mp4

    # HTTP Range request returns 206 with exact slice
    req = urllib.request.Request(video_url, headers={"Range": "bytes=8-23"})
    with urllib.request.urlopen(req) as resp:
        assert resp.status == 206
        assert resp.headers.get("Content-Range") == f"bytes 8-23/{len(fake_mp4)}"
        assert resp.read() == fake_mp4[8:24]

    # since_id paging returns only newer than m1_id
    st, paged, _ = api_req(base, f"/2/users/{wt_id}/mentions?since_id={m1_id}", token="sim-wholetake")
    assert st == 200
    assert [x["id"] for x in paged["data"]] == [m2_id]

    # since_id at newest returns empty without "data"
    st, paged_empty, _ = api_req(base, f"/2/users/{wt_id}/mentions?since_id={m2_id}", token="sim-wholetake")
    assert st == 200
    assert "data" not in paged_empty


def test_rate_limit_429(tmp_path):
    store = Store(tmp_path / "rl_data")
    srv = SimXServer(("127.0.0.1", 0), store, rate_limit=3)
    port = srv.server_address[1]
    base = f"http://127.0.0.1:{port}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        for expected_rem in ("2", "1", "0"):
            st, _, hdrs = api_req(base, "/2/users/me", token="sim-nikhil")
            assert st == 200
            assert hdrs.get("x-rate-limit-limit") == "3"
            assert hdrs.get("x-rate-limit-remaining") == expected_rem
            assert "x-rate-limit-reset" in hdrs

        st, err, hdrs = api_req(base, "/2/users/me", token="sim-nikhil")
        assert st == 429
        assert err["status"] == 429
        assert hdrs.get("x-rate-limit-remaining") == "0"
    finally:
        srv.shutdown()
        srv.server_close()


def test_persistence_across_restart_and_seed_cli(simx_env, tmp_path):
    base = simx_env["base"]
    data_dir = simx_env["data"]

    clip_file = tmp_path / "sample.mp4"
    clip_file.write_bytes(b"\x00\x00\x00\x18ftypmp42" + b"VIDEO" * 50)

    out = subprocess.check_output(
        [sys.executable, "-m", "simx", "seed-clip", str(clip_file), "--as", "clipper", "--text", "Seeded clip!"],
        env={**os.environ, "X_API_BASE": base},
    ).decode().strip()
    assert out.isdigit()

    # Restart a fresh Store + SimXServer pointing at the same data_dir
    store2 = Store(data_dir)
    tw = store2.get_tweet(out)
    assert tw is not None
    assert tw["text"] == "Seeded clip!"
    mkey = tw["attachments"]["media_keys"][0]
    assert store2.media_path(mkey).read_bytes() == clip_file.read_bytes()


def test_ui_self_contained_no_external_urls(simx_env):
    base = simx_env["base"]
    with urllib.request.urlopen(f"{base}/") as resp:
        assert resp.status == 200
        html = resp.read().decode("utf-8")
    assert "Who's checking clips" in html
    assert "http://" not in html
    assert "https://" not in html
    assert not re.search(r'(?:src|href)=["\']//', html)
