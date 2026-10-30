import sys
import time
from unittest.mock import patch
import numpy as np
from roomstamp import detect, engine, wt_detect
from roomstamp.session import Session
from roomstamp.spec import FS


def test_run_l1_minimal_no_content_match():
    x = np.zeros(10 * FS, dtype=np.float64)
    clip = wt_detect.Clip(
        path="dummy.wav",
        sha256="00" * 32,
        n_bytes=1000,
        x=x,
        orig_rate=FS,
        orig_channels=1,
        codec="pcm_s16le",
        decoder="soundfile",
        pcm_sha256="11" * 32,
        seed=1234,
    )
    sess = Session(
        key_id="00" * 8,
        t_start=1793430600,
        params=engine.StampParams(mode="pa"),
        feed="",
    )
    fake_r = {
        "lag_samples": 999 * FS,
        "speech_removed_db": 0.5,
        "l1": {"clip_z": 8.0, "frac_z5": 0.08, "n_win": 10},
    }
    with patch.object(detect, "detect", return_value=fake_r), patch.object(detect, "verdict", return_value="inconclusive"):
        st = wt_detect._run_l1_minimal(clip, sess, b"\x00" * 32, x, time.time())
    if st["status"] != "inconclusive":
        return False, f"expected status=inconclusive, got {st['status']}"
    if st["content_match"] is not False:
        return False, f"expected content_match=False, got {st['content_match']}"
    if st["recorded_at_unix"] is not None or st["venue_t_start"] is not None or st["venue_t_end"] is not None:
        return False, f"expected None timestamps when content_match=False, got recorded_at_unix={st['recorded_at_unix']}, vt0={st['venue_t_start']}, vt1={st['venue_t_end']}"
    return True, ""


def test_fuse_inconclusive_l1_without_content_match():
    doc = wt_detect._empty_doc([])
    doc["clip"] = {"duration_s": 15.0}
    doc["layers"]["l1"] = wt_detect._stage(
        "inconclusive", "s1-minimal", "weak",
        recorded_at_unix=1793429601.0,
        venue_t_start=1793429601.0,
        venue_t_end=1793429616.0,
        content_match=False,
    )
    doc["layers"]["wt"] = wt_detect._stage(
        "present", "s4", "matched",
        recorded_at_unix=1793430612.0,
        venue_t_start=1793430612.0,
        venue_t_end=1793430627.0,
        content_match=True,
    )
    wt_detect.fuse(doc)
    v = doc["verdict"]
    if v["recorded_at_unix"] != 1793430612.0 or v["recorded_at_source"] != "wt":
        return False, f"fuse() took recorded_at_unix={v['recorded_at_unix']} (src={v['recorded_at_source']}) from L1 with content_match=False instead of matched witness (1793430612.0)"

    doc2 = wt_detect._empty_doc([])
    doc2["clip"] = {"duration_s": 15.0}
    doc2["layers"]["l1"] = wt_detect._stage(
        "inconclusive", "s1-minimal", "weak",
        recorded_at_unix=1793429601.0,
        content_match=False,
    )
    doc2["layers"]["l2"] = wt_detect._stage(
        "present", "s3", "locked",
        recorded_at_unix=1793430612.0,
        tempo=1.25,
    )
    wt_detect.fuse(doc2)
    v2 = doc2["verdict"]
    if v2["recorded_at_unix"] != 1793430612.0 or v2["recorded_at_source"] != "l2":
        return False, f"fuse() took recorded_at_unix={v2['recorded_at_unix']} from unmatched L1 instead of present L2"
    if any("l2_time_mismatch" in w for w in doc2["warnings"]):
        return False, f"unexpected l2_time_mismatch warning against unmatched L1: {doc2['warnings']}"
    if v2["tempo_ratio"] != 1.25:
        return False, f"expected tempo_ratio=1.25 from L2, got {v2['tempo_ratio']}"
    return True, ""


def main():
    failed = False
    for name, fn in [
        ("test_run_l1_minimal_no_content_match", test_run_l1_minimal_no_content_match),
        ("test_fuse_inconclusive_l1_without_content_match", test_fuse_inconclusive_l1_without_content_match),
    ]:
        ok, reason = fn()
        if ok:
            print(f"PASS {name}")
        else:
            print(f"FAIL {name}: {reason}")
            failed = True
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
