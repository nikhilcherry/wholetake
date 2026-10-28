"""Opt-in: claim codes, and the witness only capturing while the code is up."""
import json
import subprocess
import threading
import time

import pytest

from ingest import optin, witness


@pytest.fixture(autouse=True)
def key(tmp_path, monkeypatch):
    monkeypatch.setattr(optin, "KEY_FILE", tmp_path / "claim.key")


def test_claim_code_is_stable_per_channel_and_secret(tmp_path, monkeypatch):
    a = optin.claim_code("UCaaa")
    assert a == optin.claim_code("UCaaa") and a.startswith("wholetake-")
    assert a != optin.claim_code("UCbbb")
    assert (tmp_path / "claim.key").stat().st_mode & 0o777 == 0o600
    monkeypatch.setattr(optin, "KEY_FILE", tmp_path / "other.key")
    assert optin.claim_code("UCaaa") != a  # someone without our secret can't mint codes


def test_check_finds_code_in_bio_or_stream_description(monkeypatch):
    code = optin.claim_code("UCx")
    for bio, desc, where in [(f"hi {code}", "", "bio"), ("hi", f"live! {code}", "stream_description"), ("hi", "", None)]:
        monkeypatch.setattr(optin, "resolve", lambda url, b=bio, d=desc: {"channel_id": "UCx", "channel": "X", "bio": b, "stream_description": d})
        r = optin.check("https://youtube.com/@x/live")
        assert r["where"] == where and r["ok"] == (where is not None)


@pytest.fixture
def stream(tmp_path):
    src = tmp_path / "long.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=f=440:d=60", str(src)], check=True)
    return src


def make(stream, tmp_path, answers, monkeypatch):
    """A witness on a local real-time 'stream' that asks a fake opt-in check, answering from `answers`."""
    calls = []

    def fake_check(url):
        calls.append(time.time())
        ok = answers[min(len(calls), len(answers)) - 1]
        return {"ok": ok, "where": "bio" if ok else None, "code": "wholetake-test", "url": url, "checked_utc": optin.utc_now()}

    monkeypatch.setattr(optin, "check", fake_check)
    w = witness.Witness(str(stream), "s", 2, tmp_path / "w")
    w.optin = True  # local files skip opt-in by default; force it on
    return w, calls


def test_never_connects_without_the_code(stream, tmp_path, monkeypatch):
    w, calls = make(stream, tmp_path, [False], monkeypatch)
    w.run(until=time.time() + 10)
    assert not list(w.dir.glob("*.wav"))
    assert [json.loads(l)["ok"] for l in (w.dir / "optin.jsonl").read_text().splitlines()] == [False]


def test_unreachable_check_does_not_start_a_fresh_witness(stream, tmp_path, monkeypatch):
    w, calls = make(stream, tmp_path, [None], monkeypatch)
    monkeypatch.setattr(witness, "BACKOFF", [1])
    w.run(until=time.time() + 4)
    assert not list(w.dir.glob("*.wav")) and len(calls) >= 2  # kept retrying the check, never captured


def test_stops_when_creator_removes_the_code(stream, tmp_path, monkeypatch):
    monkeypatch.setattr(witness, "OPTIN_EVERY_S", 5)
    w, calls = make(stream, tmp_path, [True, False], monkeypatch)
    t0 = time.time()
    w.run(until=time.time() + 40)
    took = time.time() - t0
    assert took < 15, f"kept witnessing {took:.0f}s after the code was removed"
    assert list(w.dir.glob("*.meta.json")), "should have captured while opted in"
    log = [json.loads(l)["ok"] for l in (w.dir / "optin.jsonl").read_text().splitlines()]
    assert log == [True, False]
