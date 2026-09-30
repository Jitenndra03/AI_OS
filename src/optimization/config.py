"""Explicit opt-in policy loading. No executable guessing or shell commands."""

import json
import math
from pathlib import Path

from src.optimization.models import Action, BackgroundRule, Category


def load_rules(path: str | Path | None) -> tuple[BackgroundRule, ...]:
    if path is None:
        return ()
    with Path(path).open() as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict) or set(payload) != {"background_processes"}:
        raise ValueError("Policy requires only a background_processes array")
    if not isinstance(payload["background_processes"], list):
        raise ValueError("background_processes must be an array")
    rules = []
    allowed = {"executable", "category", "strategy", "nice", "cpu_quota_percent",
               "memory_high_bytes", "allow_suspend"}
    for item in payload["background_processes"]:
        if not isinstance(item, dict) or not set(item) <= allowed:
            raise ValueError("Unknown background policy fields")
        executable = item.get("executable")
        if not isinstance(executable, str) or not executable.startswith("/"):
            raise ValueError("Each background rule requires an absolute executable path")
        if ".." in Path(executable).parts or "\x00" in executable:
            raise ValueError("Invalid executable path")
        if any(r.executable == executable for r in rules):
            raise ValueError("Duplicate executable rule")
        category = Category(item.get("category", "BACKGROUND_SAFE"))
        strategy = Action(item.get("strategy", "RENICE"))
        if category not in {Category.BACKGROUND_SAFE, Category.BACKGROUND_OPTIONAL}:
            raise ValueError("Rules can only opt in background categories")
        if strategy not in {Action.RENICE, Action.CGROUP, Action.CPU_LIMIT, Action.SUSPEND}:
            raise ValueError("Unsupported background strategy")
        nice = item.get("nice", 10)
        if type(nice) is not int or not 0 <= nice <= 19:
            raise ValueError("nice must be an integer from 0 to 19")
        quota = item.get("cpu_quota_percent", 25.0)
        if type(quota) not in {int, float} or not math.isfinite(quota) or not 1 <= quota <= 100:
            raise ValueError("cpu_quota_percent must be between 1 and 100 (one CPU)")
        high = item.get("memory_high_bytes")
        if high is not None and (type(high) is not int or high < 16 * 1024 * 1024):
            raise ValueError("memory_high_bytes must be at least 16 MiB")
        suspend = item.get("allow_suspend", False)
        if type(suspend) is not bool:
            raise ValueError("allow_suspend must be a boolean")
        if strategy == Action.SUSPEND and (not suspend or category != Category.BACKGROUND_OPTIONAL):
            raise ValueError("Suspension requires BACKGROUND_OPTIONAL and allow_suspend=true")
        if high is not None and strategy not in {Action.CGROUP, Action.CPU_LIMIT}:
            raise ValueError("memory_high_bytes requires a cgroup strategy")
        rules.append(BackgroundRule(executable, category, strategy, nice, float(quota), high, suspend))
    return tuple(rules)
