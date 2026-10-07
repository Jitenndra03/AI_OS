# AI_OS validation record

Validated on **2026-09-30** in the development environment: Ubuntu 26.04.1 LTS,
Linux 7.0.0-34-generic, Python 3.14.4, ordinary UID 1000. This is evidence for
this checkout and environment, not a claim that a fresh Ubuntu 24.04/Debian VM
has already been booted and tested. Run the same checks inside the target VM.

## Required verification

```bash
.venv/bin/python scripts/verify.py --require-sandbox
```

All six checks passed. Machine-readable evidence and logs are generated under
`artifacts/verification/` (generated artifacts are intentionally gitignored).

| Check | Actual result |
| --- | --- |
| Dependency consistency | `pip check`: no broken requirements |
| Environment preflight | Required Linux/Python/dependencies/procfs and real sandbox probe passed |
| Complete regression suite | **660 passed in 9.74 seconds; no failures or skips** |
| Optimizer observation | Real `main.py --once --no-ml-training --ml-model <temporary absent model>` exited 0 |
| Integrated offline demonstration | Optimization simulation, ML, CLI and real sandbox execution all passed |
| Python source syntax | 76 files compiled for syntax without errors |

The tests include session-aware foreground protection, unknown-session conservative
handling, PID/executable/UID revalidation, shared model-class import consistency,
recovery and state integrity, cgroup fixtures, ML data/model rejection, PTY approval
and process cleanup, and actual harmless bubblewrap execution when available.

## Additional checks performed

- `bash scripts/setup_vm.sh --venv .venv`: completed, including requirements,
  editable package build/install and dependency check. This reused the existing
  environment; it did not install system packages or simulate a clean VM.
- Installed `ai-os --once --no-ml-training --ml-model <absent path>`,
  `ai-os-cli --offline --yes -c 'show memory usage'`, and `ai-os-sandbox doctor`
  exited successfully.
- `python demo.py --output-dir artifacts/demo --require-sandbox`: completed.
  The synthetic process actuator changed nice 0 → 10 → 0, with real journal
  phases preceding simulated changes and no remaining records.
- Synthetic ML generator trained on 700 samples across seven states, evaluated
  the final 140 chronological samples, exported JSON, reloaded, and predicted
  the supplied compilation fixture. These results are not real-world accuracy.
- Sandbox probe confirmed host sentinel and `/etc` hidden, runtime read-only,
  cleared environment, isolated work directory, and distinct user, mount, PID
  and network namespaces. The bundled harmless artifact ran inside isolation.
- One bounded baseline/observer pair completed using 0.5 foreground CPU seconds
  per trial: baseline wall time 1.06745 s, observer wall time 1.06702 s. This was
  a smoke check of the harness and **does not establish a performance gain**.
  Raw local report: `artifacts/observation-smoke.json`.
- Setup shell syntax/help and `git diff --check` passed.

## NLI extension validation

The complete suite now includes 194 NLI tests. They cover exact package names,
update → install → update sequencing, dependency simulation approval, rejected
sudo bypass attempts, empty/failed password handling, isolation of passwords
from APT stdin, cancellation that waits for child exit, and no remaining package
steps after failure. Real harmless PTYs confirm hidden password input is absent
from both streamed and captured output and that terminal settings are restored.

File-search tests verify filename/glob matching and symlink exclusion. Bash tests
cover syntax-only parsing with a clean environment, generated-code nonexecution,
malformed online responses, quoted template parameters, path traversal/symlink
rejection and exclusive private script creation. CLI preview/save routes are
exercised end to end. The integrated demo now previews a package plan and saves
an offline report script without running it.

Package authentication and mutations use controlled substitutes in tests.
Read-only actual APT simulations and exact package lookups passed. No live sudo
command or package installation/removal was performed on the development host,
and no real password was supplied. Test a password-entered transaction separately
inside the disposable VM. Sudoers determines password verification; NOPASSWD and
no-cache policies have the limitations described in [cli-guide.md](cli-guide.md).
Online Bash generation is tested with substitute provider responses; no live
Gemini call or API credential is required for the validated offline demo.

## Scope that remains environment-dependent

The current account lacks CAP_SYS_NICE. Live privileged renice/restoration,
live delegated-cgroup CPU/memory changes, and suspension were not exercised on
production processes. They have regression coverage using controlled fixtures;
the guide provides a separate optional disposable-worker renice benchmark for
an appropriately configured disposable VM. The standard combined demonstration
uses an explicitly synthetic optimizer actuator and requires no such privilege.

No independently labelled real-application ML evaluation, broad malware or
supply-chain detection validation, online Gemini API call, or end-to-end
optimization speedup claim is established here. The CLI now supports canonical read-only operations, password-gated APT plans,
and reviewed Bash authoring; authored scripts are not automatically executed. The sandbox is a bounded script demonstrator with the
resource/isolation limitations described in its guide, not a guarantee that an
arbitrary downloaded application is safe.

## Repeat before presenting

Follow [VM_DEMO_GUIDE.md](VM_DEMO_GUIDE.md). Require live isolation when presenting
all three components:

```bash
source .venv/bin/activate
python doctor.py --require-sandbox
python scripts/verify.py --require-sandbox
python demo.py --output-dir artifacts/demo --require-sandbox
```

Read `artifacts/verification/report.json`, the tests log, and
`artifacts/demo/report.json`. Any required failure returns nonzero. A sandbox
execution capability failure is never replaced with host execution. Without
`--require-sandbox`, unavailable isolation is explicitly reported as a partial
demo; that is not evidence of a complete three-component demonstration.
