import sys
from unittest.mock import patch
import sounddevice as sd
from roomstamp.emitter import find_device

MOCK_DEVS = [
    {"name": "Mic Only", "hostapi": 0, "max_input_channels": 2, "max_output_channels": 0},
    {"name": "Speakers Only", "hostapi": 0, "max_input_channels": 0, "max_output_channels": 2},
]
MOCK_APIS = [
    {"name": "Windows WASAPI", "default_input_device": 0, "default_output_device": 1}
]

def check(name: str, fn):
    try:
        fn()
        print(f"PASS {name}")
        return True
    except Exception as e:
        print(f"FAIL {name}: {e}")
        return False

def assert_system_exit(spec_, kind="output", hostapi="WASAPI"):
    try:
        res = find_device(spec_, kind, hostapi)
    except SystemExit:
        return
    raise AssertionError(f"find_device({spec_!r}, {kind!r}) returned {res!r} instead of raising SystemExit")

def main():
    ok = True
    with patch.object(sd, "query_devices", return_value=MOCK_DEVS), \
         patch.object(sd, "query_hostapis", return_value=MOCK_APIS):
        ok &= check("find_device_out_of_range_index", lambda: assert_system_exit(99999, "output"))
        ok &= check("find_device_out_of_range_digit_str", lambda: assert_system_exit("99999", "output"))
        ok &= check("find_device_negative_index", lambda: assert_system_exit(-5, "output"))
        ok &= check("find_device_negative_str", lambda: assert_system_exit("-5", "output"))
        ok &= check("find_device_wrong_direction_output", lambda: assert_system_exit(0, "output"))
        ok &= check("find_device_wrong_direction_input", lambda: assert_system_exit(1, "input"))
        ok &= check("find_device_bool_rejected", lambda: assert_system_exit(True, "output"))

        def check_valid():
            assert find_device(1, "output", "WASAPI") == 1
            assert find_device("1", "output", "WASAPI") == 1
            assert find_device(0, "input", "WASAPI") == 0
            assert find_device("0", "input", "WASAPI") == 0

        ok &= check("find_device_valid_indices", check_valid)

    sys.exit(0 if ok else 1)

if __name__ == "__main__":
    main()
