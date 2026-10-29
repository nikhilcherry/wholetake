from __future__ import annotations

import sys
from wholetake import asr, match
from wholetake.audio import FPS


def _make_words(toks: list[str], t0: float, step: float = 0.4, idx0: int = 0) -> list[asr.Word]:
    out = []
    for k, tok in enumerate(toks):
        s = t0 + k * step
        out.append(asr.Word(idx0 + k, tok, tok, s, s + step * 0.9, 0.99))
    return out


def main() -> int:
    # Construct a >20 min witness transcript with two distant regions:
    # Region 1 around t=100s (clauses A and C)
    # Region 2 around t=1500s (clause B)
    clause_a = ["quantum", "entanglement", "experiments", "demonstrate", "nonlocal"]
    clause_c = ["correlations", "between", "distant", "photons", "reliably"]
    clause_b = ["fiscal", "policy", "reforms", "accelerate"]

    ww: list[asr.Word] = []
    ww += _make_words([f"padw{i}" for i in range(30)], 10.0, 0.4, len(ww))
    ww += _make_words(clause_a + clause_c, 100.0, 0.4, len(ww))
    ww += _make_words([f"midw{i}" for i in range(40)], 500.0, 0.4, len(ww))
    ww += _make_words(clause_b, 1500.0, 0.4, len(ww))
    ww += _make_words([f"endw{i}" for i in range(30)], 1800.0, 0.4, len(ww))

    # Clip splices clause B (from t=1500s) between clause A and clause C (from t=100s).
    # Pass 1 bridges across B's 4 clip tokens via SW_GAP_CLIP (-0.8 * 4 = -3.2) to connect A and C.
    cw = _make_words(clause_a + clause_b + clause_c, 0.0, 0.4, 0)

    tp = match._sw_pieces(cw, ww)
    if len(tp) < 2:
        print(f"FAIL test_F016: expected >= 2 text alignment pieces when bridging spliced segment, got {len(tp)}: {tp}")
        return 1

    # Check that the spliced middle clip indices (5..8) were aligned in a subsequent pass
    b_clip_indices = {c for p in tp[1:] for c, _ in p}
    if b_clip_indices != {5, 6, 7, 8}:
        print(f"FAIL test_F016: expected spliced clip indices {{5, 6, 7, 8}} in secondary piece, got {b_clip_indices}")
        return 1

    # Check that _regions includes both the ~100s region and the ~1500s region
    clip_s = cw[-1].end
    rec_s = 2000.0
    regions = match._regions(tp, cw, ww, 1.0, clip_s, rec_s)
    has_r1 = any(r0 <= 100 * FPS <= r1 for r0, r1 in regions)
    has_r2 = any(r0 <= 1500 * FPS <= r1 for r0, r1 in regions)
    if not (has_r1 and has_r2):
        print(f"FAIL test_F016: expected regions covering both 100s and 1500s, got {regions}")
        return 1

    print("PASS test_F016")
    return 0


if __name__ == "__main__":
    sys.exit(main())
