"""
HANNA v2 — benchmark harness skeleton.

Purpose: run a VLM on ground-truth clinic-card forms and score extraction accuracy,
so we can compare Qwen 3.5/3.8 against the prior iteration on real gold standards.

STATUS: SCAFFOLD. This is a clean, dependency-light skeleton. It is intentionally
provider-agnostic (Ollama / OpenAI / custom) and does NOT run any model by itself —
a concrete provider backend must be wired in at Phase 1, when a GPU/VPS inference path
exists. It is importable and its pure functions (loading, scoring, reporting) are
testable offline.

Usage (when a backend exists):
    python benchmarks/benchmark_harness.py --config benchmarks/run_config.yaml
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class GroundTruth:
    """One form's expected key/value pairs (the gold standard)."""
    form_id: str
    image_path: Optional[str]
    expected: Dict[str, str]  # field key -> expected value (normalised)

    @classmethod
    def from_json(cls, path: Path) -> "GroundTruth":
        d = json.loads(path.read_text())
        return cls(
            form_id=d["form_id"],
            image_path=d.get("image_path"),
            expected={k: str(v) for k, v in d.get("expected", {}).items()},
        )


@dataclasses.dataclass
class RunResult:
    form_id: str
    model: str
    predicted: Dict[str, str]
    expected: Dict[str, str]
    # field key -> correct (bool) or None if not comparable
    field_correct: Dict[str, Optional[bool]]

    def field_accuracy(self) -> float:
        judged = [v for v in self.field_correct.values() if v is not None]
        return (sum(judged) / len(judged)) if judged else 0.0

    def group_pass_rate(self, groups: Dict[str, List[str]]) -> float:
        passes = 0
        total = 0
        for _gname, keys in groups.items():
            judged = [k for k in keys if self.field_correct.get(k) is not None]
            if not judged:
                continue
            total += 1
            if all(self.field_correct[k] for k in judged):
                passes += 1
        return (passes / total) if total else 0.0


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def normalise(value: str) -> str:
    """Normalise a value for comparison (strip case/whitespace)."""
    return str(value).strip().lower().replace(" ", "")


def is_correct(predicted: str, expected: str, numeric: bool = False, tol: int = 1) -> bool:
    """Exact match by default; optional numeric tolerance for counts/years."""
    if numeric:
        try:
            return abs(int(float(predicted)) - int(float(expected))) <= tol
        except (ValueError, TypeError):
            return False
    return normalise(predicted) == normalise(expected)


def score(
    predicted: Dict[str, str],
    expected: Dict[str, str],
    numeric_fields: Optional[set] = None,
    tol: int = 1,
) -> Dict[str, Optional[bool]]:
    """Return per-field correctness map (None when a field is missing/unjudgeable)."""
    numeric_fields = numeric_fields or set()
    out: Dict[str, Optional[bool]] = {}
    for key, exp in expected.items():
        if key not in predicted:
            out[key] = False
        else:
            out[key] = is_correct(predicted[key], exp, key in numeric_fields, tol)
    return out


# ---------------------------------------------------------------------------
# Provider abstraction (to be implemented at Phase 1)
# ---------------------------------------------------------------------------

class InferenceBackend(Protocol):
    """Uniform interface for any VLM provider. Implement for Ollama/OpenAI/custom."""
    def name(self) -> str: ...
    def chat_with_images(self, prompt: str, image_paths: List[str], model: str) -> str: ...


def default_parse_model_output(raw: str) -> Dict[str, str]:
    """Best-effort parse of a model's JSON-ish output into {key: value}.
    Override per provider/model as needed."""
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        obj = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in obj.items()}


# ---------------------------------------------------------------------------
# Runner / reporting
# ---------------------------------------------------------------------------

def run_benchmark(
    ground_truths: List[GroundTruth],
    backend: InferenceBackend,
    model: str,
    prompt_builder: Callable[[GroundTruth], str],
    parse_output: Callable[[str], Dict[str, str]] = default_parse_model_output,
    numeric_fields: Optional[set] = None,
    groups: Optional[Dict[str, List[str]]] = None,
) -> List[RunResult]:
    results: List[RunResult] = []
    for gt in ground_truths:
        prompt = prompt_builder(gt)
        images = [gt.image_path] if gt.image_path else []
        raw = backend.chat_with_images(prompt, images, model)
        predicted = parse_output(raw)
        field_correct = score(predicted, gt.expected, numeric_fields)
        results.append(RunResult(gt.form_id, model, predicted, gt.expected, field_correct))
    return results


def write_report(results: List[RunResult], out_csv: Path, groups=None) -> None:
    with out_csv.open("w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["form_id", "model", "field_accuracy", "group_pass_rate"])
        for r in results:
            writer.writerow([
                r.form_id, r.model,
                round(r.field_accuracy(), 4),
                round(r.group_pass_rate(groups or {}), 4),
            ])
    print(f"report written to {out_csv}")


def main() -> None:
    ap = argparse.ArgumentParser(description="HANNA v2 benchmark harness (skeleton)")
    ap.add_argument("--config", default="benchmarks/run_config.yaml")
    ap.add_argument("--ground-truth-dir", default="ground_truth")
    ap.add_argument("--out", default="benchmarks/report.csv")
    args = ap.parse_args()

    gt_dir = Path(args.ground_truth_dir)
    ground_truths = [
        GroundTruth.from_json(p) for p in sorted(gt_dir.glob("*.json"))
    ]
    if not ground_truths:
        print("No ground-truth JSON found. Add them under ground_truth/ (see ground_truth/README.md).")

    print(
        "Benchmark harness is a SCAFFOLD: no inference backend is wired yet. "
        "Phase 1 must implement an InferenceBackend (Ollama/OpenAI) once a GPU/VPS "
        "inference path exists, then this runner scores it."
    )
    print(f"Found {len(ground_truths)} ground-truth form(s) to score.")


if __name__ == "__main__":
    main()
