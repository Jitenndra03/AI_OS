"""Opt-in policy parsing must fail closed on malformed or dangerous rules."""
import json
import pytest
from src.optimization.config import load_rules
from src.optimization.models import Action, Category

def write(tmp_path, payload):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(payload))
    return path

def test_no_policy_means_no_opt_ins():
    assert load_rules(None) == ()

def test_valid_exact_executable_policy_and_explicit_suspension(tmp_path):
    rules = load_rules(write(tmp_path, {"background_processes": [
        {"executable": "/opt/batch"},
        {"executable": "/opt/optional", "category": "BACKGROUND_OPTIONAL",
         "strategy": "SUSPEND", "allow_suspend": True}]}))
    assert rules[0].strategy == Action.RENICE and rules[0].nice == 10
    assert rules[1].category == Category.BACKGROUND_OPTIONAL and rules[1].allow_suspend

@pytest.mark.parametrize("payload", [[], {}, {"background_processes": {}},
    {"background_processes": [], "ignored": True}, {"background_processes": ["batch"]}])
def test_invalid_top_level_schema(tmp_path, payload):
    with pytest.raises(ValueError):
        load_rules(write(tmp_path, payload))

@pytest.mark.parametrize("changes", [
    {"executable": "batch"}, {"executable": "/tmp/../usr/bin/batch"}, {"executable": "/a\x00b"},
    {"category": "CRITICAL_SYSTEM"}, {"strategy": "KILL"}, {"strategy": "RESTORE"},
    {"nice": True}, {"nice": -1}, {"nice": 20}, {"nice": 1.5},
    {"cpu_quota_percent": 0}, {"cpu_quota_percent": 101}, {"cpu_quota_percent": float("nan")},
    {"memory_high_bytes": 1024}, {"memory_high_bytes": 32*1024*1024},
    {"allow_suspend": "yes"}, {"strategy": "SUSPEND"},
    {"strategy": "SUSPEND", "allow_suspend": True}, {"shell_command": "echo nope"},
])
def test_invalid_rule_is_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        load_rules(write(tmp_path, {"background_processes": [{"executable": "/opt/batch", **changes}]}))

def test_duplicate_executable_rule_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="Duplicate"):
        load_rules(write(tmp_path, {"background_processes": [{"executable": "/opt/batch"}]*2}))
