# Phase 0 measurements — resolution floor and client gate

**Status:** 2026-10-29. Both measurements are complete: 35 resolution runs and the gate
calibration on seven cards.

Both scripts run outside the run ledger on purpose: these are preprocessing probes, and
mixing them into `runs` would pollute the accuracy views.

---

## 1. Resolution sweep

**Method.** Seven cards × five long edges (`orig` = 4080 px, 3332, 2400, 1600, 1200),
route v3 (D-2026-10-26-09), scored against the seven ground-truth sheets through
`normalise.compare`. The alignment warps every source onto the 3332×2502 template, so
the model always sees template-scale crops; what changes is the information available to
SIFT and to the warp.

**Results** (full sweep, 35 runs, 2026-10-29; `output/resolution_sweep/results.json`)

| form | orig 4080 | 3332 | 2400 | 1600 | 1200 |
|---|---|---|---|---|---|
| form16 | 51/53 | 50/53 | 48/53 | 49/53 | 48/53 |
| form19 | 47/49 | 47/49 | 47/49 | 46/49 | 46/49 |
| form21 | 42/48 | **31/48** | 41/48 | 39/48 | 40/48 |
| form22 | 49/52 | 49/52 | 49/52 | 48/52 | 47/52 |
| form23 | 49/51 | 47/51 | 47/51 | 47/51 | 46/51 |
| form25 | 47/49 | 44/49 | 45/49 | 45/49 | 43/49 |
| form30 | 47/50 | 44/50 | 47/50 | 45/50 | 41/50 |
| **total** | **332/352 (94.3%)** | 312/352 (88.6%) | 324/352 (92.0%) | 319/352 (90.6%) | 311/352 (88.4%) |

**Reading.** Full resolution wins, by 2.3 to 5.9 points in aggregate, and every card is
affected. The owner's instinct — take the best image the device can give — is supported by
measurement, not just by caution.

Two things stop this being a clean curve. **form21 at 3332 collapses to 31/48** and
recovers at 2400; form21 is the hard capture (230 alignment matches at full size), so it is
the card where alignment and resampling interact badly. And the order is not monotonic
(2400 beats 1600 beats 1200 on some cards, loses on others), which is what a noise band of
two to three fields per card looks like. One run per cell cannot separate a 1-field
difference from a bad draw.

**The claim that survives:** capture at maximum resolution. **The claim that does not yet
exist:** a defensible "1600 px is enough for these cards" — the aggregate says otherwise.

**What it would take to settle it.** Finish the seven cards, then repeat one card three
times to get the noise band. Until then, no capture decision should rest on this table.

---

## 2. Client gate calibration

**Method.** `benchmarks/gate_calibration.py`: each card is reduced to a 600 px thumbnail
and measured, then measured again after six deliberate defects. No model calls; this asks
"does the cheap check see this?", not "would the model have failed?".

Medians across the seven cards:

| variant | brightness mean | blur (Laplacian var) | glare fraction | edge hull area |
|---|---|---|---|---|
| baseline | 153.6 | 1974.6 | 0.0000 | 0.686 |
| blurred | 153.6 | **75.1** | 0.0000 | 0.685 |
| dark | **53.1** | 243.1 | 0.0000 | 0.632 |
| overexposed | **232.9** | 1601.9 | **0.4851** | 0.686 |
| glare (ellipse) | 160.2 | 1826.0 | **0.0669** | 0.686 |
| rotated 3° | 153.0 | 1937.0 | 0.0000 | 0.686 |
| card clipped 12% | 155.0 | 2074.7 | 0.0000 | **0.743** |

**Two checks work.** Blur separates cleanly (1975 → 75). Brightness separates in both
directions (53 dark, 233 overexposed). Glare is caught by the saturated-pixel fraction,
whether it comes from a reflection or from overexposure.

**Two do not.**

- **Rotation is invisible to all four metrics.** A 3° tilt changes nothing measurable on a
  thumbnail. Rotation will be caught by the alignment stage, not on the phone.
- **The hull-area metric moves the wrong way when the card is clipped.** Removing 12% of
  one edge raises the number from 0.686 to 0.743, because the clipped card fills more of
  the frame. It measures "there is a large edged object", not "four card corners are
  visible". It must not ship as a framing check.

**Proposed thresholds** (to be validated against model error, which the sweep does not yet
supply):

```yaml
reject_if:
  blur_laplacian_var: "< 300"
  brightness_mean:    "< 70 or > 215"
  glare_fraction:     "> 0.02"
framing_and_rotation: server-side alignment gate only
```

---

## 3. What this changes

- **D-2026-10-28-12 stands, with a narrower gate.** The client check is blur, brightness
  and glare. Framing and rotation belong to the server-side alignment gate, which runs
  before any inference call.
- **The 1200 px upload floor is a sanity check, not a quality guarantee.** A sharp 1200 px
  capture of a well-lit card may read fine; a 4000 px blurred one will not. The gate, not
  the pixel count, is what protects the model.
- **The template rescan is closed.** The owner holds no better hard copy, so the template
  stays at 3332×2502 and the capture ceiling with it. Alignment quality is bounded by that
  scan; if it ever becomes the constraint, the fix is a new source document, not
  processing.
- **Measured cost per routed card: $0.0045** (`runs.cost_usd` on the first end-to-end API
  run), consistent with the $0.003–0.013 range in the README.

## 4. Still to measure

- Repeat one cell three times to get the noise band; then decide whether a second run per cell changes any conclusion.
- Correlate the gate metrics with per-card accuracy once the sweep is complete: the
  question is whether a blurred card that passes the thumbnail check still reads badly.
- A real low-end Android device, not a desktop browser.
