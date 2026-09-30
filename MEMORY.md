# AI_OS Project Memory

> **Historical notes:** Entries below describe earlier development snapshots and may contain superseded status, architecture wording, or test results. For the current three-component bounded implementation, use the [README](README.md), [VM demo guide](docs/VM_DEMO_GUIDE.md), and the [validation report](docs/VALIDATION_REPORT.md), refreshed by `scripts/verify.py`. AI_OS is a user-space Linux management layer; prior kernel-layer, general safe-Bash, and unimplemented-sandbox descriptions are historical.

> **Last updated**: 2026-09-29
> **Context**: This file captures the full project state, architecture decisions, and work history.

## Project Vision

AI_OS integrates an AI layer between the Linux kernel and user space, with three core components:

1. **Process Optimization** — Optimize system performance by managing background processes when the user runs heavy workloads (coding, gaming, editing, etc.)
2. **Natural-Language CLI** — An assistant that takes user intent and generates/executes appropriate bash commands with safety validation
3. **Security Sandbox** — Test downloaded files, apps, and libraries in an isolated environment before they can affect the host system

## Component Status

| Component | Status | Key Files |
|---|---|---|
| Process Optimization | ✅ Working (observation + ML) | `src/optimization/`, `src/ai/`, `main.py` |
| Natural-Language CLI | ✅ Working (unified proposal, REPL, safe executor) | `nli/` |
| Security Sandbox | 🔴 Not started | — |

---

## Component 1: Process Optimization — Architecture

### Pipeline

```
SystemSnapshot → ML Feature Engine → ML Workload Classifier (advisory)
                                   ↕
Monitor → Deterministic WorkloadDetector → Classification → Decision Engine
                                                          → Safety Checks
                                                          → Linux Controller
                                                          ↕
                                                  Persistent Recovery Journal
```

### Key Design Principles

1. **ML predictions are advisory** — the deterministic detector always has final authority
2. **Observation-only by default** — `--apply` required for live process changes
3. **Explicit opt-in only** — processes need exact executable paths in a policy file to be eligible for management
4. **Reversible actions** — original state journaled before any change; recovery on exit/crash
5. **Process identity = PID + start time** — prevents PID-reuse confusion

### Files Created/Modified

#### Deterministic Layer (created by GPT-6 Astra)
- `src/optimization/models.py` — Shared data contracts (WorkloadState, Category, Action, ProcessMetrics, etc.)
- `src/optimization/workload.py` — Sustained resource-based workload detection with process-tree analysis
- `src/optimization/classification.py` — Conservative process classification (protected/eligible)
- `src/optimization/decisions.py` — Pure policy engine: only explicitly authorized actions
- `src/optimization/engine.py` — Ordered control cycle orchestration
- `src/optimization/safety.py` — Protection rules (critical system processes, foreground, etc.)
- `src/optimization/config.py` — Policy file loading with strict validation
- `src/optimization/state.py` — Crash-safe action journal with flock
- `src/optimization/recovery.py` — Independent recovery worker (survives SIGKILL)
- `src/controller/process_controller.py` — Linux resource controller (renice, cgroups, suspend)
- `src/controller/cgroups.py` — Cgroup v2 management
- `src/monitor/metrics_collector.py` — Read-only system sampling with PSI support
- `src/config/settings.py` — Central configuration
- `main.py` — Entry point with --apply/--observe/--recover/--ml modes

#### ML Layer (created by Antigravity, continuing from GPT-6 Astra's interrupted work)
- `src/ai/feature_engine.py` — Feature extraction from SystemSnapshot
  - System-level features (CPU, memory, load, pressure, disk IO)
  - Aggregate process features (counts, distributions, top-N shares)
  - Executable hint one-hot encoding (compilation, gaming, editing, dev, AI/ML)
  - Process-tree shape descriptors (depth, width, root count)
  - 30 features total, all deterministic and finite
- `src/ai/training_data.py` — Labeled data collector
  - Records (features, workload_label) from confident deterministic decisions
  - NORMAL state downsampled (10% rate) to prevent class imbalance
  - Row capping (50k default), thread-safe, restrictive file permissions
- `src/ai/workload_model.py` — RandomForest workload classifier
  - `class_weight="balanced"` for remaining imbalance
  - Cross-validation with minimum accuracy threshold (60%)
  - Feature scaling with StandardScaler
  - Thread-safe training/prediction with separate locks
  - Model persistence with ownership/permission verification
  - Per-class probability estimates for explainability
  - Top feature importances in every prediction
- `src/ai/ml_workload_detector.py` — ML-enhanced detector wrapper
  - Runs deterministic detector on every snapshot (ground truth)
  - ML prediction for early hints (before sustained threshold)
  - Confidence boost when ML agrees with deterministic (+5%)
  - Background retraining thread (every 5 min / 50 new samples)
  - Force-train CLI option (`--ml-train-now`)
  - Disable flags (`--no-ml`)

#### Tests
- `tests/test_ml_layer.py` — 37 tests covering all ML components
- Previous tests: `test_workload.py`, `test_decisions.py`, `test_classification.py`, `test_state.py`, `test_recovery.py`, `test_monitor.py`, `test_cgroups.py`, `test_resource_controller.py`, `test_system_flow.py`, `test_safety_audit.py`, `test_cli.py`, `test_config.py`, `test_data_logger.py`, `test_controller_safety.py`, `test_ai_detector.py`

### How to Run

```bash
# Observe mode (default, no process changes)
.venv/bin/python main.py --once

# Continuous observation with ML learning
.venv/bin/python main.py

# Disable ML
.venv/bin/python main.py --no-ml

# Force ML model training from collected data
.venv/bin/python main.py --ml-train-now

# Apply mode (requires policy file)
.venv/bin/python main.py --apply --policy policy.json
```

### Requirements
```
psutil>=7.2
scikit-learn>=1.8
joblib>=1.5
numpy>=2.4
pandas>=3.0
pytest>=8.0
```

---

## Component 2: Natural-Language CLI — Current State

### What Exists (Completed)
- `nli/models.py` — Unified `CommandProposal` dataclass.
- `nli/nli_parser.py` — Uses `google-genai` SDK (`gemini-3.6-flash`) to generate structured JSON proposals restricted to `NLI_ALLOWED_ACTIONS`.
- `nli/safety_validator.py` — Verifies the proposal against whitelists (e.g., `PROCESS_WHITELIST`) and inspects the bash script for dangerous patterns.
- `nli/command_executor.py` — Executes the script in a subprocess with timeouts and combined stdout/stderr capture.
- `nli/cli_interface.py` — Interactive REPL with colorized output that parses, validates, and seeks explicit user approval before execution.

### How to Run

```bash
# Set your API key in .env, then run the REPL
export $(cat .env | grep -v '^#' | xargs) && PYTHONPATH=./src:. .venv/bin/python nli/cli_interface.py

# Or run a one-off command
export $(cat .env | grep -v '^#' | xargs) && PYTHONPATH=./src:. .venv/bin/python nli/cli_interface.py -c "list top 3 cpu intensive processes"
```

---

## Component 3: Security Sandbox — Current State

### What Exists
Nothing implemented.

### What's Needed
- Download → quarantine → inspect → isolated execution → behavior collection → release decision
- MicroVM or container-based isolation (Firecracker, bubblewrap, etc.)
- Network/filesystem restrictions
- Separate lifecycle from optimization and CLI
- Document/library/binary artifact type handling

---

## Previous Chat History Summary

### Chat 1 (GPT-6 Astra): Project Audit
- Identified drift from "optimize user's priority app" to "detect unusual processes and renice them"
- Found 5 test collection errors, stale process identity bugs, retraining stall
- Documented the turning point at commit `b5436f4`
- Recommended: stabilize foundation, build measurable scenario, complete CLI path, implement sandbox

### Chat 2 (GPT-6 Astra): Component 1 Implementation
- Built the full deterministic optimization pipeline (348 tests)
- Observation mode default, explicit opt-in, reversible actions
- Recovery journal, watchdog process, crash safety
- Left ML for later ("currently a workload-aware optimizer, not yet ML-driven")

### Chat 3 (GPT-6 Astra → Antigravity): ML Layer
- GPT-6 Astra began ML implementation but hit usage limit
- **Antigravity continued**: implemented full ML layer (feature engine, training data collector, RandomForest classifier, ML-enhanced detector wrapper)
- 37 new tests, all passing
- Integrated into main.py with --ml/--no-ml flags

### 2026-09-29: Runtime Log Explanation
- Reviewed user-provided logs from 15:32:36–15:32:57 against `main.py`, `src/optimization/workload.py`, `src/ai/ml_workload_detector.py`, and `src/ai/workload_model.py`.
- The logs contain INFO messages, with no reported errors. Samples appeared roughly every five seconds and observed 450–451 processes in total.
- Workload changed from `NORMAL` to `DEVELOPMENT` at 15:32:47 after the deterministic detector recognized sustained measured activity from development-related user processes. `NORMAL` means no sustained heavy workload was confirmed, not necessarily that the machine was idle.
- `proposals: []` means no non-NOOP optimization actions were proposed in those cycles. Workload detection alone does not require an optimization action. These logs do not establish whether the run used observe or apply mode.
- `ml_early_hint: false` means ML did not flag a workload ahead of deterministic confirmation; this flag alone does not establish model readiness.
- `Insufficient training data: 50 < 100` through `53 < 100` means training was skipped because the available labeled samples were below the minimum of 100. The count stayed at 50 for two logged cycles, then increased to 51, 52, and 53.
- Reaching 100 samples only clears the initial training gate; sufficient samples in at least two workload classes and model validation are also required. Deterministic workload detection continues independently of ML training readiness.
- Repeated insufficient-data messages indicate repeated training attempts below the minimum; log noise was explained, but no code change was requested or made.
- The final `^C` and shell prompt show the user interrupted the program with Ctrl+C and returned to the terminal.
- This session explained the supplied logs and updated this memory file. No tests were run and no broader component-status audit was performed; earlier architecture/status entries remain historical context.

---

## Technical Decisions Log

| Decision | Rationale |
|---|---|
| RandomForest over Isolation Forest | Classification (predict workload type) vs anomaly detection (is this unusual). RF gives class probabilities and feature importances. |
| ML advisory, not authoritative | Safety-critical decisions must be deterministic and auditable. ML can only assist, never override. |
| Separate training/prediction locks | Allow concurrent predictions during background retraining |
| NORMAL downsampling in training data | Prevent 90%+ NORMAL samples from drowning minority workload classes |
| class_weight="balanced" | Further address remaining class imbalance in RandomForest |
| Cross-validation threshold (60%) | Reject models that don't meaningfully distinguish workload types |
| Feature engineering over raw metrics | Tree depth, CPU distribution, hint presence are more stable signals than raw per-process values |
