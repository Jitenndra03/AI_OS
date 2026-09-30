# AI_OS

AI_OS is a Linux **user-space prototype** with three demonstrable components:
workload-aware process optimization, a constrained natural-language terminal
assistant, and isolation for inspecting and running downloaded scripts. It uses
existing Linux interfaces; it is not a replacement kernel or a new operating system.

| Component | Implemented demonstration | Boundary |
|---|---|---|
| Process optimization + ML | Live monitoring, sustained workload detection, Random Forest advisory labels, explicit policy, journaled controls and recovery | Observation by default; synthetic demo exercises control logic without changing host resources |
| Natural-language CLI | Eight offline read-only intents, command preview, explicit approval, dedicated PTY, timeout/cancel/output limits | Arbitrary generated Bash and automatic administration are intentionally unsupported |
| Artifact sandbox | Private quarantine, static inspection, verified bubblewrap isolation, bounded Python/shell execution and JSON reports | An isolation test is not a malware verdict or a guarantee against supply-chain attacks |

## Start in a Linux VM

Use an ordinary user account on Ubuntu 24.04 or Debian 12+ with Python 3.11+.
The complete [VM setup and demonstration guide](docs/VM_DEMO_GUIDE.md) includes a
rehearsal script, expected results, recovery, and capability troubleshooting.

```bash
# Installs VM system dependencies only because --install-system is explicit.
bash scripts/setup_vm.sh --install-system
source .venv/bin/activate
python doctor.py --require-sandbox
python scripts/verify.py --require-sandbox
python demo.py --output-dir artifacts/demo --require-sandbox
```

The default demo needs no API key or privilege escalation. Setup needs network
access to install dependencies; demonstrations work offline afterward. If the VM
cannot provide verified isolation, the required sandbox check fails and execution
is refused. Review [validation results](docs/VALIDATION_REPORT.md) for what was
actually tested in the development environment.

## Use each component

```bash
# Live observation; no resource changes and no model retraining.
python main.py --no-ml-training
# Deterministic detection only.
python main.py --no-ml
# A single initial sample (CPU/IO rates need subsequent samples).
python main.py --once --no-ml-training

# Optional reproducible SYNTHETIC ML training, never a real-accuracy benchmark.
python -m scripts.train_demo_model --output-dir artifacts/demo-model
python main.py --ml-model artifacts/demo-model/workload_model.json --no-ml-training

# Preview a supported intent, then approve interactively.
python -m nli.cli_interface --offline -c "show memory usage"
# --yes is explicit approval for this one read-only command.
python -m nli.cli_interface --offline --yes -c "show disk usage"

# Inspect without executing; run only a harmless supplied example for the demo.
python -m sandbox.cli inspect examples/sandbox/isolation_demo.py
python -m sandbox.cli doctor
python -m sandbox.cli run examples/sandbox/isolation_demo.py --timeout 5
```

Editable installation also provides `ai-os`, `ai-os-cli`, `ai-os-sandbox`,
`ai-os-demo`, and `ai-os-doctor`. Run the documented VM workflow from this checkout.
See the [ML guide](docs/ml-guide.md), [CLI guide](docs/cli-guide.md),
and [sandbox guide](docs/sandbox-guide.md) for supported options and limitations.

```text
Live monitor -> deterministic sustained workload -> process classification -> policy
                         ^                                  |
                   advisory ML labels                       v
                                              live identity/safety checks
                                                           |
                              recovery watchdog <-> journal <-> Linux controls

User intent -> constrained proposal -> preview/approval -> dedicated PTY
Downloaded file -> quarantine/inspection -> verified isolation -> behavior report
```

ML refines an already established HEAVY_UNKNOWN workload label. It cannot choose
target PIDs, relax protection, or grant action authority. Model persistence uses
validated JSON trees; runtime ML never loads pickle files. Continuous observation
collects heuristic-labelled samples by default; `--no-ml-training` disables
collection and training. `--ml-train-now` trains from collected data only if data
and validation gates pass. Synthetic data demonstrates the pipeline, not measured
real-world accuracy.

Sampling defaults to five seconds, workload activation to 15 seconds, and release
to 20 quiet seconds. Protect a known application tree with `--foreground-pid PID`;
the pin includes start time to detect PID reuse. Terminal foreground dependencies
are protected automatically. GUI active-window detection is not implemented.

## Explicit background policy

Copy and edit `examples/optimizer-policy.json`. Its placeholder matches no real
application. Choose an exact executable that you understand is optional background
work; do not opt in a shared interpreter such as Python to target just one script.
Rules apply to all instances of the exact executable except protected processes.

```bash
python main.py --policy examples/optimizer-policy.json
python main.py --policy examples/optimizer-policy.json --apply
```

`--apply` is required for changes. It starts an independent recovery worker before
allowing any action. Existing pending actions are restored before new work starts.

Renicing requires `CAP_SYS_NICE` or a sufficient **target process** `RLIMIT_NICE` to
restore the original priority. When restoration is not possible, AI_OS refuses the
change. It does not request sudo or grant capabilities automatically. Do not grant
capabilities to a general-purpose Python interpreter. See the optional narrowly
configured systemd service template in `deploy/` and the deployment notes below.

Supported explicit strategies:

| Strategy | Behavior | Requirements |
|---|---|---|
| `RENICE` | Lower priority to absolute `nice` target; save/restore original | Restoration permission, real CPU contention |
| `CPU_LIMIT` | Dedicated cgroup with `cpu.max` | Delegated cgroup v2 subtree, enabled CPU controller |
| `CGROUP` | CPU quota and/or soft `memory.high` limit | Relevant controllers and memory headroom |
| `SUSPEND` | Guarded stop/resume | `BACKGROUND_OPTIONAL`, policy `allow_suspend: true`, CLI `--allow-suspend`, no IO history or children |

Stronger strategies are selected only by explicit policy, never automatically after
renicing. CPU quota percentages are percentages of **one logical CPU** (1–100).
Memory pressure protection uses `memory.high`, not an OOM-triggering `memory.max`.
The configured soft ceiling must remain at least 25% above current RSS; it limits
future growth rather than promising immediate memory reclamation.

Example cgroup rule:

```json
{
  "background_processes": [{
    "executable": "/absolute/path/to/background-worker",
    "strategy": "CGROUP",
    "cpu_quota_percent": 25,
    "memory_high_bytes": 1073741824
  }]
}
```

Pass `--cgroup-root /sys/fs/cgroup/.../delegated-subtree`. The acting user must own
that subtree, and target processes must already be inside it. Controllers must be
enabled by the administrator/service manager. AI_OS creates private child groups;
it never enables controllers globally, moves system services into its scope, or
changes cgroup ownership. The source group must remain available for restoration.

## Safety and recovery

- Unknown/inaccessible processes and root/other-user processes are never eligible.
- Structural protections cover init/kernel tasks, critical system/desktop services,
  mixed-UID privileged tasks, foreground dependencies, workload trees, and AI_OS.
- Actions require exact executable opt-in, confidence, sustained user work, actual
  pressure, and meaningful resource usage by the target.
- Live PID/start time, executable, owner, status, and foreground checks precede
  controls. Process names alone cannot authorize an action.
- No routine kill, arbitrary shell execution, or LLM control exists in this path.
- Private versioned JSON records are persisted before changes using atomic replace
  and fsync. A lifetime flock prevents two controllers sharing the same journal.
- Workload completion, foreground transitions, stale/incomplete monitoring, normal
  shutdown, and restart recovery trigger restoration. A pipe-connected watchdog
  can recover after the main optimizer is killed unexpectedly.
- Cgroup recovery also restores children born in the managed group. Failed recovery
  remains recorded and visible; the watchdog retries rather than forgetting it.

Manual recovery uses the same UID, state path, and cgroup root as the original run:

```bash
python main.py --recover
```

Default state: `data/runtime/optimizer-state.json` (private directory and files).
Use `--state-file` to choose a dedicated directory. A system service must use a state
path and runtime account it can read/write; do not share journals across different
runtime accounts. A non-root process can only manage its own UID; a privileged
instance must explicitly choose a non-root `--target-uid`.

The watchdog must survive to recover immediately. If both it and the main process
are killed, restore on the next launch or use `--recover`. Permission loss, changed
executables, outside interference, or deletion of the source cgroup may require
operator intervention. A journal is recovery evidence, not a guarantee that the OS
will always allow restoration.

## Checks and presentation

```bash
python scripts/verify.py --require-sandbox
python -m pytest -q
python benchmark.py --mode observe --runs 3 --duration 2 --output artifacts/observation.json
```

Verification writes `artifacts/verification/report.json` and per-check logs.
The combined demo writes `artifacts/demo/report.json`. The observer benchmark
measures observation overhead only; it cannot establish optimization gains.
The optional `--mode renice` benchmark applies/restores priority only on its own
bounded disposable worker and reports failure when permissions are inadequate.
See the VM guide before using its privileged mode in a disposable VM.

Tests cover pure policy, fake cgroups, identity/permission failures, durable
recovery, ML validation, CLI execution boundaries, and real harmless sandbox
execution when isolation is available. Unit tests do not modify production
process priorities or host cgroups. The older `benchmark_report.html` is historical
and must not be presented as evidence for the current implementation.

The [slide-generation brief](docs/AI_OS_PRESENTATION_BRIEF.md) is ready to give an
LLM to create a presentation. Use [current validation evidence](docs/VALIDATION_REPORT.md)
for test results, and keep synthetic demonstrations labelled.

## Remaining scope

This is a bounded VM demonstration prototype, not a production security boundary
or a general autonomous administrator. Background eligibility requires explicit
executable rules; the system does not guess which applications are unnecessary.
GPU telemetry, desktop focus adapters, broad package installation/testing,
independently labelled real-workload evaluation, and end-to-end performance
improvement measurements remain future work. Resource-control privileges and
cgroup delegation must be validated on the target VM. Sandbox CPU/memory limits
are per-process and its tmpfs has no aggregate quota; cap the disposable VM and
use only the supplied harmless examples during presentation.
