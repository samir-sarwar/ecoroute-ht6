#!/usr/bin/env python
"""Convert RouterBench raw (.pkl) into freesolo router training data.

RouterBench raw has one row per (prompt, model): 36,497 prompts x 11 models,
each with a `performance` score in [0, 1]. The freesolo RouterEnvironment needs
ONE example per prompt, labelled with complexity/risk/etc.

Labeling design (v2 -- tuned to be learnable from prompt text AND balanced):

  complexity  <- cross-model solve rate. A prompt many models solve is "low"
                 (a cheap model can handle it); one few solve is "high".
  risk        <- DOMAIN, not solve rate. Legal / medical / financial / security /
                 moral prompts are "high" regardless of difficulty (spec 1.3).
                 This is detectable from the text, so the model can learn it --
                 unlike v1 where risk == complexity and was not learnable.
  slm_eligible<- deterministic: complexity == low AND risk != high.

The set is then BALANCED across all 9 (complexity x risk) cells so the model
cannot win by always predicting the majority class -- v1's imbalance
(low 19k / med 11k / high 6k) plus a 2000-example train cap made the model
collapse to "low", tanking macro-F1 and the high-risk false-low rate.

Usage:
    python scripts/build_router_dataset.py
Outputs:
    training/router/dataset/{train,eval,test}.jsonl
"""

from __future__ import annotations

import ast
import json
import random
from collections import defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PKL = ROOT / "data" / "routerbench" / "routerbench_raw.pkl"
OUT_DIR = ROOT / "training" / "router" / "dataset"

SOLVE_THRESHOLD = 0.5           # a model "solves" a prompt if performance >= this
LOW_MIN = 0.70                  # >=70% of models solve it -> low complexity
HIGH_MAX = 0.30                 # <=30% of models solve it -> high complexity
PER_CELL = 1400                 # examples per (complexity x risk) cell before split
EVAL_FRAC, TEST_FRAC = 0.075, 0.075
SEED = 13
random.seed(SEED)

# --- domain -> risk (detectable from prompt text; spec 1.3 high-stakes domains)
HIGH_RISK_KEYS = ("law", "legal", "jurisprudence", "medicine", "medical", "clinical",
                  "anatomy", "virology", "nutrition", "human-aging", "human-sexuality",
                  "accounting", "audit", "econometrics", "security", "moral", "ethics",
                  "college-chemistry", "college-physics")
MED_RISK_KEYS = ("math", "algebra", "mbpp", "computer", "machine-learning", "electrical",
                 "physics", "chemistry", "statistics", "engineering", "econom")


def risk_for(eval_name: str) -> str:
    e = eval_name.lower()
    if any(k in e for k in HIGH_RISK_KEYS):
        return "high"
    if any(k in e for k in MED_RISK_KEYS):
        return "medium"
    return "low"


def task_type_for(eval_name: str) -> str:
    e = eval_name.lower()
    if "math" in e or "algebra" in e or "remainder" in e or "gsm" in e:
        return "math"
    if "mbpp" in e or "code" in e or "computer" in e or "machine-learning" in e:
        return "code"
    if "law" in e or "legal" in e or "jurisprudence" in e:
        return "legal"
    if "medic" in e or "clinical" in e or "anatomy" in e or "virology" in e:
        return "medical"
    if "moral" in e or "ethic" in e or "philosophy" in e:
        return "reasoning"
    if "chinese" in e or "poem" in e or "poetr" in e or "translation" in e:
        return "multilingual"
    if "hellaswag" in e or "winogrande" in e or "arc" in e:
        return "commonsense"
    if "mmlu" in e:
        return "knowledge"
    return "general"


def complexity_for(solve_rate: float) -> str:
    if solve_rate >= LOW_MIN:
        return "low"
    if solve_rate <= HIGH_MAX:
        return "high"
    return "medium"


def first_of_list_literal(raw: str) -> str:
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, (list, tuple)) and parsed:
            return str(parsed[0]).strip()
    except (ValueError, SyntaxError):
        pass
    return str(raw).strip()


def build_example(g: pd.DataFrame) -> dict:
    perf = g["performance"].astype(float)
    solve_rate = float((perf >= SOLVE_THRESHOLD).mean())
    mean_perf = float(perf.mean())
    eval_name = str(g["eval_name"].iloc[0])

    complexity = complexity_for(solve_rate)
    risk = risk_for(eval_name)
    task_type = task_type_for(eval_name)
    slm_eligible = complexity == "low" and risk != "high"

    prompt_text = first_of_list_literal(str(g["prompt"].iloc[0]))
    best_row = g.loc[perf.idxmax()]
    resp = first_of_list_literal(str(best_row["model_response"]))
    predicted_output_tokens = max(16, int(len(resp.split()) * 1.3))

    required = []
    if task_type in {"math", "code"}:
        required.append(task_type)
    if len(prompt_text.split()) > 400:
        required.append("long_context")

    confidence = round(min(0.99, 0.5 + abs(mean_perf - 0.5)), 2)
    rationale = {"low": "SLM_OK", "medium": "MID_TIER", "high": "ESCALATE"}[complexity]

    label = {
        "complexity": complexity,
        "task_type": task_type,
        "risk": risk,
        "slm_eligible": slm_eligible,
        "cache_eligible": risk == "low",
        "required_capabilities": required,
        "predicted_output_tokens": predicted_output_tokens,
        "confidence": confidence,
        "rationale_code": rationale,
    }
    return {"input": f"PROMPT: {prompt_text}", "output": label, "_cell": (complexity, risk)}


def main() -> None:
    print(f"Loading {PKL} (~1.2 GB, takes a bit)...")
    df = pd.read_pickle(PKL)
    print(f"  {len(df):,} rows, {df['sample_id'].nunique():,} unique prompts")

    by_cell: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for _, g in df.groupby("sample_id", sort=False):
        ex = build_example(g)
        by_cell[ex.pop("_cell")].append(ex)

    print("\nAvailable per (complexity, risk) cell:")
    for c in ["low", "medium", "high"]:
        for r in ["low", "medium", "high"]:
            print(f"  {c:6s} x {r:6s}: {len(by_cell[(c, r)])}")

    # Balance: take PER_CELL from every cell, then split each cell so train/eval/test
    # are all balanced across complexity and risk.
    train, eval_, test = [], [], []
    for cell, items in by_cell.items():
        random.shuffle(items)
        take = items[:PER_CELL]
        n = len(take)
        n_eval = int(n * EVAL_FRAC)
        n_test = int(n * TEST_FRAC)
        eval_ += take[:n_eval]
        test += take[n_eval:n_eval + n_test]
        train += take[n_eval + n_test:]

    for split in (train, eval_, test):
        random.shuffle(split)

    from collections import Counter
    print("\nBalanced output:")
    for name, rows in [("train", train), ("eval", eval_), ("test", test)]:
        comp = Counter(r["output"]["complexity"] for r in rows)
        risk = Counter(r["output"]["risk"] for r in rows)
        print(f"  {name}: {len(rows)}  complexity={dict(comp)} risk={dict(risk)}")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for name, rows in [("train", train), ("eval", eval_), ("test", test)]:
        path = OUT_DIR / f"{name}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for ex in rows:
                fh.write(json.dumps(ex, ensure_ascii=False) + "\n")
        print(f"  wrote {len(rows):,} -> {path}")


if __name__ == "__main__":
    main()
