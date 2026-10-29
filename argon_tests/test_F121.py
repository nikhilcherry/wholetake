import os
import shutil
import sys
import tempfile
import numpy as np
import soundfile as sf
from roomstamp import engine, wavio, wt_detect
from roomstamp.spec import FS

def test_restore_audio_early_cut_venue_t_bounds():
    d = tempfile.mkdtemp(prefix="wt_f121_")
    try:
        t0 = 1793430600
        feed_path = os.path.join(d, "feed.wav")
        wavio.write(feed_path, np.sin(2 * np.pi * 440 * np.arange(5 * FS) / FS) * 0.5)
        sl = wt_detect.SessionLog(
            sid="session_test",
            path=os.path.join(d, "session.jsonl"),
            header={},
            secs=[],
            t_start=t0,
            t_end=t0 + 5,
            key_id="00" * 8,
            params=engine.StampParams(mode="pa"),
            feed_path=feed_path,
            venue_name="Test Venue",
        )
        doc = {
            "layers": {
                "l1": {
                    "edits": [
                        {
                            "source": "l1",
                            "kind": "removed",
                            "clip_t": 0.5,
                            "removed_s": 0.3,
                            "venue_t_from": t0 + 0.5,
                            "venue_t_to": t0 + 0.8,
                        },
                        {
                            "source": "l1",
                            "kind": "removed",
                            "clip_t": 4.0,
                            "removed_s": 0.3,
                            "venue_t_from": t0 + 4.2,
                            "venue_t_to": t0 + 4.5,
                        },
                    ]
                }
            },
            "warnings_base": [],
        }
        out_json = os.path.join(d, "detection.json")
        wt_detect._restore_audio(doc, sl, out_json)
        edits = doc["layers"]["l1"]["edits"]
        for i, e in enumerate(edits):
            wav_path = os.path.join(d, e["restored_audio"])
            info = sf.info(wav_path)
            wav_dur = info.frames / info.samplerate
            vt0, vt1 = e["restored_audio_venue_t"]
            meta_dur = vt1 - vt0
            if vt0 < t0 or vt1 > t0 + 5.0:
                print(f"FAIL test_restore_audio_early_cut_venue_t_bounds: edit {i} bounds {[vt0, vt1]} outside session [{t0}, {t0 + 5.0}]")
                sys.exit(1)
            if abs(meta_dur - wav_dur) > 1e-4:
                print(f"FAIL test_restore_audio_early_cut_venue_t_bounds: edit {i} WAV duration is {wav_dur:.2f}s, but restored_audio_venue_t={[vt0, vt1]} spans {meta_dur:.2f}s")
                sys.exit(1)
        print("PASS test_restore_audio_early_cut_venue_t_bounds")
    finally:
        shutil.rmtree(d, ignore_errors=True)

if __name__ == "__main__":
    test_restore_audio_early_cut_venue_t_bounds()
