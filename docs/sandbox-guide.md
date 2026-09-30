# Artifact inspection and isolated execution

The `sandbox` package provides a small, usable Linux VM demonstration: inspect a local artifact, copy it into a private quarantine, and explicitly run a Python or POSIX shell script inside bubblewrap. It uses only Python's standard library. Inspection and quarantine do not execute the artifact. A clean static report is **not** a malware verdict.

Run commands from the repository root. Use `.venv/bin/python` in place of `python3` if preferred. For execution, the VM needs its distribution's `bubblewrap` package and `/usr/bin/python3`. Python scripts use that system interpreter with `-I -S`, without the project's virtual environment or third-party dependencies. Shell scripts use `/bin/sh`, regardless of their shebang. ELF binaries, archives, and other formats can be inspected but cannot be executed by this tool.

```bash
python3 -m sandbox.cli inspect examples/sandbox/isolation_demo.py
python3 -m sandbox.cli doctor
python3 -m sandbox.cli run examples/sandbox/isolation_demo.py --timeout 5
python3 -m sandbox.cli run examples/sandbox/hello.sh --timeout 5
```

The Python demo observes whether `/etc/passwd` and a host-home path are absent, a write under `/usr` is denied, an outbound connection to a reserved documentation address is unavailable, and temporary writes work. It sends no application data. These are observed checks, not proof that every possible host access is blocked. Only run the example through the CLI; its probes are intended for the isolated environment.

## Reports and quarantine

Use fresh paths for saved reports: existing files and symlinks are never overwritten. Report files have mode `0600`; their parent directory must already exist. JSON is also printed to stdout. CLI exit code `0` means the requested inspection/quarantine/probe/run completed; `2` means an input error, blocked execution, failed child, or timeout. Standard argparse usage errors also return `2`.

```bash
mkdir -p /tmp/ai-os-demo
python3 -m sandbox.cli inspect examples/sandbox/isolation_demo.py --output /tmp/ai-os-demo/inspection.json
python3 -m sandbox.cli quarantine examples/sandbox/isolation_demo.py --directory /tmp/ai-os-demo/quarantine --output /tmp/ai-os-demo/quarantine.json
python3 -m sandbox.cli run examples/sandbox/isolation_demo.py --timeout 5 --report /tmp/ai-os-demo/run.json
```

Quarantine copies the inspected bytes into `<sha256>.artifact` with mode `0400` inside an owned `0700` directory. The source remains in place. Quarantine is an inert storage convention, not a system-wide enforcement policy: the owner can change permissions. An existing digest is never overwritten. Parent directories must exist. The final source and quarantine-directory components cannot be symlinks; source special files, changing files, and artifacts over 10 MiB are rejected. Filesystem metadata comparisons detect normal replacement or mutation during reading; they are not a defense against a malicious privileged host.

Inspection reports include SHA-256, byte size, a heuristic file type, and static indicators with occurrence counts and up to 20 sampled match line numbers. Indicators flag network APIs, dynamic execution, sensitive paths, destructive command patterns, and encoded-data handling. Comments and harmless code can trigger them; obfuscated or novel behavior can escape them. The inspected bytes are copied once into a private temporary file for execution, mounted read-only inside the sandbox. A later change to the original cannot change the executed snapshot.

## Execution boundary

The `run` subcommand is the explicit execution request. Each run first launches a built-in benign capability probe with the same bubblewrap layout. If bubblewrap is missing, namespace setup is denied, or a check fails, the artifact is not launched. There is **no fallback to execution on the host** and no automatic installation, network access, or host-policy change.

Bubblewrap creates separate user, network, PID, IPC, UTS and mount namespaces using `--unshare-all` and explicit `--unshare-user`; cgroup namespace creation is attempted where supported. Capabilities are dropped, and further user namespace creation is disabled and asserted with `--disable-userns --assert-userns-disabled`. An older bubblewrap lacking these options fails the probe rather than relaxing the boundary. The child receives read-only runtime trees (`/usr`, `/bin`, `/sbin`, `/lib`, `/lib64` where present), a fresh `/proc` and minimal `/dev`, empty home and work directories, an empty `/etc`, and temporary storage. The host home, project directory, environment variables, and host `/etc` are not mounted. Only `PATH`, `HOME`, `LANG`, and `TMPDIR` are supplied. Runtime trees remain visible in full, so do not place secrets in those trees in the demo VM. There is no DNS configuration or host network namespace. No files produced by the child are exported; only bounded stdout/stderr enter the JSON report. Temporary child files disappear on exit, avoiding artifact-export symlink traversal.

Runs have a wall timeout of more than 0 and at most 30 seconds (default 5), per-process CPU and 512 MiB address-space limits, an 8 MiB per-file limit, 64 open file descriptors, no core dumps, and a process/thread limit. Linux counts all threads belonging to the real host UID for `RLIMIT_NPROC`, even across namespaces, so its ceiling is the observed UID baseline plus 64, capped by the inherited hard limit. The actual shared-UID ceiling is reported. This is not a dedicated per-sandbox process quota; other host activity affects it. Stdout and stderr retain at most 64 KiB each, with truncation indicators. Timeout cleanup kills the launcher's process group; bubblewrap's PID namespace and parent-death handling keep descendants tied to the isolated launch.

These limits do not provide aggregate memory, disk, or I/O quotas, seccomp filtering, syscall tracing, behavioral scoring, or protection from kernel exploits. Many processes can consume more aggregate memory, and many temporary files can exceed the per-file limit. Use the component for owned, harmless demonstrations in a disposable VM with an appropriate VM memory/CPU cap. It is not a production malware-analysis service or a reason to run hostile material on a workstation.

## Troubleshooting and verification

`doctor` returns structured `available`, `namespace_unavailable`, `unavailable`, or `probe_failed` status. `namespace_unavailable` includes the actual bubblewrap error; another launcher or probe failure is reported separately. A successful probe observes hidden host files, readonly runtime, cleared environment, isolated workdir, and changed user/network/PID/mount namespaces.

For a missing dependency, install bubblewrap and system Python through the VM distribution's package manager. If the kernel, container runtime, or AppArmor denies namespace creation, ask the VM administrator to review that policy or choose a VM that supports unprivileged bubblewrap. The tool never relaxes host policy. A successful `doctor` on one machine is not a guarantee that another machine supports execution.

```bash
.venv/bin/python -m pytest -q tests/test_sandbox.py
```

Tests cover non-executing inspection, input and symlink rejection, changing sources, quarantine permissions and collisions, timeout validation, fail-closed behavior, snapshot execution, bounded output, timeout cleanup, report handling, and failure classification. The real benign isolation test runs only after a successful probe; otherwise pytest records an explicit skip and reason. Check skipped tests before claiming a VM's isolation demonstration passed.
