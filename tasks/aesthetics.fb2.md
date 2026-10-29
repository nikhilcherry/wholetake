Round 2 review. I rendered both pages in Chromium at 1280 px on the demo laptop and read them. Tests pass (63/1 skip),
and there are no protocol literals in simx. It's a big step up: the CUT stamp, the pinch funnel, the custody strip and
the simx card all land. Fix these and emit both files in full again. Argue if you think one is wrong.

verdict/page.py
1. FONT, and it's a real bug on the demo machine: on this laptop Chromium resolves `system-ui` and "Roboto" to
   **Roboto Slab**, so the whole page renders as a slab serif (H1, card titles, transcript), the exact thing R8 banned.
   fc-match says plain sans here is "Noto Sans". Stack: `"Inter", "Noto Sans", "Segoe UI", "Helvetica Neue", Arial,
   sans-serif`. Drop system-ui, -apple-system, BlinkMacSystemFont and Roboto. Mono: `"JetBrains Mono", "Noto Sans Mono",
   "DejaVu Sans Mono", ui-monospace, Menlo, Consolas, monospace`.
2. Duplicates. Every fact appears once:
   - The top-right "cut" chip repeats the giant stamp, so remove it. Keep only the "ⓘ text match only" chip.
   - "Duration: 13.1s · Stream: aje" in the alignment card header repeats the hero meta line, so remove it. Put
     "Witnessed record vs. posted clip" there in muted text instead.
   - The AI note shows "MINOR minor: Omitting…", so drop the inline "minor:" text and keep the pill.
   - The custody step "Hash-chained" says "n=4 · verified · verified". Make it "n=4 · proof verified".
   - The quote block header "Cut at 0:07 (0.9 s removed · chunk #4)" repeats the hero line. Make it just "chunk #4 ·
     18:54:00–18:54:11 UTC" (the record's wall-clock span of the shown words if you have it, otherwise chunk only).
3. Overflow: the "Witnessed live" step wraps its mono line as "18:54:11.829 U / TC". Show "18:54:11 UTC" (no millis) and
   give step bodies `overflow-wrap: anywhere` as a safety net. The full value stays in Raw chunk proofs.
4. The ribbon region reads as one solid blue slab: the two ribbons merge because they share a near-solid fill. Make
   ribbon fill ~0.16 opacity with a 1.25 px stroke, so the space between the tracks is mostly navy and the two
   ribbons plus the red pinch are clearly separate shapes. Leave a 2 px gap in the record track's blue bars at
   piece boundaries.
5. Removed words in the transcript: the strike is still a full-opacity line through bold text. Make it 1 px at 40%
   opacity in the verdict colour, regular weight, keeping your pill. The words have to be readable from the back row.
6. The "▶ Hear the cut" button sits at the right with empty space around it on narrow widths. At < 760 px, stack the
   hero (stamp, line, button full-width). Check that 390 px doesn't scroll horizontally (the SVG has to scale via
   viewBox + width:100%).
7. Favicon: the page 404s /favicon.ico and logs a console error on stage if devtools is open. Add an inline
   `<link rel="icon" href="data:,">`.

simx/ui.html
8. The card is great. Two touches: (a) the bot tweet body still shows the full reply text above the card, which X
   does too, so that's fine, but cut the tail of the body at the link (already done?) and confirm no raw URL shows
   anywhere; (b) the card footer says "WHOLETAKE EVIDENCE · aje · 28 Oct 18:54 UTC". Use the same stream map as the
   page ("Al Jazeera English") so the two screens agree.
9. Same font fix as #1 for the card only (simx's own X chrome can keep its stack).

Don't regress anything you got right in round 2.
