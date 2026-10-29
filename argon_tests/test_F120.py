import os
import secrets
import shutil
import sys
import tempfile
import numpy as np

from roomstamp import spec, wavio, wt_detect
from roomstamp.spec import FS

def main():
    tmp = tempfile.mkdtemp(prefix="f120_")
    try:
        key = secrets.token_bytes(32)
        kid = spec.key_id(key)
        key_file = os.path.join(tmp, "venue.key")
        with open(key_file, "w", encoding="utf-8") as f:
            f.write(key.hex())
        empty_venue = os.path.join(tmp, "empty_venue")
        os.makedirs(empty_venue, exist_ok=True)
        clip_path = os.path.join(tmp, "clip.wav")
        wavio.write(clip_path, 0.1 * np.sin(2 * np.pi * 440 * np.arange(3 * FS) / FS))

        doc = wt_detect.run(clip_path, venue_dir=empty_venue, key_file=key_file)
        venue_kid = doc["verdict"]["venue"]["key_id"]
        proof_kid = doc["proof"]["key_id"]
        if venue_kid != kid:
            print(f"FAIL f120_proof_key_id: verdict.venue.key_id={venue_kid!r}, expected {kid!r}")
            sys.exit(1)
        if proof_kid != kid:
            print(f"FAIL f120_proof_key_id: proof.key_id={proof_kid!r}, expected {kid!r}")
            sys.exit(1)
        print("PASS f120_proof_key_id")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

if __name__ == "__main__":
    main()
