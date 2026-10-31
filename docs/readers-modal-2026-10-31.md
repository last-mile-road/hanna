# Readers on Modal — state at 2026-10-31

## MinerU: working, but on the CPU engine

`mineru-kit parse` (in-process, no daemon) returns the card as HTML tables — NIL, name,
dates, clinical history and the tick columns, all present and readable. One card, 3.5k
characters of markdown, `ok: true`.

**It took 526 s per card.** The engine selector picked `llama-cpp` because `mineru[torch]`
does not install vLLM, and llama.cpp runs the VLM step on CPU. The fix is
`mineru[full]` (or vLLM) with `config.model.vlm.engine = vllm`, at which point the same
call should be ~10–20 s on an L4. That is the next build.

Two things that cost time and are now known:

- `mineru parse` (the CLI) needs a local doclib daemon that binds a Unix socket inside
  `MINERU_HOME`; a Modal Volume cannot host a socket (`OSError 95`). `mineru-kit parse`
  avoids the daemon entirely.
- `MINERU_HOME` must be on local disk (`/tmp/mineru`) with the weights symlinked from the
  Volume; the socket lives under the home.

## Qwen 8B and 9B: endpoints deployed, not yet exercised

`hanna-vllm` serves `Qwen/Qwen3-VL-8B-Instruct` and `Qwen/Qwen3.5-9B` through vLLM on an
L4, fp16, one model per container, 8k context, OpenAI-compatible. Deployed; the first call
to each downloads 17–19 GB of weights into `hanna-models`.

Before testing: add `--api-key` to both servers so the endpoints are not open.

## Next

1. MinerU with the vLLM engine on an L4; re-time one card.
2. API key on the vLLM servers; point two profiles at them.
3. Seven cards through 8B, 9B and MinerU with the same prompts: accuracy, latency,
   and a repeat run for consistency.
