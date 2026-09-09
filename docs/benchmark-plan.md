# Benchmark Plan — VLM Model Comparison & Extraction Accuracy (HANNA v2)

**Purpose:** measure whether **Qwen 3.5** (and possibly **Qwen 3.8**) VLMs improve extraction
quality vs. the prior iteration (which used qwen3-vl / minicpm-v :8b), and establish a repeatable
accuracy baseline for any country form.

## Data: ground truths (start here)
Existing gold standards documented in `hanna/AI_self_make.txt` (Mozambique template,
`Mozambique_Leprosy_clinic_card_PT_front.jpg`):

| Form | Ground truth |
|---|---|
| `form16.jpg` | nerve enlargement `-,+,-,-,-,+`; nerve pain `-,-,-,+,-,-`; skin patches: 2 front + 2 back = **4**; WHO disability value-pairs (12 fields) fully listed |
| `form19.jpg` | nerve enlargement `[ -][-][+][-][-][-]`; nerve pain all `-`; skin patches **0**; tickbox truths: Female, new case, PB adult, Itching-NO, Family contact-YES, Previous treatment-NO, Self referred |
| `form21.jpg` | nerve enlargement `[ -][+][-][-][+][-]`; nerve pain `[-][+][-][-][-][-]`; skin patches **3**; WHO disability value-pairs listed |

*Locations:`/home/sam/hanna/hanna3/uploads/form16.jpg` per the notes; confirm current location of
the image files (upload folders exist under `hanna/MOBILE_APP`/`hanna_scalable`).*

## Metrics
- **Field accuracy** per value-pair: exact match on allowed values; for numeric (skin patches,
  birth year) use exact + ±1-tolerance.
- **Group/section pass rate** (all fields in a group correct).
- **Required-field completeness detection** accuracy (does the system flag missing correctly?).
- **Latency** and **cost per form** (for provider economics).
- Comparison to **prior iteration output** where available.

## What to compare (matrix)
| Dimension | Values |
|---|---|
| Models | Qwen 3.5 VL (new), Qwen 3.8 VL (if available), qwen3-vl (baseline), optionally prior 8b |
| Prompt style | plain extraction vs. template-comparison vs. few-shot/CoT (from `hanna/hanna_next_steps.txt`) |
| Image | aligned-only vs. aligned + background-subtracted (template subtraction) |
| Inference path | local (later GPU) vs. remote API |

## Harness skeleton
`benchmarks/benchmark_harness.py` provides:
- a `GroundTruth` loader (JSON per form: image_path → expected key-value pairs),
- a uniform `model_call(image_paths, prompt) -> {key: value, reasoning}` interface (provider-agnostic),
- a `score_result` that computes field / group accuracy,
- CSV/JSON report output for comparison.

Run: `python benchmarks/benchmark_harness.py --config benchmarks/run_config.yaml [--model qwen3.5-vl]`.

## Execution notes
- **Cannot run locally yet** — no GPU/Ollama on this machine (verified). Phase 1 must select a remote
  Qwen 3.5/3.8 path (provider API) or bring up a GPU/VPS. The harness is ready for that.
- Add more country forms' ground truths as they are validated (Phase 3).

## Acceptance target (provisional)
- Clear, reproducible accuracy improvement over prior iteration on the Mozambique gold standards,
  with a documented per-field breakdown; then extend to a second country card.
- <95% nerve/tickbox (per prior notes) and a measurable skin-patch/% improvement are goals to test,
  not assumed.
