import json
import shutil
import sys
import tempfile
from pathlib import Path

from verdict.service import VerdictManager, witness_heads_snapshot


def test_12_witness_heads_snapshot_stale_head_and_partial_chain() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="wt_test_f372_"))
    try:
        wroot = tmp / "witness"
        sdir = wroot / "stream_a"
        sdir.mkdir(parents=True, exist_ok=True)

        stale_hash = "37642b358f77fdc527c9d7d3487ee085e02e9cf419da04f5becafcc3bb3d47af"
        newer_hash = "b" * 64

        # 1. Stale head.json (seq 0) vs newer chain.jsonl (seq 1)
        (sdir / "head.json").write_text(
            json.dumps({"seq": 0, "hash": stale_hash}), encoding="utf-8"
        )
        chain_lines = [
            json.dumps({"seq": 0, "prev": "0" * 64, "hash": stale_hash}),
            json.dumps({"seq": 1, "prev": stale_hash, "hash": newer_hash}),
        ]
        (sdir / "chain.jsonl").write_bytes(("\n".join(chain_lines) + "\n").encode("utf-8"))

        snap1 = witness_heads_snapshot(wroot)
        if snap1.get("stream_a") != newer_hash:
            print(
                f"FAIL test_12_witness_heads_snapshot_stale_head_and_partial_chain: "
                f"witness_heads_snapshot returned stale head.json ({snap1.get('stream_a')!r} != {newer_hash!r})"
            )
            sys.exit(1)

        # 2. Partial/corrupt trailing line in chain.jsonl without head.json
        (sdir / "head.json").unlink()
        with (sdir / "chain.jsonl").open("ab") as f:
            f.write(b'{"seq": 2, "prev": "' + newer_hash.encode("ascii") + b'", "hash": "cccc')

        snap2 = witness_heads_snapshot(wroot)
        if snap2.get("stream_a") != newer_hash:
            print(
                f"FAIL test_12_witness_heads_snapshot_stale_head_and_partial_chain: "
                f"dropped stream on partial trailing line in chain.jsonl ({snap2!r}), poisoning VerdictManager cache!"
            )
            sys.exit(1)

        # 3. Stale head.json AND partial trailing line in chain.jsonl
        (sdir / "head.json").write_text(
            json.dumps({"seq": 0, "hash": stale_hash}), encoding="utf-8"
        )
        snap3 = witness_heads_snapshot(wroot)
        if snap3.get("stream_a") != newer_hash:
            print(
                f"FAIL test_12_witness_heads_snapshot_stale_head_and_partial_chain: "
                f"expected {newer_hash!r} with stale head.json + partial trailing line, got {snap3!r}"
            )
            sys.exit(1)

        # 4. Verify VerdictManager cache invalidates when chain.jsonl advances past stale head.json
        wroot2 = tmp / "witness2"
        sdir2 = wroot2 / "stream_b"
        sdir2.mkdir(parents=True, exist_ok=True)
        (sdir2 / "head.json").write_text(
            json.dumps({"seq": 0, "hash": stale_hash}), encoding="utf-8"
        )
        (sdir2 / "chain.jsonl").write_bytes(
            (json.dumps({"seq": 0, "prev": "0" * 64, "hash": stale_hash}) + "\n").encode("utf-8")
        )

        clip_wav = tmp / "clip.wav"
        clip_wav.write_bytes(b"RIFF\x24\x00\x00\x00WAVEfmt ")
        call_count = 0

        def fake_fetch(url: str) -> dict:
            return {
                "url": url,
                "wav": str(clip_wav),
                "sha256": "a" * 64,
                "duration_s": 10.0,
            }

        def fake_detect(wav_path, witness_root) -> dict:
            nonlocal call_count
            call_count += 1
            return {
                "detector": {"name": "wt-detect", "version": "0.1"},
                "clip": {"duration_s": 10.0, "sha256": "a" * 64},
                "stamp": None,
                "witness": {
                    "verdict": "not_found" if call_count == 1 else "faithful",
                    "stream": None if call_count == 1 else "stream_b",
                    "pieces": [],
                    "removed": [],
                    "inserted": [],
                    "transcript_clip": "hello",
                },
            }

        mgr = VerdictManager(
            witness_root=wroot2,
            verdicts_dir=tmp / "verdicts",
            fetch_fn=fake_fetch,
            detect_fn=fake_detect,
            start_worker=False,
        )
        try:
            j1 = mgr.submit(str(clip_wav), allow_local=True)["id"]
            mgr.process_one(j1)
            assert call_count == 1

            # Advance chain.jsonl with seq 1 + partial trailing line while head.json remains stale at seq 0
            with (sdir2 / "chain.jsonl").open("ab") as f:
                f.write((json.dumps({"seq": 1, "prev": stale_hash, "hash": newer_hash}) + "\n").encode("utf-8"))
                f.write(b'{"seq": 2, "hash": "partial')

            j2 = mgr.submit(str(clip_wav), allow_local=True)["id"]
            mgr.process_one(j2)
            if call_count != 2:
                print(
                    f"FAIL test_12_witness_heads_snapshot_stale_head_and_partial_chain: "
                    f"VerdictManager served stale cached result instead of re-detecting (call_count={call_count})"
                )
                sys.exit(1)
        finally:
            mgr.close()

        print("PASS test_12_witness_heads_snapshot_stale_head_and_partial_chain")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_12_witness_heads_snapshot_stale_head_and_partial_chain()
