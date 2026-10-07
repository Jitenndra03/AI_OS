# Natural-language CLI

AI_OS supports offline system checks, filename searches, Debian/Ubuntu package
management, and Bash script authoring. Each operation shows what it will do before
approval. Run the assistant as your ordinary user, not through sudo.

```bash
source .venv/bin/activate
python -m nli.cli_interface --offline
```

You can also use the installed `ai-os-cli` command. Enter or EOF at an approval
prompt cancels. `--dry-run` previews a request without executing it or saving files.

## Find files and check storage

```bash
python -m nli.cli_interface -c 'check storage'
python -m nli.cli_interface --workspace ~/Documents -c 'find files named "*.pdf"'
python -m nli.cli_interface --workspace . -c 'find report.txt'
python -m nli.cli_interface -c 'show memory usage'
```

Other supported requests include CPU usage, top processes, system summary,
current directory, list files and find files. Searches match filenames (including
globs), inspect at most four directory levels, return at most 1,000 regular files,
and skip symlinks. They do not read file contents. `--workspace` sets the search
root; this application policy is not kernel filesystem isolation.

## Install or uninstall applications

Use exact package names available from the VM's configured APT repositories.
Application display names are not automatically mapped to repository packages.
URLs, downloaded `.deb` files, arbitrary APT options and repository configuration
changes are unsupported.

```bash
# Preview only; works without a password and makes no changes.
python -m nli.cli_interface --dry-run -c 'install apps git and curl'

# Execute interactively after reviewing and approving the plan.
python -m nli.cli_interface -c 'install package vlc'
python -m nli.cli_interface -c 'uninstall package vlc'
python -m nli.cli_interface -c 'sudo apt update'
```

An installation runs these steps in order, using `apt-get`, APT's script-oriented
interface:

1. Refresh package indexes (`apt-get update`).
2. Verify exact package names, show a read-only APT dependency simulation, and
   require another explicit `yes` for the displayed changes.
3. Install the packages and required dependencies, refusing removals and retaining
   modified configuration files. An already-installed named package may be upgraded.
4. Refresh package indexes again (`apt-get update`).

**Every privileged APT step requires fresh password entry**, including each index
refresh. A failed/cancelled step stops the remaining steps. Earlier successful
steps are retained; there is no automatic rollback. If the final update fails,
the package may already be installed and the overall operation reports failure.

Uninstallation shows the removal simulation first, including dependent packages
APT would remove. It retains configuration files and does not purge or run
`autoremove`. APT's normal essential-package safeguards remain enabled. A
standalone update only refreshes repository indexes; it does not upgrade packages.

## Password handling and terminal behavior

The package worker has its own controlling PTY. It explains the command and asks
for a hidden password. Noninteractive execution and running AI_OS as root are
rejected. `--yes` cannot bypass package approval or password entry.

The password is sent over an anonymous pipe solely to `sudo -S -v`, after
invalidating cached credentials. After authentication, a separate `sudo -n`
invocation runs the exact APT command with empty stdin. The password never goes
into command arguments, environment variables, logs, or APT/maintainer-script
stdin. It exists briefly in process memory; Python strings cannot be reliably
zeroized. The terminal's sudo timestamp is invalidated afterward, including on
failure.

The machine's sudoers policy decides authentication. Under `NOPASSWD`, AI_OS still
requires nonempty password entry, but sudoers may not validate it. Enforce `PASSWD`
in the VM's sudo policy if actual password verification is mandatory. Policies
that forbid credential caching or restrict `sudo -v` can reject this workflow;
AI_OS reports failure and has no fallback that skips its password gate.

APT operations stream their output. To avoid forcibly killing `dpkg` mid-change,
`--timeout` is not a package-transaction deadline and output limits only truncate
retained output. Ctrl-C requests interruption and waits for subprocess cleanup.
A stuck package transaction may require operator diagnosis in another terminal;
do not power off the VM to dismiss a prompt. Normal read-only operations retain
their timeout and process-group termination behavior.

## Write Bash scripts

```bash
python -m nli.cli_interface --write-script 'report storage usage' --output-script storage.sh
python -m nli.cli_interface --write-script 'backup directory' --output-script backup.sh
python -m nli.cli_interface --dry-run -c 'write a bash script to find files named "*.log"'
```

Offline templates cover storage/system reports, directory backups and file
searches. Other requested scripts can be generated with explicitly enabled online
mode. Install the optional provider SDK with `python -m pip install -e '.[online]'`
and configure `GEMINI_API_KEY` and `GEMINI_MODEL` in your environment, then use
`--online --write-script 'YOUR TASK'`. Requests in online mode may be sent to Gemini;
no live provider request is needed for the offline demonstration.

The CLI previews the complete source and explanation, checks Bash syntax, and asks
before saving. New scripts are private files inside `--workspace`, created without
overwriting files or following symlinks. **Generation and saving never execute the
script.** Syntax checking is not a security review; review generated code before
running it yourself. See [script-guide.md](script-guide.md) for examples and limits.

`--yes` can approve a single read-only command or script save; it cannot enable
blanket REPL approval or privileged execution without a password.

## Execution contract

Normal operations and package plans use immutable typed proposals. Execution
revalidates canonical argv, targets, working directory and all displayed fields;
a digest binds approval to that exact content. Arbitrary shell text is not an
executable proposal. The digest binds consent, not authentication against other
trusted Python code in the same process.

`execute_proposal(..., approved_digest=..., interactive=True)` is required for a
package proposal and checks for a real terminal and non-root user. Its result
includes success, output, exit status, timeout/cancellation and truncation flags.
Interactive output is already streamed and should not be printed a second time.

Read-only defaults are 30 seconds and 65,536 output bytes (configurable up to
300 seconds and 1 MiB). CLI exit code 0 means success; 1 means decline/execution
failure; 2 means unsupported request or invalid configuration. Optional online
intent classification remains limited to the read-only operation vocabulary;
package operations always use the deterministic local parser.
