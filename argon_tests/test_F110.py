import os
import secrets
import sys
import tempfile
import numpy as np

from roomstamp import engine, l1verify, selftest, spec, wavio
from roomstamp.session import Session
from roomstamp.spec import FS

T0 = selftest.T0

def make_sess(x, mode='pa', seed=1):
    x = x + 10 ** (-80 / 20) * np.random.default_rng(seed).standard_normal(len(x))
    key = secrets.token_bytes(32)
    p = engine.StampParams(mode=mode) if mode != 'table' else engine.StampParams(mode='table', l1_dbfs=-45.0)
    out = engine.render(x, key, T0, p).astype(np.float64)
    d = tempfile.mkdtemp(prefix='rs_f110_')
    wavio.write(os.path.join(d, 'feed.wav'), x)
    sess = Session(key_id=spec.key_id(key), t_start=T0, params=p, feed='feed.wav', venue='Test Stage')
    sess.save(os.path.join(d, 'session.json'))
    rec = selftest.simulate(out, 0.6, 15.0, seed=3, mouth=x if mode == 'table' else None)
    return key, sess, x, rec

def test_repetitive_feed_no_false_edits():
    sp = selftest.load_speech()
    x = np.concatenate([sp, sp[::-1]])
    # Repeat phrases at clip boundaries and interior (separated in time)
    x[int(20 * FS):int(22.5 * FS)] = x[int(38 * FS):int(40.5 * FS)]
    x[int(33 * FS):int(35.5 * FS)] = x[int(39 * FS):int(41.5 * FS)]
    key, sess, x, rec = make_sess(x, 'pa')
    lead = int(1.3 * FS)
    clip = rec[lead + int(20 * FS):lead + int(35.5 * FS)]
    r = l1verify.verify(clip, x, sess, key, log=lambda *_: None)
    if r['edits']:
        print(f"FAIL repetitive_feed_no_false_edits: continuous clip over repetitive feed reported false edits: {r['edits']}")
        return False
    print('PASS repetitive_feed_no_false_edits')
    return True

def test_back_to_back_looped_phrase_continuous_clip():
    sp = selftest.load_speech()
    x = np.concatenate([sp, sp[::-1]])
    # Back-to-back repeated 4s phrase (3 copies in a row)
    phrase = x[int(10 * FS):int(14 * FS)].copy()
    x[int(20 * FS):int(24 * FS)] = phrase
    x[int(24 * FS):int(28 * FS)] = phrase
    x[int(28 * FS):int(32 * FS)] = phrase
    key, sess, x, rec = make_sess(x, 'pa')
    lead = int(1.3 * FS)
    clip = rec[lead + int(18 * FS):lead + int(35 * FS)]
    r = l1verify.verify(clip, x, sess, key, log=lambda *_: None)
    if r['edits']:
        print(f"FAIL back_to_back_looped_phrase_continuous_clip: continuous clip with back-to-back repeated 4s phrase reported false edits: {r['edits']}")
        return False
    print('PASS back_to_back_looped_phrase_continuous_clip')
    return True

if __name__ == '__main__':
    ok1 = test_repetitive_feed_no_false_edits()
    ok2 = test_back_to_back_looped_phrase_continuous_clip()
    sys.exit(0 if (ok1 and ok2) else 1)
