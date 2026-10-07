# AI_OS

AI_OS is a **Linux user-space prototype** for workload-aware process management,
a natural-language terminal assistant, and artifact inspection with isolated
script execution. Its original goal is to make Linux easier to use and more
responsive while adding checks around downloaded software.

The current implementation consists of Python programs that use existing Linux
interfaces. It does not modify the kernel or insert a new execution layer between
the kernel and every user-space application. The three components have separate
commands. Downloads are not intercepted automatically, and NLI package installs
are not routed through the sandbox.

## Current implementation and evidence

| Area | Implemented | Validation and limits |
| --- | --- | --- |
| Process optimization | Live monitoring, sustained workload detection, explicit background rules, priority/cgroup/suspend controls, action journal and recovery worker | Live observation tested. The combined demo uses synthetic processes and simulated resource changes. Privileged controls have fixture-based regression coverage; live privileged operation on a target VM remains to be validated. |
| ML workload recognition | Random Forest training, chronological holdout evaluation, JSON model persistence and advisory inference | Synthetic training/export/reload/inference tested. No independently labelled real-workload accuracy or measured optimization benefit is established. |
| Natural-language interface (NLI) | Offline system checks, filename searches, approved PTY execution, APT install/remove plans and Bash authoring | Read-only commands, script saving and harmless PTYs tested. Package authentication/mutations use controlled substitutes in tests; no live password-entered installation/removal has been validated. |
| Artifact sandbox | Static inspection, private quarantine, capability probe and explicit isolated execution of Python/POSIX shell scripts | Real bubblewrap isolation and bundled harmless examples tested on the development host. This is not a general malware detector or evidence of supply-chain attack prevention. |

The latest recorded verification, **2026-09-30**, reports **660 tests passed with
no failures or skips**, plus dependency, source-syntax and integrated-demo checks.
The recorded environment was **Ubuntu 26.04.1 LTS, Linux 7.0.0-34-generic,
Python 3.14.4**. See [the validation record](docs/VALIDATION_REPORT.md) for the
commands, evidence and untested paths. Passing these checks does not guarantee
correctness on another machine or under every failure condition.

## Setup and demonstration

The project requires Linux and Python **3.11 or newer**. The setup script uses APT
for optional system-package installation. The VM guide targets Ubuntu 24.04 or
Debian 12+, but clean installations on those versions have **not** been validated
in the recorded run. Bubblewrap must support the required namespace options, and
the VM's kernel/security policy must permit them. Installation alone does not
prove isolation works.

From the checkout root, as an ordinary non-root user:

```bash
# Explicitly install system dependencies, then create/update .venv.
bash scripts/setup_vm.sh --install-system
source .venv/bin/activate

# Require actual sandbox capability for the full demonstration.
python doctor.py --require-sandbox
python scripts/verify.py --require-sandbox
python demo.py --output-dir artifacts/demo --require-sandbox
```

If system dependencies are already installed, use `bash scripts/setup_vm.sh`
without `--install-system`. Setup installs the Python requirements and this
checkout in editable mode. It does not enable services, configure cgroup
delegation, or grant optimizer capabilities. Dependency installation normally
requires network access. The standard demo subsequently runs offline without an
API key; actual APT transactions and optional online model requests may need
network access.

The combined demo exercises real policy/controller/journal code through a
**synthetic optimizer backend**, trains a **synthetic ML fixture**, runs a read-only
NLI command, previews an APT plan, saves a Bash script without executing it, and
runs a harmless example inside verified isolation. It does not install packages
or change real process priorities. Reports are written to
`artifacts/demo/report.json` and `artifacts/verification/report.json`.

Without `--require-sandbox`, unavailable isolation can produce a partial demo;
that is not a successful demonstration of isolated execution. Follow the
[VM setup and walkthrough guide](docs/VM_DEMO_GUIDE.md) before presenting.

## 1. Process optimization

The optimizer samples process and system metrics, detects sustained user work,
classifies processes, and proposes changes only for explicitly opted-in
background executables. It does not determine automatically which applications
are unnecessary. Observation is the default, and there are no default targets.

```bash
# Observe continuously without collecting ML training data or retraining.
python main.py --no-ml-training

# Use deterministic workload detection without the ML layer.
python main.py --no-ml

# One initial observation; CPU/IO rate counters need subsequent samples.
python main.py --once --no-ml-training
```

Default polling is every five seconds, activation requires 15 seconds of
sustained activity, and release uses a 20-second quiet window. Terminal foreground
dependencies are protected. `--foreground-pid PID` additionally protects a chosen
application tree using PID and process start time. There is no desktop-specific
active-window detector or GPU telemetry.

### Policy and live controls

Edit [examples/optimizer-policy.json](examples/optimizer-policy.json) to identify
an exact background executable before enabling changes. The supplied path is a
placeholder. Rules can match multiple instances of that executable; using a shared
interpreter such as Python does not restrict a rule to one script.

```bash
# Review proposals first.
python main.py --policy examples/optimizer-policy.json

# Explicitly enable controls after editing the policy and checking permissions.
python main.py --policy examples/optimizer-policy.json --apply
```

| Strategy | Implemented behavior | Main requirements |
| --- | --- | --- |
| `RENICE` | Lower the process leader's scheduling priority and record its original value | CPU contention and permission to restore priority: `CAP_SYS_NICE` or sufficient target-process `RLIMIT_NICE` |
| `CPU_LIMIT` | Apply `cpu.max` in a managed cgroup | Explicitly delegated cgroup v2 subtree with the CPU controller enabled |
| `CGROUP` | Apply a CPU quota and/or soft `memory.high` limit | Appropriate delegated controllers and configured memory headroom |
| `SUSPEND` | Stop/resume an explicitly optional background process | `BACKGROUND_OPTIONAL`, policy `allow_suspend: true`, CLI `--allow-suspend`, and additional status/IO/child checks |

Strategies are selected by policy; the optimizer does not escalate automatically
from renicing to suspension. CPU quota percentages refer to one logical CPU.
`memory.high` is a soft limit, requires headroom above current RSS, and does not
guarantee immediate memory reclamation. Suspending a process can still affect
other work if it holds locks; the checks cannot prove the absence of dependencies.

Cgroup operation requires `--cgroup-root` pointing to an already delegated subtree.
The target must already be within that subtree, and the original source group
must remain available for restoration. AI_OS does not enable controllers globally
or set up delegation. Native renice does not cover every thread or descendant.
Numeric-PID cgroup migration also retains a kernel-interface PID-reuse race despite
identity checks around migration.

### Protection and recovery

The planning and execution paths check process identity, executable, owner,
status and protected dependencies. Root-owned processes, processes outside the
configured non-root UID, inaccessible metadata, and identified critical services
are excluded from eligibility. These checks reduce risk; they do not prove that
an opted-in executable is harmless or semantically safe to pause.

Live apply mode starts a recovery worker and writes private action records before
mutations. A journal lock prevents concurrent controllers from sharing the same
state file. Workload release, protection changes, stale observations and shutdown
trigger restoration attempts. Pending actions are recovered before new work.

```bash
python main.py --recover
```

Use the original runtime account, privileges, target UID, state path, and cgroup
root when applicable. The default journal is
`data/runtime/optimizer-state.json`; `--state-file` selects another location.
A privileged optimizer must specify a non-root `--target-uid`.

Recovery can fail after permission loss, executable changes, external interference
or deletion of a source cgroup. If both the optimizer and watchdog die, recovery
must wait for a later launch or manual invocation. Failed actions remain recorded;
restoration is not guaranteed. An optional, uninstalled service template is in
[deploy/ai-os-optimizer.service.example](deploy/ai-os-optimizer.service.example).

### What the ML layer does

ML may refine the label of an already established, protected `HEAVY_UNKNOWN`
workload. It cannot activate work on its own, select target PIDs, or bypass
eligibility and control checks. The deterministic path works without a model.

```bash
# Generate a clearly labelled synthetic dataset and demonstration model.
python -m scripts.train_demo_model --output-dir artifacts/demo-model

# Explicitly load that demonstration model for observation.
python main.py --ml-model artifacts/demo-model/workload_model.json --no-ml-training
```

Continuous ML-enabled observation collects heuristic-labelled samples by default.
`--no-ml-training` disables collection and training; `--ml-train-now` attempts
training from collected data subject to data and validation requirements. Training
uses an 80% chronological training partition and 20% holdout. Agreement with
heuristic labels, especially on synthetic data, is not independently measured
real-world accuracy.

The active workload classifier loads validated JSON trees. Legacy anomaly-analysis
code also remains in `src/ai/`; its older persistence code is separate from the
active workload classifier and does not authorize optimizer actions. See the
[ML guide](docs/ml-guide.md) for model/data validation and training limits.

## 2. Natural-language interface

The CLI maps supported phrases to canonical operations, displays their effect and
arguments, and asks for approval. Execution checks the proposal again and binds
approval to its exact contents. Commands run in a dedicated PTY session; a PTY is
terminal separation, not a filesystem or security sandbox.

```bash
python -m nli.cli_interface --offline
python -m nli.cli_interface -c 'show memory usage'
python -m nli.cli_interface -c 'check storage'
python -m nli.cli_interface --workspace . -c 'find files named "*.py"'
```

Offline requests also cover CPU usage, top processes, system summary, current
directory and file listing. Searches match filenames/globs, skip symlinks, inspect
at most four directory levels and return at most 1,000 regular files. They do not
search file contents. `--workspace` selects the root. This is an application-level
restriction, not protection against all concurrent filesystem changes.

### APT packages and passwords

Package management supports exact Debian/Ubuntu repository package names. It does
not install arbitrary URLs, local `.deb` files, pip/npm packages, or configure
repositories. Application display names are not automatically translated into
package names.

```bash
# Preview without changing packages or prompting for a password.
python -m nli.cli_interface --dry-run -c 'install apps git and curl'

# Interactive operations: each privileged step requires password entry.
python -m nli.cli_interface -c 'install package vlc'
python -m nli.cli_interface -c 'uninstall package vlc'
python -m nli.cli_interface -c 'sudo apt update'
```

Installation runs **APT index update → install → APT index update**, using
`apt-get`. Before install/removal, it verifies exact package names, displays an
APT dependency simulation and asks for another explicit confirmation. Installs
refuse removals; removal can include dependent packages shown by APT, retains
configuration files, and does not request purge/autoremove. Existing named
packages may be upgraded during installation.

Each privileged APT step explains its effect and requires fresh hidden password
entry. Running the assistant as root or using noninteractive package execution is
rejected, and `--yes` does not bypass package approval/passwords. The worker clears
cached credentials, authenticates separately through sudo, runs the exact APT
command, and attempts to clear the timestamp afterward. Passwords are not written
to logs, arguments or environment variables and are not passed to APT stdin.
They briefly exist in process memory.

**Password entry and password verification are different:** sudoers remains the
authentication authority. With `NOPASSWD`, the CLI still asks for nonempty input,
but sudo may not verify that password. Policies that prohibit credential caching
or restrict sudo validation may reject this workflow. The code does not change
those policies. The separate setup script uses ordinary sudo behavior; it does
not use this NLI password mechanism.

Failures stop remaining steps without rolling back earlier ones. A failed final
index update can therefore leave a successfully installed package. APT transactions
have no enforced wall timeout: cancellation requests interruption and waits for
subprocess exit, while output limits only truncate retained output. A stuck
transaction can require operator intervention. Ordinary read-only commands retain
bounded timeouts and process-group cleanup.

### Bash authoring

```bash
python -m nli.cli_interface --write-script 'report storage usage' --output-script storage.sh
python -m nli.cli_interface --write-script 'backup directory' --output-script backup.sh
```

Offline templates cover system/storage reports, directory backups and filename
searches. Other requests require optional online generation through Gemini:

```bash
python -m pip install -e '.[online]'
# Configure GEMINI_API_KEY and GEMINI_MODEL in your environment first.
python -m nli.cli_interface --online --write-script 'Count lines in each text file' --output-script count-lines.sh
```

Online requests may be sent to the configured provider. There is no live-provider
validation in the recorded test run; tests use substitute responses. Known offline
templates remain local. The CLI does not automatically load `.env` files.

Scripts are previewed, checked with Bash's syntax parser, and saved after approval
as new private files inside the workspace. Existing files and symlink destinations
are rejected. **Authored scripts are never automatically executed.** A successful
syntax check does not prove safety, correctness, or agreement with the generated
explanation. Manually running a saved script is outside the NLI password gate.

`--dry-run` previews without executing/saving. `--yes` may approve one read-only
operation or one script save. See the [CLI guide](docs/cli-guide.md) and
[script guide](docs/script-guide.md) for examples, limits and API usage.

## 3. Artifact inspection and sandbox execution

```bash
python -m sandbox.cli inspect examples/sandbox/isolation_demo.py
python -m sandbox.cli quarantine examples/sandbox/isolation_demo.py --directory artifacts/quarantine
python -m sandbox.cli doctor
python -m sandbox.cli run examples/sandbox/isolation_demo.py --timeout 5
```

Inspection reports metadata, a digest, format and simple text-pattern indicators.
Quarantine copies inert bytes into a private directory. Neither operation executes
the artifact or establishes whether it is malicious. Inputs are limited to regular
files up to 10 MiB; symlinks and special files are rejected.

Execution supports Python and POSIX shell scripts only. Python uses the system
`/usr/bin/python3 -I -S`, without project-venv dependencies. Shell scripts use
`/bin/sh`, regardless of shebang, so arbitrary Bash scripts are not necessarily
compatible. The runner does not execute arbitrary applications, binaries, archives
or package/library installations.

Before each run, a harmless capability probe checks the required bubblewrap
isolation. Failure blocks execution; there is no host-execution fallback. The
layout separates namespaces, clears the environment, hides host home/project and
host `/etc`, and exposes runtime directories read-only. Those runtime trees remain
readable in full. Reports contain bounded stdout/stderr; produced files are not
exported.

The default timeout is five seconds, configurable up to 30. Limits include
per-process CPU/address space, per-file size, open files and shared-host-UID process
counts. There are **no aggregate memory/disk/IO quotas, seccomp filter, syscall
tracing or malware-scoring engine**. Namespace isolation shares the host kernel
and does not protect against kernel vulnerabilities. Use the harmless examples
in a disposable VM with resource caps. See the [sandbox guide](docs/sandbox-guide.md)
for the exact boundary and capability requirements.

## Verification, benchmarks and project layout

```bash
python scripts/verify.py --require-sandbox
python -m pytest -q
python benchmark.py --mode observe --runs 3 --duration 2 --output artifacts/observation.json
```

The verifier checks dependencies, environment capability, the complete test suite,
optimizer observation, the combined demo and Python source syntax. It does not
validate live privileged optimizer actions, real password-entered package
transactions, or the optional online provider. Sandbox tests can skip when
isolation is unavailable; use `--require-sandbox` and inspect the logs before
claiming the complete demo passed.

The observation benchmark measures monitoring overhead. The separate `--mode
renice` benchmark measures a priority change on its own disposable worker and
requires suitable restoration permissions; instructions are in the VM guide.
Neither is evidence of end-to-end ML optimization gains. No real-workload speedup
is established. The older `benchmark_report.html` is historical, not validation
of the current implementation.

| Location | Purpose |
| --- | --- |
| `main.py`, `src/monitor/`, `src/optimization/`, `src/controller/` | Observation, policy, controls and recovery |
| `src/ai/` | ML features, collection, training and inference; legacy anomaly-analysis code |
| `nli/` | Intent parsing, proposals, PTY execution, APT worker and script authoring |
| `sandbox/` | Inspection, quarantine, capability probe and isolated execution |
| `doctor.py`, `demo.py`, `scripts/` | Preflight, synthetic/harmless demonstrations, setup and verification |
| `tests/` | Regression tests, fixtures and supported real harmless execution checks |
| `examples/`, `deploy/` | Example policy/scripts and optional service template |
| `docs/` | Component guides, VM walkthrough, validation record and presentation brief |
| `data/`, `logs/`, `artifacts/` | Runtime data and generated outputs; gitignored |

Editable installation exposes `ai-os`, `ai-os-cli`, `ai-os-sandbox`, `ai-os-demo`
and `ai-os-doctor`. The documented workflow runs from the source checkout. For
slides, use [AI_OS_PRESENTATION_BRIEF.md](docs/AI_OS_PRESENTATION_BRIEF.md) alongside
the validation record and keep synthetic examples explicitly labelled.

## Work still required

The original project vision is broader than this prototype. Remaining work includes
independent real-workload ML evaluation, end-to-end performance measurements,
target-VM validation of live controls and password-entered APT transactions,
desktop focus/GPU integration, automatic download interception, broader artifact
and package support, and security evaluation beyond harmless demonstration files.
The repository does not establish production readiness or a guarantee against
system damage, malware, supply-chain attacks, or all runtime failures.
