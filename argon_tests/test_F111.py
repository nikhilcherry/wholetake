import os
import sys
from unittest.mock import patch

os.environ["WHOLETAKE_ASR"] = "none"

import numpy as np
from scipy.signal import resample_poly

from roomstamp.witness import wt_stage
from wholetake import audio, record


def main() -> int:
    name = "test_F111"
    try:
        rec_dir = os.path.join("testdata", "witness", "record")
        talk_wav = os.path.join("testdata", "witness", "talk.wav")
        if os.path.exists(os.path.join(rec_dir, "meta.json")) and os.path.exists(talk_wav):
            rec = record.load(rec_dir)
            w_audio = audio.decode(talk_wav)
            best_gap = None
            for a, b in zip(rec.words, rec.words[1:]):
                if a.end >= 8.0 and b.start <= rec.duration - 8.0:
                    gap_s = b.start - a.end
                    if best_gap is None or gap_s > best_gap[0]:
                        best_gap = (gap_s, a.end, b.start)
            if best_gap is not None and best_gap[0] > 0.6:
                _, t1, t2 = best_gap
                c1 = t1 + 0.02
                c2 = t2 - 0.02
                sr = audio.SR
                part1 = w_audio[int((c1 - 7.0) * sr):int(c1 * sr)]
                part2 = w_audio[int(c2 * sr):int((c2 + 7.0) * sr)]
                clip_16k = np.concatenate([part1, part2])
                clip_48k = resample_poly(clip_16k, 3, 1).astype(np.float32)
                res = wt_stage(clip_48k, rec_dir)
                assert res["verdict"] == "faithful", f"Expected faithful verdict, got {res['verdict']}"
                pts = [e for e in res["edits"] if e["kind"] == "pause_trim" and e["removed_s"] > 0.5]
                assert pts, f"Expected pause_trim edit with removed_s > 0.5, got {res['edits']}"
                summary_lo = res["summary"].lower()
                assert "nothing removed" not in summary_lo, (
                    f"wt_stage contradicts itself: edits={res['edits']} has removed_s>0.5s, "
                    f"but summary={res['summary']!r}"
                )
                assert "continuous span" not in summary_lo, (
                    f"wt_stage claims continuous span despite discontinuous pieces: summary={res['summary']!r}"
                )
                assert "pause" in summary_lo, f"Expected pause trim mentioned in summary, got {res['summary']!r}"

                # Also verify truly uncut faithful clip still reports continuous span
                uncut_16k = w_audio[int(15.0 * sr):int(25.0 * sr)]
                uncut_48k = resample_poly(uncut_16k, 3, 1).astype(np.float32)
                res_uncut = wt_stage(uncut_48k, rec_dir)
                assert res_uncut["verdict"] == "faithful"
                assert not res_uncut["edits"]
                assert res_uncut["summary"] == "Continuous span of the witness record, nothing removed."
                print(f"PASS {name}")
                return 0

        # Fallback unit test if prebuilt record is unavailable
        fake_match = {
            "verdict": "faithful",
            "record_id": "test",
            "clip_duration_s": 14.0,
            "tempo": 1.0,
            "pieces": [
                {"clip_start_s": 0.0, "clip_end_s": 7.21, "witness_start_s": 10.0, "witness_end_s": 17.21, "similarity": 0.9, "witness_text": "a"},
                {"clip_start_s": 7.21, "clip_end_s": 14.0, "witness_start_s": 18.82, "witness_end_s": 25.61, "similarity": 0.9, "witness_text": "b"},
            ],
            "gaps": [
                {"type": "pause_trim", "clip_at_s": 7.21, "witness_from_s": 17.21, "witness_to_s": 18.82, "removed_s": 1.61, "restored_text": ""}
            ],
            "foreign_insertions": [],
            "decisive_words_removed": [],
            "clip_transcript": "a b",
        }
        with patch("wholetake.match.match", return_value=fake_match), patch("roomstamp.witness._record") as mrec:
            mrec.return_value.meta = {"t_start_unix": 1700000000.0}
            res = wt_stage(np.zeros(48000, dtype=np.float32), "dummy")
            summary_lo = res["summary"].lower()
            assert "nothing removed" not in summary_lo, f"Self-contradictory summary: {res['summary']!r}"
            assert "continuous span" not in summary_lo, f"Self-contradictory summary: {res['summary']!r}"
            assert "pause" in summary_lo
        print(f"PASS {name}")
        return 0
    except Exception as e:
        print(f"FAIL {name}: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
