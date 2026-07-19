#!/usr/bin/env python
"""Print held-out router mismatches for a deployed FreeSOLO adapter."""

from __future__ import annotations

import json
import os
import random
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
BASE_URL = "https://clado-ai--freesolo-lora-serving.modal.run/v1"


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: diagnose_router_deployment.py RUN_ID [N]")
    model = sys.argv[1]
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 50
    key = os.environ.get("FREESOLO_API_KEY")
    if not key:
        raise SystemExit("FREESOLO_API_KEY is required")
    sys.path.insert(0, str(ROOT / "training" / "router"))
    import environment as env  # noqa: PLC0415

    rows = env.load_jsonl(ROOT / "training" / "router" / "dataset" / "test.jsonl")
    random.seed(42)
    rows = random.sample(rows, min(count, len(rows)))

    def infer(index_and_row: tuple[int, dict]) -> tuple[int, dict, dict | None, str]:
        index, row = index_and_row
        client = OpenAI(base_url=BASE_URL, api_key=key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": env.SYSTEM_PROMPT},
                {"role": "user", "content": row["input"]},
            ],
            temperature=0,
            max_tokens=256,
        )
        text = response.choices[0].message.content or ""
        try:
            prediction = json.loads(text)
        except json.JSONDecodeError:
            prediction = None
        return index, row, prediction, text

    completed = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(infer, item) for item in enumerate(rows)]
        for future in as_completed(futures):
            completed.append(future.result())

    for index, row, prediction, raw in sorted(completed):
        expected = row["output"]
        mismatches = []
        if prediction is None:
            mismatches.append("invalid_json")
        else:
            for key_name in ("complexity", "task_type", "risk", "slm_eligible"):
                if prediction.get(key_name) != expected[key_name]:
                    mismatches.append(
                        f"{key_name}:{expected[key_name]}->{prediction.get(key_name)}"
                    )
        if mismatches:
            print(f"[{index}] {', '.join(mismatches)}")
            print(f"  {row['input']}")
            print(f"  predicted={prediction if prediction is not None else raw[:300]!r}")


if __name__ == "__main__":
    main()
