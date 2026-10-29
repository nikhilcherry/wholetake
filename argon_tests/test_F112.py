import sys
from roomstamp.blind import _parse_time

try:
    assert _parse_time('1793427120') == 1793427120
    assert _parse_time('1793427120.5') == 1793427120
    assert _parse_time('1793427120.987') == 1793427120
    assert _parse_time('2026-10-31T06:12:00Z') == 1793427120
    assert _parse_time('2026-10-31T06:12:00.50Z') == 1793427120
    assert _parse_time('2026-10-31 06:12:00.50Z') == 1793427120
except Exception as e:
    print(f'FAIL parse_time_contract: {type(e).__name__}: {e}')
    sys.exit(1)

print('PASS parse_time_contract')
