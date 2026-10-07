"""Preview operations, authenticate package changes, and author reviewed Bash files."""
import argparse
from pathlib import Path
import re
import sys

from nli.command_executor import execute_proposal
from nli.nli_parser import parse_user_input
from nli.offline_fallback import SUPPORTED_SCOPE
from nli.safety_validator import approval_digest, validate_proposal


class AIOSCli:
    def __init__(self, *, offline=True, yes=False, cwd=None, timeout=30, max_output=65536,
                 dry_run=False, output_script="generated-script.sh"):
        self.offline, self.yes = offline, yes
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        self.timeout, self.max_output = timeout, max_output
        self.dry_run, self.output_script = dry_run, output_script

    def get_approval(self, prompt="Execute this exact proposal? [y/N]: "):
        try:
            return input(prompt).strip().lower() in {"y", "yes"}
        except (KeyboardInterrupt, EOFError):
            return False

    def write_script(self, request):
        from nli.script_authoring import generate_script, save_script
        try:
            draft = generate_script(request, offline=self.offline)
            print(f"Script source: {draft.source}\nExplanation: {draft.explanation}")
            print(f"Destination: {self.output_script} (within {self.cwd})")
            print("Bash syntax checked. Review the complete script before using it. Saving does not execute it.")
            print("--- Script preview ---\n" + draft.code + "\n--- End script ---")
            if self.dry_run:
                print("Preview only; no script was saved or executed.")
                return 0
            if not self.yes and not self.get_approval("Save this exact script? [y/N]: "):
                print("Cancelled; no script was saved.")
                return 1
            path = save_script(draft, self.output_script, self.cwd)
            print(f"Saved: {path}\nThe script has not been executed.")
            return 0
        except (ValueError, OSError) as exc:
            print(f"Script not saved: {exc}")
            return 2

    def handle_request(self, user_input):
        if re.match(r'^\s*(?:please\s+)?(?:write|create|generate)\s+(?:me\s+)?(?:a\s+)?(?:bash|shell)\s+script\b', user_input, re.I):
            return self.write_script(user_input)
        proposal = parse_user_input(user_input, offline=self.offline, cwd=self.cwd)
        valid, reason, _ = validate_proposal(proposal)
        if not valid:
            print(proposal.explanation if proposal.action == "unknown" else reason)
            return 2
        digest = approval_digest(proposal)
        print(f"Action: {proposal.action}\nWorking directory: {proposal.cwd}")
        print(f"Explanation: {proposal.explanation}\nProposed argv: {proposal.script}")
        print(f"Approval digest: {digest}")
        if self.dry_run:
            print("Preview only; no command ran.")
            return 0
        if proposal.requires_sudo:
            if not sys.stdin.isatty() or not sys.stdout.isatty():
                print("Package changes require an interactive terminal for password entry. Use --dry-run to preview.")
                return 2
            print("APT may run for several minutes. Cancellation waits for an orderly exit; output limits do not stop APT.")
        if (proposal.requires_sudo or not self.yes) and not self.get_approval():
            print("Execution cancelled; no command ran.")
            return 1
        result = execute_proposal(proposal, self.timeout, approved_digest=digest,
                                  max_output_bytes=self.max_output, interactive=proposal.requires_sudo)
        if not proposal.requires_sudo or result.exit_code is None:
            print(result.output, end="" if result.output.endswith("\n") else "\n")
        print(f"Exit status: {result.exit_code}")
        if result.timed_out:
            print("Timed out; the entire child process group was terminated.")
        if result.cancelled:
            print("Cancellation requested; package worker exited." if proposal.requires_sudo else
                  "Cancelled; the entire child process group was terminated.")
        if result.truncated:
            print("Saved output was truncated; package execution was allowed to finish." if proposal.requires_sudo else
                  "Output limit reached; the entire child process group was terminated.")
        return 0 if result.success else 1

    def run_repl(self):
        print("AI_OS natural language CLI (offline)" if self.offline else "AI_OS natural language CLI (online)")
        print(SUPPORTED_SCOPE)
        print("Type exit or quit to close.")
        while True:
            try:
                request = input("AI_OS> ").strip()
                if request.lower() in {"exit", "quit"}:
                    return 0
                if request:
                    self.handle_request(request)
            except EOFError:
                return 0
            except KeyboardInterrupt:
                print("\nType exit to quit.")


def main(argv=None):
    parser = argparse.ArgumentParser(description="AI_OS approved operations, authenticated APT, and Bash authoring")
    requests = parser.add_mutually_exclusive_group()
    requests.add_argument("--command", "-c", help="Propose one operation and exit")
    requests.add_argument("--write-script", help="Describe a Bash script to preview and save; never execute")
    parser.add_argument("--output-script", default="generated-script.sh", help="New script path inside --workspace")
    parser.add_argument("--dry-run", action="store_true", help="Preview only; no operation or file write")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline", action="store_true", help="Use built-in intents (default)")
    modes.add_argument("--online", action="store_true", help="Use Gemini for unfamiliar read-only intents or Bash authoring")
    parser.add_argument("--yes", action="store_true", help="Approve one read-only operation or script save; never bypass package approval/passwords")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Working directory for file operations")
    parser.add_argument("--timeout", type=float, default=30, help="Read-only timeout; APT waits for orderly completion")
    parser.add_argument("--max-output", type=int, default=65536, help="PTY output byte limit")
    args = parser.parse_args(argv)
    import math
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 300:
        parser.error("--timeout must be finite and between 0 and 300 seconds")
    if not 0 < args.max_output <= 1048576:
        parser.error("--max-output must be between 1 and 1048576 bytes")
    if not args.workspace.is_dir():
        parser.error("--workspace must be an existing directory")
    if args.yes and not (args.command or args.write_script):
        parser.error("--yes requires a single --command or --write-script")
    cli = AIOSCli(offline=not args.online, yes=args.yes, cwd=args.workspace,
                  timeout=args.timeout, max_output=args.max_output, dry_run=args.dry_run,
                  output_script=args.output_script)
    if args.write_script:
        return cli.write_script(args.write_script)
    return cli.handle_request(args.command) if args.command else cli.run_repl()


if __name__ == "__main__":
    raise SystemExit(main())
