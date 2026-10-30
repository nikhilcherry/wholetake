import glob
import sys
import tempfile
from unittest import mock
from roomstamp import blind, wt_test

def test_cli_partial_from_to_with_log():
    # logs/ is gitignored: build a throwaway S1 session log instead of relying on a local one
    venue = tempfile.mkdtemp(prefix='test_f267_')
    wt_test.make_venue(venue, 'pa', b'\x11' * 32, seconds=5.0, feed=False)
    logs = sorted(glob.glob(f'{venue}/session_*.jsonl'))
    assert logs, 'no session logs found'
    log_path = logs[0]
    key_path = f'{venue}/venue.key'  # and venue.key is gitignored too
    with open(key_path, 'w', encoding='utf-8') as f:
        f.write('11' * 32)
    for flag in ('--from', '--to'):
        with mock.patch.object(blind, 'detect') as m_det, \
             mock.patch.object(blind.wavio, 'read', return_value=[0.0] * 48000 * 10):
            try:
                blind.main(['testdata/speech.wav', '--log', log_path, '--key-file', key_path, flag, '1800000000'])
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
