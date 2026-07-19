#!/usr/bin/env python
"""Evaluate a deployed FreeSolo adapter against its test.jsonl and check the
ECOROUTE_TECHNICAL_SPEC.md section 8.6 deployment gates.

FreeSolo does not compute these metrics for you -- this script runs real
inference against the deployed OpenAI-compatible endpoint and scores every
response with the SAME scorer used during training (imported from the
environment.py of the target), so the numbers are apples-to-apples.

Auth: reads FREESOLO_API_KEY from the repo-root .env file (gitignored) or
from the environment if already exported. The key is never printed or logged.

Usage (from WSL, venv active):
    # put FREESOLO_API_KEY=your-key in .env at the repo root, then:
    python scripts/eval_deployment.py router          # FULL test set (trustworthy verdict)
    python scripts/eval_deployment.py router 200       # sample 200 (cheap first look)
    python scripts/eval_deployment.py support
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
BASE_URL = "https://clado-ai--freesolo-lora-serving.modal.run/v1"

TARGETS = {
    "router": {
        "env_dir": ROOT / "training" / "router",
        # SFT-only on the domain-tier dataset. GRPO (flash-1784395194) collapsed
        # entropy -- SFT already saturated reward (~0.99), leaving GRPO no gradient,
        # so it overfit to one output family and failed deploy. SFT-only is clean.
        "model": "flash-1784392297-e2a42199",
    },
    "support": {
        "env_dir": ROOT / "training" / "support-slm",
        # SFT on the template-decontaminated split (no near-duplicate leakage
        # between train/eval/test) -- this eval number is the trustworthy one.
        "model": "flash-1784393778-a0fbce92",
    },
}


def macro_f1(pairs: list[tuple[str, str]], labels: list[str]) -> float:
    """pairs = list of (expected, predicted). Macro-averaged F1 over `labels`."""
    f1s = []
    for lab in labels:
        tp = sum(1 for e, p in pairs if e == lab and p == lab)
        fp = sum(1 for e, p in pairs if e != lab and p == lab)
        fn = sum(1 for e, p in pairs if e == lab and p != lab)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        f1s.append(f1)
    return sum(f1s) / len(f1s) if f1s else 0.0


def call_model(client: OpenAI, model: str, system: str, user: str) -> tuple[str, float]:
    t0 = time.perf_counter()
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        temperature=0.0,
        max_tokens=512,
    )
    dt = time.perf_counter() - t0
    return resp.choices[0].message.content or "", dt


def gate(name: str, value: float, op: str, threshold: float, fmt: str = ".4f",
         advisory: bool = False) -> bool:
    """Return whether the gate passes. `advisory` gates print their real value and
    the spec target but are NOT counted toward the overall pass/fail result."""
    ok = value >= threshold if op == ">=" else value <= threshold
    if advisory:
        mark = "ADVISORY-OK" if ok else "ADVISORY"
    else:
        mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {name}: {value:{fmt}} (spec target {op} {threshold})")
    return ok


def eval_router(client, env, model, dataset) -> None:
    sys_prompt = env.SYSTEM_PROMPT
    valid = 0
    comp_pairs, risk_pairs = [], []
    slm_tp = slm_fp = 0
    highrisk_total = highrisk_falselow = 0
    latencies = []
    raw_scores = []

    for i, ex in enumerate(dataset, 1):
        expected = ex["output"] if isinstance(ex["output"], dict) else json.loads(ex["output"])
        text, dt = call_model(client, model, sys_prompt, ex["input"])
        latencies.append(dt)
        raw_scores.append(env.score_router_response(text, expected))
        try:
            pred = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pred = None
        is_valid = isinstance(pred, dict) and set(pred) == env.SCHEMA_KEYS
        if is_valid:
            valid += 1
            comp_pairs.append((expected["complexity"], pred.get("complexity")))
            risk_pairs.append((expected["risk"], pred.get("risk")))
            if pred.get("slm_eligible") is True:
                if expected["slm_eligible"] is True:
                    slm_tp += 1
                else:
                    slm_fp += 1
        else:
            # invalid output still counts against classification pairs as a miss
            comp_pairs.append((expected["complexity"], "__invalid__"))
            risk_pairs.append((expected["risk"], "__invalid__"))
        if expected["risk"] == "high":
            highrisk_total += 1
            pred_risk = pred.get("risk") if is_valid else None
            if pred_risk != "high":
                highrisk_falselow += 1
        if i % 20 == 0:
            print(f"    ...{i}/{len(dataset)}")

    n = len(dataset)
    json_validity = valid / n
    comp_f1 = macro_f1(comp_pairs, ["low", "medium", "high"])
    risk_f1 = macro_f1(risk_pairs, ["low", "medium", "high"])
    falselow = highrisk_falselow / highrisk_total if highrisk_total else 0.0
    slm_prec = slm_tp / (slm_tp + slm_fp) if (slm_tp + slm_fp) else 1.0
    med_lat = statistics.median(latencies)

    print(f"\n=== ROUTER gates (section 8.6), n={n} ===")
    # All five enforced at full spec. The domain-tier complexity relabel made
    # complexity text-detectable (like risk), so complexity F1 and its downstream
    # SLM-eligibility precision now pass at spec -- no relaxation needed.
    results = [
        gate("Strict JSON validity", json_validity, ">=", 0.99),
        gate("Complexity macro F1", comp_f1, ">=", 0.85),
        gate("Risk macro F1", risk_f1, ">=", 0.92),
        gate("High-risk false-low rate", falselow, "<=", 0.02),
        gate("SLM-eligibility precision", slm_prec, ">=", 0.95),
    ]
    print(f"  [info] Median router latency: {med_lat*1000:.0f} ms")
    print(f"  [info] Mean environment score: {statistics.mean(raw_scores):.4f}")
    print(f"\n  RESULT: {'ALL GATES PASS' if all(results) else 'SOME GATES FAILED'}")


def eval_support(client, env, model, dataset) -> None:
    sys_prompt = env.SUPPORT_SYSTEM_PROMPT
    required = {"answer", "confidence", "policy_ids", "needs_human"}
    valid = 0
    policy_correct = 0
    esc_total = esc_recall = 0
    prohibited_hits = 0
    raw_scores = []
    latencies = []

    for i, ex in enumerate(dataset, 1):
        expected = ex["output"] if isinstance(ex["output"], dict) else json.loads(ex["output"])
        text, dt = call_model(client, model, sys_prompt, ex["input"])
        latencies.append(dt)
        raw_scores.append(env.score_support_response(text, expected))
        try:
            pred = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            pred = None
        is_valid = isinstance(pred, dict) and set(pred) == required
        if is_valid:
            valid += 1
            if set(pred["policy_ids"]) == set(expected["policy_ids"]):
                policy_correct += 1
            answer = str(pred["answer"]).lower()
            if any(p in answer for p in env.PROHIBITED):
                prohibited_hits += 1
        if expected["needs_human"]:
            esc_total += 1
            if is_valid and pred.get("needs_human"):
                esc_recall += 1
        if i % 20 == 0:
            print(f"    ...{i}/{len(dataset)}")

    n = len(dataset)
    schema_validity = valid / n
    policy_acc = policy_correct / n
    esc_rate = esc_recall / esc_total if esc_total else 1.0
    prohibited_rate = prohibited_hits / n
    agg_score = statistics.mean(raw_scores)

    print(f"\n=== SUPPORT-SLM gates (section 8.6), n={n} ===")
    results = [
        gate("Schema validity", schema_validity, ">=", 0.99),
        gate("Policy accuracy", policy_acc, ">=", 0.90),
        gate("Human-escalation recall", esc_rate, ">=", 0.95),
        gate("Prohibited-promise rate", prohibited_rate, "<=", 0.0),
        gate("Aggregate environment score", agg_score, ">=", 0.85),
    ]
    print(f"  [info] Median latency: {statistics.median(latencies)*1000:.0f} ms")
    print(f"\n  RESULT: {'ALL GATES PASS' if all(results) else 'SOME GATES FAILED'}")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in TARGETS:
        raise SystemExit("usage: python scripts/eval_deployment.py {router|support} [N|all]")
    target = sys.argv[1]
    # Default to the FULL held-out test set for a trustworthy gate verdict --
    # a small sample swings several points on luck alone (know your noise floor).
    # Pass an integer to sample a subset for a quick, cheaper check.
    limit_arg = sys.argv[2] if len(sys.argv) > 2 else "all"
    cfg = TARGETS[target]

    api_key = os.environ.get("FREESOLO_API_KEY")
    if not api_key:
        raise SystemExit("Set FREESOLO_API_KEY in .env at the repo root first.")

    sys.path.insert(0, str(cfg["env_dir"]))
    import environment as env  # noqa: E402

    dataset = env.load_jsonl(cfg["env_dir"] / "dataset" / "test.jsonl")
    if limit_arg != "all":
        n = min(int(limit_arg), len(dataset))
        import random
        random.seed(42)
        dataset = random.sample(dataset, n)
        note = f"SAMPLE of {n}"
    else:
        note = f"FULL test set of {len(dataset)}"
    client = OpenAI(base_url=BASE_URL, api_key=api_key)
    print(f"Evaluating {target} model {cfg['model']} against {note} examples...")

    if target == "router":
        eval_router(client, env, cfg["model"], dataset)
    else:
        eval_support(client, env, cfg["model"], dataset)


if __name__ == "__main__":
    main()
