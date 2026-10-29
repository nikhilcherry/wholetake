import json
import os
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import spec, wt_detect


def main():
    tmp = tempfile.mkdtemp(prefix="test_f238_")
    try:
        t0 = 1793430600
        log_path = os.path.join(tmp, f"session_{t0}.jsonl")
        with open(log_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "session",
                "spec": spec.SPEC_VERSION,
                "key_id": "0123456789abcdef",
                "t_start": t0,
                "fs": 48000,
                "block": 480,
                "params": {"mode": "pa"},
            }) + "\n")
            for s in range(40):
                f.write(json.dumps({
                    "type": "sec",
                    "t": t0 + s,
                    "round": 1000 + s // 3,
                    "anchored": True,
                    "randomness": "00" * 32,
                    "anchor": "11" * 32,
                    "dac_utc": t0 + s + 0.01,
                }) + "\n")

        sl = wt_detect.load_session_log(log_path, "Test Venue")
        clip = wt_detect.Clip(
            path=os.path.join(tmp, "clip.wav"),
            sha256="aa" * 32,
            n_bytes=1000,
            x=np.zeros(48000 * 20, dtype=np.float64),
            orig_rate=48000,
            orig_channels=1,
            codec="pcm_s16le",
            decoder="soundfile",
            pcm_sha256="bb" * 32,
            seed=1234,
        )

        doc = wt_detect._empty_doc(["wt-detect", "clip.wav"])
        doc["clip"] = {"duration_s": 20.0}
        doc["verdict"]["venue"] = {"name": "Test Venue", "key_id": sl.key_id, "session": sl.sid, "mode": "pa"}
        doc["layers"]["l1"] = wt_detect._stage(
            "present", "s2", "verified",
            recorded_at_unix=t0 + 1.2,
            venue_t_start=t0 + 1.2,
            venue_t_end=t0 + 21.2,
            content_match=True,
        )
        wt_t0 = t0 + 86400
        doc["layers"]["wt"] = wt_detect._stage(
            "present", "s4", "matched witness",
            recorded_at_unix=wt_t0 + 1.5,
            venue_t_start=wt_t0 + 1.5,
            venue_t_end=wt_t0 + 21.5,
            content_match=True,
        )
        wt_detect.fuse(doc)
        proof = wt_detect._proof(sl, doc, clip)
        mtr = proof["matched_t_range"]
        if mtr is None:
            print("FAIL cross_session_splice_proof_range_explosion: matched_t_range is None")
            sys.exit(1)
        span = mtr[1] - mtr[0]
        if span > 40 or mtr[0] < sl.t_start or mtr[1] > sl.t_end:
            print(f"FAIL cross_session_splice_proof_range_explosion: _proof() produced {span}s matched_t_range {mtr} outside session [{sl.t_start}, {sl.t_end}]")
            sys.exit(1)
        if mtr != [t0 + 1, t0 + 22]:
            print(f"FAIL cross_session_splice_proof_range_explosion: unexpected matched_t_range {mtr}")
            sys.exit(1)
        print("PASS cross_session_splice_proof_range_explosion")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
