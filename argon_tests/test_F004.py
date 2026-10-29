import sys
import numpy as np
from roomstamp import detect, spec

def main():
    name = "f004_layer2_short_clip"
    y = np.zeros(100)
    feed = np.zeros(100)
    signs = np.ones((10, spec.L2_NBANDS))
    try:
        res = detect.layer2(y, feed, signs)
    except Exception as e:
        print(f"FAIL {name}: raised {type(e).__name__}: {e}")
        sys.exit(1)
    if res != {"frames": 0, "z": 0.0}:
        print(f"FAIL {name}: unexpected return value {res}")
        sys.exit(1)
    print(f"PASS {name}")

if __name__ == "__main__":
    main()
