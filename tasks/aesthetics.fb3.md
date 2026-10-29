Round 3 review: the font, the duplicates, the overflow, the strike, the favicon and stacking at 390 px all work (no horizontal
scroll at 390). Tests pass. Three things remain. Emit both files in full.

1. The ribbon slab is still there, and that's on ME, not you. Your paths are correct: I told you to scale the record
   track to the span of the pieces, so a clip that covers the record almost 1:1 gives nearly vertical ribbons that fill
   the whole band. Fix the domain instead: pad the record track with real context on both sides. Use
   max(4 s, 30% of the clip duration) each side, clamped to what the witnessed chunks actually cover (chunk json
   start_utc_est + duration_s; without chunk files, clamp to [0, seq_duration]). Draw that context as dim, unmatched
   record (track well only, plus a faint "…" fade at the far ends). Then the clip is visibly a SLICE of a longer record,
   the ribbons taper inward, and the pinch reads as the slice having a piece taken out. Apply the same padding to the
   mini SVG in the simx card. Axis labels: the start/end wall-clock of the padded record.
2. simx card footer: it wraps to two lines at 840 px ("… · 28 Oct 18:54 / UTC" and "Open forensic record / →").
   Make the footer a single nowrap line: left "Al Jazeera English · 28 Oct 18:54 UTC" (ellipsis if it's too long),
   right "Open record →". Drop the "WHOLETAKE EVIDENCE" prefix, because the card's red rail and stamp already say whose it is.
3. At < 560 px, hide the SVG's small axis/time labels and keep RECORD/CLIP and the −0.9s tag, so nothing renders
   below ~9 px.
