# HANNA Server v2 — Fresh Technical Iteration (Objective 2)

**Status:** Scaffolding (project foundation only; model runs deferred to GPU/VPS environment)
**Date:** 2026-09-09
**Aligns with:** memory pack objective 2
**Working directory:** `/home/sam/Documents/hanna_project/objective-2-server/`
**Baseline to build on:** `new_agent/hanna_scalable/` (last working iteration)

> This folder is the fresh, professionally-managed start of the new server. Its docs live in
> `./docs/`. The new repo will live on **private GitHub first, public later** (no secrets).

---

## Objectives (this iteration)
1. **Generic & user-definable form setup** — a country scans its template form, identifies
   information areas / data groups / fields, and the system builds its config from that (no
   hardcoded per-country config).
2. **Model quality upgrade** — test **Qwen 3.5** and possibly **Qwen 3.8** VLMs against the prior
   iteration and against ground-truth forms; measure extraction accuracy.
3. **Structured, professional dev cycle** — objectives + commits per milestone; roadmap-driven;
   clean repo hygiene (private → public later).
4. **Local on this machine → VPS later** — must run on modest hardware before cloud migration.

## Working method & phasing (suggested)
- **Phase 0 (this folder):** docs, architecture, benchmark harness skeleton, repo init. No GPU needed.
- **Phase 1:** local environment (venv), pick inference path — remote Qwen 3.5/3.8 API or a capable
  GPU/VPS. Build the **model-comparison benchmark** on the existing Mozambique gold standards
  (form16/19/21 ground truths from `hanna/AI_self_make.txt`).
- **Phase 2:** implement **generic template definition** (UI to scan/upload a form, define field
  groups + bounding boxes, auto-suggest fields) → generate extraction config.
- **Phase 3:** multi-country config + accuracy validation on other country forms (Bangladesh, Chad,
  DRC, India, Nigeria samples exist in `Reading patient cards with AI/`).
- **Phase 4:** local full-stack test, then VPS deployment + open-sourcing decision.

## Constraints / notes
- **No GPU / no Ollama on the dev machine right now** (verified: 14GB RAM, 16 CPU, no nvidia-smi,
  no docker). Local VLM runs are not feasible here; plan for a remote/GPU inference path in Phase 1.
- Python 3.14 is the system python (externally managed) — use a dedicated venv.
- Keep the provider-agnostic `inference` layer concept from `hanna_scalable` — it is a core lesson
  (see `memory/lessons/2026-09-09-scalable-config-driven.md`).

## Files in this folder
| Path | Purpose |
|---|---|
| `README.md` | This file |
| `docs/requirements-generic-forms.md` | Requirements for the generic/user-definable template setup |
| `docs/benchmark-plan.md` | Model-comparison & accuracy benchmark plan |
| `benchmarks/benchmark_harness.py` | Skeleton harness for running a model on ground-truth forms and scoring extraction |
| `ground_truth/README.md` | Note on where ground truths come from |

## Dev-cycle conventions
- One commit per logical unit; branch per milestone; conventional commit messages.
- Each milestone ends with a verification step (tests / benchmark run).
- No secrets in the repo (inference provider API keys go in env / gitignored config).
