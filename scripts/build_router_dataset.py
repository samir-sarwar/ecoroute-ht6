#!/usr/bin/env python
"""Convert RouterBench raw (.pkl) into freesolo router training data.

RouterBench raw has one row per (prompt, model): 36,497 prompts x 11 models,
each with a `performance` score in [0, 1]. The freesolo RouterEnvironment needs
ONE example per prompt, labelled with complexity/risk/etc.

Labeling design (v3 -- make complexity LEARNABLE from prompt text):

  complexity  <- DOMAIN difficulty tier. Each benchmark/domain gets ONE tier from
                 its *average* cross-model solve rate. So every prompt in a domain
                 shares that domain's tier. This is text-detectable (the model can
                 tell a Chinese-poetry prompt from a grade-school-math prompt), the
                 SAME mechanism that makes `risk` learnable (risk F1 = 1.0).
                 v1/v2 used PER-PROMPT solve rate, which is NOT in the text -- the
                 model could not infer "did 11 specific LLMs solve THIS prompt",
                 so complexity F1 stalled at ~0.47 (near the 0.33 random floor).
                 Tradeoff: complexity is now coarser ("how hard is this TYPE of
                 question") -- accepted, since it only governs cost-routing, and
                 all 5 gates need complexity to be classifiable at all.
  risk        <- DOMAIN keyword (legal/medical/financial/security/moral = high).
                 Unchanged from v2; already scores F1 = 1.0.
  slm_eligible<- deterministic: complexity == low AND risk != high.

Balanced by complexity class (the failing axis) so the model can't collapse to
the majority tier. Risk classes stay well-represented as a side effect.

Usage:
    python scripts/build_router_dataset.py
Outputs:
    training/router/dataset/{train,eval,test}.jsonl
"""

from __future__ import annotations

import ast
import json
import random
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
PKL = ROOT / "data" / "routerbench" / "routerbench_raw.pkl"
OUT_DIR = ROOT / "training" / "router" / "dataset"

SOLVE_THRESHOLD = 0.5           # a model "solves" a prompt if performance >= this
# Domain-mean solve-rate -> complexity tier. Tuned so each tier has a healthy pool.
DOMAIN_LOW_MIN = 0.65           # domain avg solve >= 0.65 -> low complexity
DOMAIN_HIGH_MAX = 0.45          # domain avg solve <= 0.45 -> high complexity
PER_CLASS = 2500                # prompts per complexity class before split
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


def domain_complexity_tier(domain_mean_solve: float) -> str:
    if domain_mean_solve >= DOMAIN_LOW_MIN:
        return "low"
    if domain_mean_solve <= DOMAIN_HIGH_MAX:
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


def build_example(g: pd.DataFrame, complexity: str) -> dict:
    perf = g["performance"].astype(float)
    mean_perf = float(perf.mean())
    eval_name = str(g["eval_name"].iloc[0])

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
    return {"input": f"PROMPT: {prompt_text}", "output": label}


def main() -> None:
    print(f"Loading {PKL} (~1.2 GB, takes a bit)...")
    df = pd.read_pickle(PKL)
    print(f"  {len(df):,} rows, {df['sample_id'].nunique():,} unique prompts")

    # Step 1: domain-level mean solve rate -> per-domain complexity tier.
    df["_solved"] = (df["performance"].astype(float) >= SOLVE_THRESHOLD).astype(int)
    per_prompt = df.groupby("sample_id").agg(
        eval_name=("eval_name", "first"), solve_rate=("_solved", "mean")
    )
    domain_mean = per_prompt.groupby("eval_name")["solve_rate"].mean()
    domain_tier = {name: domain_complexity_tier(m) for name, m in domain_mean.items()}
    print("\nDomain -> complexity tier counts:")
    print("  ", Counter(domain_tier.values()))

    # Step 2: build one example per prompt, bucket by complexity class.
    by_class: dict[str, list[dict]] = defaultdict(list)
    for _, g in df.groupby("sample_id", sort=False):
        complexity = domain_tier[str(g["eval_name"].iloc[0])]
        by_class[complexity].append(build_example(g, complexity))

    print("\nPrompts available per complexity class:")
    for c in ["low", "medium", "high"]:
        print(f"  {c:6s}: {len(by_class[c])}")

    # Step 3: balance by complexity class, split each so all splits are balanced.
    train, eval_, test = [], [], []
    for cls, items in by_class.items():
        random.shuffle(items)
        take = items[:PER_CLASS]
        n = len(take)
        n_eval = int(n * EVAL_FRAC)
        n_test = int(n * TEST_FRAC)
        eval_ += take[:n_eval]
        test += take[n_eval:n_eval + n_test]
        train += take[n_eval + n_test:]

    for split in (train, eval_, test):
        random.shuffle(split)

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
