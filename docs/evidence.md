# Evidence — what has been measured, and its limits

All numbers below are from the Mozambique clinic card and from dummy test cards.
They are evidence about this pipeline on this card; they are not a claim about
another country's card. Every country measures on its own cards.

## Test material

Seven dummy cards, read from the scanned images, with known-correct values
recorded by hand. Ground truth for the seven: 337 verified values of 427. All
seven are read (46 to 51 of 61 fields each); the gaps are fields that are blank
on the card. form20 was dropped as a duplicate of form16.

The sheets are country data and live outside this repository; see
`docs/DEVELOPING.md`.

## Per-block accuracy, 2026-10-28

| Block | Model | Score |
|---|---|---|
| Hand/feet, with the block legend and the required `is_filled` | 9B | 26/28 (92.9%) |
| Clinical history | 27B | 41/42 (97.6%) |
| Treatment | 8B | 21/23 (91.3%) |

Route v3: 8B for classification, nerve, skin and treatment; 9B for demographics
and hand/feet; 27B for clinical history and disability.

## One card, four routes, 2026-10-26

form16, same prompt:

| Profile | Score | Wall | Cost |
|---|---|---|---|
| qwen3-vl-8b, 4 parallel | 44/49 (89.8%) | 16.8 s | $0.0032 |
| qwen3.5-27b, reasoning low | 48/49 (98.0%) | 121 s | $0.0129 |
| qwen3.8-27b, tuned | 47/49 (95.9%) | 202 s | $0.0092 |
| qwen3.8-27b, default settings | 33/52 (63.5%) | 277 s | $0.0147 |

Qwen3.8 at its default output budget returned empty content for the disability
table. With `reasoning_effort: low`, `max_tokens: 8000`, `temperature: 0.3` and
`top_p: 0.95` it scored 14/14 on that block. Parallel calls help the 8B and hurt
the 27B, which the provider queues.

Across three cards, the 8B scored 122/150 (81.3%) and the 9B 121/150 (80.7%).
form21 is the hardest capture (230 alignment matches): 8B 32/48 (66.7%), 9B
38/48 (79.2%), 27B at low reasoning 44/48 (91.7%) but very slow.

## The v5 baseline

Field accuracy of v5, from 53 human-reviewed jobs: **94.2% over 3,045
judgements**. Read it with the caveat in `docs/baseline-2026-02.md`: the 53 jobs
are only 15 patients, and one patient supplies 37% of the judgements. The weakest
fields are the three blocks v5 never declared as value pairs:
`right_foot_dorsiflex` 58%, `nr_skinpatches` 70%, `insensitive_points_*` 62–74%.

## Speed

On the development machine, a front page takes about 10 seconds and a back page
about 31 seconds through the current route, including table reads. Boot cost
dominates batch GPU use: a reader-only box is cheaper per card than a full model
box, and the arithmetic is in the project's GPU hosting note.

## How to read these numbers

- Block scores come from small counts: 23 to 53 values. A two-value change moves
  a percentage by several points.
- The route and the prompt change the result more than the model size in some
  blocks, which is why the route is recorded per block.
- A wrong value in the ground truth turns a correct extraction into a failure.
  That is why the sheets record who verified each value.
