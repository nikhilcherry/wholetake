import glob
import sys
from unittest import mock
from roomstamp import blind

def test_cli_partial_from_to_with_log():
    logs = sorted(glob.glob('logs/session_*.jsonl'))
    assert logs, 'no session logs found'
    log_path = logs[0]
    for flag in ('--from', '--to'):
        with mock.patch.object(blind, 'detect') as m_det, \
             mock.patch.object(blind.wavio, 'read', return_value=[0.0] * 48000 * 10):
            try:
                blind.main(['testdata/speech.wav', '--log', log_path, flag, '1800000000'])
            except SystemExit as e:
                if e.code == 2 and not m_det.called:
                    continue
            if m_det.called:
                t_lo, t_hi = m_det.call_args[0][3], m_det.call_args[0][4]
                if 1800000000 not in (t_lo, t_hi):
                    print(f'FAIL test_cli_partial_from_to_with_log: CLI silently ignored {flag} 1800000000 when --log was given (used window [{t_lo}, {t_hi}])')
                    sys.exit(1)
            else:
                print(f'FAIL test_cli_partial_from_to_with_log: unexpected exit for {flag}')
                sys.exit(1)
    print('PASS test_cli_partial_from_to_with_log')

if __name__ == '__main__':
    test_cli_partial_from_to_with_log()
