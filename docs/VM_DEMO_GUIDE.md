# AI_OS: a ten-minute Linux VM demonstration

Use a disposable Ubuntu 24.04 or Debian 12+ VM with an ordinary non-root login,
Python 3.11 or newer, and a working Linux user-namespace/bubblewrap configuration.
A practical starting allocation is **2 vCPUs, 4 GiB RAM, and 20 GiB disk**. Keep
these VM limits in place and take a snapshot before setup. Installation needs
network access; the standard three-component demonstration runs offline.

This is a user-space research demo, not a replacement OS. It combines workload
classification and guarded optimization, an approved natural-language CLI, and
artifact inspection/quarantine with optional verified isolation. The supplied
ML fixture is synthetic, and sandbox execution is unavailable when the VM's
kernel or security policy does not permit the required isolation.

## Setup

Copy or clone this checkout into the VM and open a terminal in its root. If using
`ai-os-vm-demo.tar.gz`, transfer it to the VM and extract it first:

```bash
tar -xzf ai-os-vm-demo.tar.gz
cd AI_OS
```

The archive includes source, tests, examples and guides; it excludes virtual
environments, credentials and generated runtime data. Run setup as your ordinary
VM user:

```bash
bash scripts/setup_vm.sh --install-system
source .venv/bin/activate
python doctor.py --require-sandbox
python scripts/verify.py --require-sandbox
```

`--install-system` explicitly authorizes installation of Python/venv support,
bubblewrap, and util-linux through `apt-get`; it may ask for the VM's sudo
password. Without that flag the script changes only the selected Python
environment. It installs the dependency versions from `requirements.txt`, then
installs the checkout with `pip install -e . --no-deps` and runs `pip check`.
It does not enable services, grant capabilities, change kernel settings, weaken
security policy, or start the optimizer.

If system packages are already available:

```bash
bash scripts/setup_vm.sh
```

Select a different installed interpreter or environment when needed:

```bash
bash scripts/setup_vm.sh --python python3.12 --venv .venv-demo
source .venv-demo/bin/activate
```

`doctor` reports actual capabilities; installing bubblewrap alone does not prove
isolation works. The `--require-sandbox` checks must succeed before presenting
all three components as operational. Run `python scripts/verify.py` without that
flag for checks on a restricted host, but retain and report any sandbox skips or
unavailable status. Passing unit tests alone is not evidence of live isolation.

## Ten-minute walkthrough

Allow dependency downloads and setup to finish before the timed presentation.
Keep the terminal at the checkout root with the virtual environment activated.

**Minutes 0–2: preflight and integrated demonstration.**

```bash
python doctor.py --require-sandbox
python demo.py --output-dir artifacts/demo --require-sandbox
```

Review the generated report and component artifacts under `artifacts/demo`.
Report a failed or unavailable component honestly; the demonstration must not
silently execute an artifact outside the sandbox. Use a new output directory
for another presentation run if existing artifact reports are protected against
overwrite.

**Minutes 2–4: workload recognition and observation.**

```bash
python -m scripts.train_demo_model --output-dir artifacts/ml-demo
python main.py --once --ml-model artifacts/ml-demo/workload_model.json --no-ml-training
```

The training command exercises feature generation, model training, evaluation,
and JSON export using an explicitly labeled synthetic dataset. Its metrics do
not establish accuracy on real applications. The main program samples real VM
processes in observation mode; it does not change their priorities by default.
One sample establishes initial rates, so use `python main.py --no-ml-training`
for repeated live samples and Ctrl-C to finish. Review actual proposals and
confidence rather than claiming an action was applied merely because a workload
was recognized.

**Minutes 4–6: natural-language approval and execution.**

```bash
python -m nli.cli_interface --offline -c 'show memory usage'
python -m nli.cli_interface --offline -c 'show top processes'
python -m nli.cli_interface --offline --workspace . -c 'find files'
```

The CLI displays the exact operation, working directory, and approval digest.
Type `yes` to run; Enter or EOF cancels. It supports finite read-only operations,
not arbitrary Bash. Demonstrate a blocked request with:

```bash
python -m nli.cli_interface --offline -c 'delete all files'
```

That rejection exits nonzero intentionally. No API key is needed. For an
explicitly authorized single-command recording, add `--yes` with `-c`. See
[the CLI guide](cli-guide.md) for output bounds, cancellation, and optional
online classification.

**Minutes 6–8: inspect, quarantine, and isolate an artifact.**

```bash
python -m sandbox.cli inspect examples/sandbox/isolation_demo.py
python -m sandbox.cli quarantine examples/sandbox/isolation_demo.py --directory artifacts/quarantine
python -m sandbox.cli doctor
python -m sandbox.cli run examples/sandbox/isolation_demo.py --timeout 5
```

Inspection does not execute the file. Quarantine preserves inert bytes in a
private directory; it is not a malware verdict. Execution requires actual
isolation checks and reports the observed outcome. Use only the bundled benign
fixtures for this presentation. If isolation is unavailable, stop at inspection
and quarantine and report that limitation. See [the sandbox guide](sandbox-guide.md).

**Minutes 8–10: show a measured baseline comparison.**

```bash
python benchmark.py --mode observe --runs 3 --duration 2 --output artifacts/benchmark-observe.json
```

The benchmark alternates baseline and managed trials using bounded disposable
CPU workers. Observe mode measures **monitoring overhead**, not optimization
speedup. Inspect the per-trial outcomes and median wall times; a positive or
negative change is a measurement, not a guaranteed performance claim. Shared
host load, VM scheduling, CPU affinity and short trials all affect the result.

## Optional live optimization comparison

Only in the disposable VM, with explicit administrator approval, measure the
effect of renicing the benchmark's own background worker. Capture the ordinary
user's UID before invoking sudo:

```bash
demo_target_uid="$(id -u)"
sudo .venv/bin/python benchmark.py --mode renice --runs 3 --duration 2 --target-uid "$demo_target_uid" --output artifacts/benchmark-renice.json
```

Run this from the ordinary user shell: the target UID must be nonzero. If using
a custom virtual environment, substitute its Python path. This elevated command
starts disposable workers as that non-root user, targets only those workers,
and checks that their original priority is restored. It does not authorize
arbitrary production-process control. Root runs without an explicit non-root
target UID are rejected. A VM without the required reversible-priority
permissions may safely reject the operation; keep that result as unavailable
rather than treating it as a passed optimization measurement.

Renice mode measures the effect of one selected resource-control action. It is
**not an end-to-end ML optimization benchmark**: synthetic classifier accuracy,
observation overhead, and disposable-worker renice effects are separate pieces
of evidence. The reported percentage is the measured difference between median
baseline and managed wall times. No speedup is assumed.

## Recovery and limitations

Ordinary observation, the offline CLI, and static artifact inspection require
no persistent optimizer changes. The benchmark restores recorded priorities
and terminates its own workers on normal completion or handled failure. The
live optimizer's control path journals process identity and original settings;
normal shutdown attempts recovery. For a separately authorized live optimizer
run with pending state, use the same target UID and state file:

```bash
python main.py --recover --target-uid "$(id -u)" --state-file /path/to/the-original-state.json
```

Recovery may need the same privileges as the original control operation. Read
its result; do not delete a pending state journal to hide failed restoration.
If the VM is forcibly terminated, recovery of live processes cannot be assumed.
Reverting the disposable VM snapshot returns the demonstration environment to
a known state.

Cgroup CPU/memory controls require an explicitly delegated cgroup v2 subtree;
its presence alone is insufficient. The standard walkthrough does not configure
cgroup delegation or enable process suspension. The classifier does not make
unknown background processes eligible for changes: explicit policy and safety
checks still apply. The artifact runner is a constrained demonstrator, not a
claim that arbitrary hostile software can be made safe.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| Python version rejected | Use Ubuntu 24.04/Debian 12+ or select an installed Python >=3.11 with `--python`. |
| `venv` or `ensurepip` unavailable | Install matching venv support using the explicit `--install-system` option, then rerun setup. |
| Imports fail | Activate the environment used by setup; inspect `python -m pip check` and `python doctor.py`. |
| Bubblewrap exists but isolation fails | Read `python -m sandbox.cli doctor`; the kernel, VM/container boundary, or security policy may prohibit required namespaces. Use a compatible disposable VM; do not globally disable host security restrictions. |
| Sandbox report cannot be created | Use a new report filename; the sandbox CLI refuses report overwrite and symlink targets. |
| Renice is blocked or restoration unavailable | Use the report as a failed/unavailable live-control check. Do not grant broad permanent capabilities merely to improve the demo result. |
| No optimization proposals appear | Observation is the default; known workload classification alone does not authorize background changes. |
| Benchmarks vary or managed trials are slower | Reduce competing VM/host work, retain all trial outcomes, and repeat bounded measurements. Do not replace measurements with claimed gains. |
| CLI rejects a sentence | Use one of the supported read-only phrases in the CLI guide. Unsupported requests execute nothing. |

Retain the integrated demo report, verification output, synthetic training
metrics, and raw benchmark JSON alongside presentation materials. Distinguish
checks that ran successfully from capability-dependent checks that were blocked
or skipped.
