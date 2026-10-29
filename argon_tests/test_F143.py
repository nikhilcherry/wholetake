import sys
from roomstamp.engine import StampParams

def main() -> None:
    p_table = StampParams(mode="table")
    if p_table.l1_dbfs != -50.0:
        print(f"FAIL table_defaults: expected l1_dbfs=-50.0, got {p_table.l1_dbfs}")
        sys.exit(1)
    if p_table.l1_floor != 0.3:
        print(f"FAIL table_defaults: expected l1_floor=0.3, got {p_table.l1_floor}")
        sys.exit(1)

    p_file = StampParams(mode="file")
    if p_file.l1_floor != 0.1:
        print(f"FAIL file_defaults: expected l1_floor=0.1, got {p_file.l1_floor}")
        sys.exit(1)

    p_pa = StampParams(mode="pa")
    if p_pa.l1_floor != 0.1:
        print(f"FAIL pa_defaults: expected l1_floor=0.1, got {p_pa.l1_floor}")
        sys.exit(1)

    p_custom = StampParams(mode="table", l1_dbfs=-45.0, l1_floor=0.15)
    p_roundtrip = StampParams(**p_custom.as_log())
    if p_roundtrip.l1_dbfs != -45.0 or p_roundtrip.l1_floor != 0.15:
        print(f"FAIL roundtrip: got {p_roundtrip}")
        sys.exit(1)

    print("PASS f143_stamp_params_table_defaults")

if __name__ == "__main__":
    main()
