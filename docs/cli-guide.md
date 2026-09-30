# Natural-language CLI

The CLI works offline without credentials. It proposes a finite read-only
operation, displays its exact arguments and working directory, and asks for
explicit approval before starting a separate PTY-backed process session.
An empty response or EOF declines execution.

From the repository root with the project environment installed:

```bash
.venv/bin/python -m nli.cli_interface --offline -c 'show memory usage'
.venv/bin/python -m nli.cli_interface --offline
```

For an explicitly authorized noninteractive demo, approve a single request with
`--yes`. This option requires `-c`; it cannot enable blanket approval in the REPL.

```bash
.venv/bin/python -m nli.cli_interface --offline --yes -c 'system summary'
.venv/bin/python -m nli.cli_interface --offline --yes --workspace . -c 'find files'
```

| Request | Behavior |
| --- | --- |
| `show CPU usage` | Sample CPU usage over a short interval |
| `show memory usage` | Physical memory and swap summary |
| `show disk usage` | Disk space for the approved working directory |
| `show top processes` | Ten processes ordered by sampled CPU usage |
| `system summary` | Uptime, CPU, memory, and disk summary |
| `current directory` | Print the approved working directory |
| `list files` | List up to 1,000 entries in that directory |
| `find files` | Find up to 1,000 regular files, at most four directory levels deep |

File listing/search does not open file contents. Search skips symlinks and does
not intentionally traverse outside the approved directory. `--workspace PATH`
selects that directory (default: the invocation directory). This is a read-only
application policy, not kernel filesystem isolation; concurrent filesystem
changes are not a sandbox boundary.

The CLI does not accept arbitrary Bash, pipes, substitutions, redirects, root
operations, writes, deletion, service changes, or process priority changes.
Unknown or compound requests explain the supported scope and execute nothing.
It is intentionally a limited, reviewable demo rather than a general shell
safety checker.

Every proposal is immutable. The execution entry point independently checks its
schema and canonical operation arguments, then verifies approval against a
SHA-256 digest of every proposal field, including the working directory.
Changing a field invalidates that approval. The digest binds consent to content;
it is not authentication against trusted Python code in the same process.

The child uses a dedicated session and PTY with merged output. Ctrl-C,
`--timeout SECONDS` (default 30, maximum 300), and output overflow terminate its
entire process group and reap the direct child. `--max-output BYTES` defaults to
65,536 bytes (maximum 1 MiB). Truncated output is marked, and the child exit
status is reported. Exit code 0 means success; 1 means declined or failed
execution; 2 means an unsupported request or invalid CLI arguments.

Optional online classification is explicitly enabled with `--online`. Install
`google-genai` separately and set `GEMINI_API_KEY` and `GEMINI_MODEL` in the
process environment. The CLI never loads `.env` files. Known offline requests
remain local; unfamiliar requests may be sent to Gemini for classification.
Online responses can only select from the same operations; generated shell
code is never accepted. Missing SDK/configuration or provider errors produce a
safe unsupported-request result. Provider exception details and credentials are
not logged.

The Python API uses the same boundary:

```python
from nli.nli_parser import parse_user_input
from nli.safety_validator import approval_digest
from nli.command_executor import execute_proposal

proposal = parse_user_input('show memory usage', offline=True)
print(proposal.script, proposal.cwd)
if input('Execute? [y/N]: ').strip().lower() == 'y':
    result = execute_proposal(proposal, approved_digest=approval_digest(proposal))
    print(result.output, result.exit_code)
```

`execute_proposal` also accepts a `threading.Event` as `cancel_event`. Its result
contains `success`, `output`, `exit_code`, `timed_out`, `cancelled`, and
`truncated`; legacy `(success, output)` unpacking remains available.
