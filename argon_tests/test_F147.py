import hashlib
import io
import json
import sys
import urllib.request
from unittest.mock import patch

from roomstamp import drand


def make_resp(payload):
    b = json.dumps(payload).encode("utf-8")
    f = io.BytesIO(b)
    f.__enter__ = lambda *a: f
    f.__exit__ = lambda *a: False
    return f


def main():
    sig = b"\x01\x02\x03\x04" * 12
    sig_hex = sig.hex()
    good_rand = hashlib.sha256(sig).hexdigest()

    # 1. Valid payload succeeds
    good_payload = {"round": 100, "randomness": good_rand, "signature": sig_hex}
    with patch.object(urllib.request, "urlopen", side_effect=lambda *a, **k: make_resp(good_payload)):
        res = drand.fetch_round(100)
        if res is None or res.get("randomness") != good_rand:
            print("FAIL f147_drand_verify: valid beacon rejected")
            sys.exit(1)
        res_latest = drand.fetch_round("latest")
        if res_latest is None or res_latest.get("round") != 100:
            print("FAIL f147_drand_verify: valid latest beacon rejected")
            sys.exit(1)

    # 2. Mismatched SHA256(signature) != randomness is rejected
    bad_sig_payload = {"round": 100, "randomness": "00" * 32, "signature": sig_hex}
    with patch.object(urllib.request, "urlopen", side_effect=lambda *a, **k: make_resp(bad_sig_payload)):
        if drand.fetch_round(100) is not None:
            print("FAIL f147_drand_verify: accepted beacon where SHA256(signature) != randomness")
            sys.exit(1)

    # 3. Missing signature is rejected
    no_sig_payload = {"round": 100, "randomness": good_rand}
    with patch.object(urllib.request, "urlopen", side_effect=lambda *a, **k: make_resp(no_sig_payload)):
        if drand.fetch_round(100) is not None:
            print("FAIL f147_drand_verify: accepted beacon without signature")
            sys.exit(1)

    # 4. Mismatched round number is rejected
    wrong_rnd_payload = {"round": 99, "randomness": good_rand, "signature": sig_hex}
    with patch.object(urllib.request, "urlopen", side_effect=lambda *a, **k: make_resp(wrong_rnd_payload)):
        if drand.fetch_round(100) is not None:
            print("FAIL f147_drand_verify: accepted beacon with mismatched round")
            sys.exit(1)

    # 5. Non-hex or wrong-length randomness is rejected
    bad_hex_payload = {"round": 100, "randomness": "not_hex", "signature": sig_hex}
    with patch.object(urllib.request, "urlopen", side_effect=lambda *a, **k: make_resp(bad_hex_payload)):
        if drand.fetch_round(100) is not None:
            print("FAIL f147_drand_verify: accepted non-hex randomness")
            sys.exit(1)

    print("PASS f147_drand_verify")


if __name__ == "__main__":
    main()
