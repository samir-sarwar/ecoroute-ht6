from __future__ import annotations

TRANSITIONS = {
    "draft": {"generating", "approved", "failed"},
    "generating": {"review_required", "failed"},
    "review_required": {"approved", "failed"},
    "approved": {"validating", "failed"},
    "validating": {"queued", "failed"},
    "queued": {"training", "failed"},
    "training": {"evaluating", "cancelling", "failed"},
    "evaluating": {"completed", "failed"},
    "completed": {"deploying", "exported"},
    "deploying": {"deployed", "failed"},
    "deployed": {"exported"},
    "cancelling": {"cancelled", "failed"},
    "failed": set(),
    "cancelled": set(),
    "exported": set(),
}


def validate_transition(current: str, target: str) -> None:
    if target not in TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid training state transition: {current} -> {target}")
