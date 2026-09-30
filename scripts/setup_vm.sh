#!/usr/bin/env bash
# Explicit VM setup. No services, security-policy changes, or optimizer actions.
set -euo pipefail

usage() {
    cat <<'USAGE'
Usage: bash scripts/setup_vm.sh [--install-system] [--python EXECUTABLE] [--venv DIRECTORY]

Create/update a Python >=3.11 virtual environment, install requirements.txt,
and install this checkout editable with --no-deps.

  --install-system    Explicitly install python3, python3-venv, python3-pip,
                      bubblewrap and util-linux using apt-get (sudo if needed).
  --python EXECUTABLE Python interpreter (default: python3).
  --venv DIRECTORY    Virtual environment (default: CHECKOUT/.venv).
  -h, --help          Show this help without making changes.

Run as your ordinary VM user. System packages need administrator permission;
Python dependencies are installed only in the selected virtual environment.
USAGE
}

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
venv_dir="$repo_root/.venv"
python_command=python3
install_system=false
while (($#)); do
    case "$1" in
        --install-system) install_system=true; shift ;;
        --python|--venv)
            if (($# < 2)) || [[ -z "$2" || "$2" == --* ]]; then
                printf 'Missing value for %s\n' "$1" >&2
                exit 2
            fi
            if [[ "$1" == --python ]]; then python_command="$2"; else venv_dir="$2"; fi
            shift 2
            ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
done

if [[ "$(uname -s)" != Linux ]]; then
    printf 'This demo requires a Linux VM.\n' >&2
    exit 2
fi
if [[ "$install_system" == true ]]; then
    if ! command -v apt-get >/dev/null 2>&1; then
        printf '%s\n' '--install-system requires an apt-based Ubuntu/Debian VM.' >&2
        exit 2
    fi
    privilege=()
    if ((EUID != 0)); then
        if ! command -v sudo >/dev/null 2>&1; then
            printf 'sudo is unavailable; ask the VM administrator to install system dependencies.\n' >&2
            exit 2
        fi
        privilege=(sudo)
    fi
    "${privilege[@]}" apt-get update
    "${privilege[@]}" apt-get install -y python3 python3-venv python3-pip bubblewrap util-linux
fi

if ! command -v "$python_command" >/dev/null 2>&1; then
    printf 'Python interpreter unavailable: %s\n' "$python_command" >&2
    exit 2
fi
"$python_command" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python >=3.11 is required; select it with --python.")'
if [[ ! -f "$repo_root/requirements.txt" || ! -f "$repo_root/pyproject.toml" ]]; then
    printf 'Run this script from a complete AI_OS checkout.\n' >&2
    exit 2
fi
if [[ -e "$venv_dir" && ! -f "$venv_dir/pyvenv.cfg" ]]; then
    printf 'Refusing to reuse a directory that is not a virtual environment: %s\n' "$venv_dir" >&2
    exit 2
fi
if [[ ! -f "$venv_dir/pyvenv.cfg" ]]; then
    if ! "$python_command" -m venv "$venv_dir"; then
        printf 'Virtual environment creation failed. Install matching Python venv support; on the supported VM use --install-system.\n' >&2
        exit 1
    fi
fi
venv_python="$venv_dir/bin/python"
"$venv_python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) and sys.prefix != sys.base_prefix else "The selected virtual environment must use Python >=3.11.")'
"$venv_python" -m pip install --requirement "$repo_root/requirements.txt"
"$venv_python" -m pip install --editable "$repo_root" --no-deps
"$venv_python" -m pip check
printf '\nSetup complete. Activate the environment with:\n  source %q\n' "$venv_dir/bin/activate"
printf 'Then run from the checkout:\n  python doctor.py --require-sandbox\n  python scripts/verify.py --require-sandbox\n'
