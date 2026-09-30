"""Preview, explicitly approve, and run finite read-only operations in a PTY."""
import argparse
from pathlib import Path

from nli.command_executor import execute_proposal
from nli.nli_parser import parse_user_input
from nli.offline_fallback import SUPPORTED_SCOPE
from nli.safety_validator import approval_digest, validate_proposal


class AIOSCli:
    def __init__(self, *, offline=True, yes=False, cwd=None, timeout=30, max_output=65536):
        self.offline, self.yes = offline, yes
        self.cwd = str(Path(cwd or Path.cwd()).resolve())
        self.timeout, self.max_output = timeout, max_output

    def get_approval(self, prompt="Execute this exact proposal? [y/N]: "):
        try:
            return input(prompt).strip().lower() in {"y", "yes"}
        except (KeyboardInterrupt, EOFError):
            return False

    def handle_request(self, user_input):
        proposal = parse_user_input(user_input, offline=self.offline, cwd=self.cwd)
        valid, reason, _ = validate_proposal(proposal)
        if not valid:
            print(proposal.explanation if proposal.action == "unknown" else reason)
            return 2
        digest = approval_digest(proposal)
        print(f"Action: {proposal.action}\nWorking directory: {proposal.cwd}")
        print(f"Explanation: {proposal.explanation}\nProposed argv: {proposal.script}")
        print(f"Approval digest: {digest}")
        if not self.yes and not self.get_approval():
            print("Execution cancelled; no command ran.")
            return 1
        result = execute_proposal(proposal, self.timeout, approved_digest=digest,
                                  max_output_bytes=self.max_output)
        print(result.output, end="" if result.output.endswith("\n") else "\n")
        print(f"Exit status: {result.exit_code}")
        if result.timed_out:
            print("Timed out; the entire child process group was terminated.")
        if result.cancelled:
            print("Cancelled; the entire child process group was terminated.")
        if result.truncated:
            print("Output limit reached; the entire child process group was terminated.")
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
    parser = argparse.ArgumentParser(description="AI_OS finite read-only natural language CLI")
    parser.add_argument("--command", "-c", help="Propose one operation and exit")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--offline", action="store_true", help="Use built-in intents (default)")
    modes.add_argument("--online", action="store_true", help="Optionally classify using Gemini")
    parser.add_argument("--yes", action="store_true", help="Explicitly approve the single -c proposal")
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="Working directory for file operations")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--max-output", type=int, default=65536, help="PTY output byte limit")
    args = parser.parse_args(argv)
    import math
    if not math.isfinite(args.timeout) or not 0 < args.timeout <= 300:
        parser.error("--timeout must be finite and between 0 and 300 seconds")
    if not 0 < args.max_output <= 1048576:
        parser.error("--max-output must be between 1 and 1048576 bytes")
    if not args.workspace.is_dir():
        parser.error("--workspace must be an existing directory")
    if args.yes and not args.command:
        parser.error("--yes requires a single --command")
    cli = AIOSCli(offline=not args.online, yes=args.yes, cwd=args.workspace,
                  timeout=args.timeout, max_output=args.max_output)
    return cli.handle_request(args.command) if args.command else cli.run_repl()


if __name__ == "__main__":
    raise SystemExit(main())
