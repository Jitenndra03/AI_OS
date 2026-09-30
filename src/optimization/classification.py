"""Conservative process categories; only explicit executable rules enable action."""

from collections import Counter
import math
import os
from pathlib import PurePosixPath

from src.optimization.models import (
    Action, BackgroundRule, Category, Classification, ProcessMetrics,
    SystemSnapshot, Workload,
)
from src.optimization.safety import protected_tree, protection_reason


_IMPORTANT_NAMES = frozenset({
    "code", "code-insiders", "codium", "vim", "nvim", "emacs", "idea", "pycharm",
    "firefox", "chrome", "chromium", "chromium-browser", "brave", "brave-browser",
    "konsole", "gnome-terminal", "gnome-terminal-server", "kitty", "alacritty",
    "bash", "zsh", "fish", "tmux", "screen", "ssh", "gpg-agent", "ssh-agent",
    "python", "python2", "python3", "node", "nodejs", "java", "ruby", "perl",
    "cargo", "rustc", "gcc", "g++", "cc", "clang", "clang++", "make", "cmake",
    "ninja", "go", "jupyter", "jupyter-lab", "R", "Rscript", "blender", "obs",
    "kdenlive", "shotcut", "ffmpeg", "gimp", "krita", "inkscape", "libreoffice",
    "soffice.bin", "steam", "gamescope", "wine", "wine64", "wineserver",
})
_CRITICAL_REASONS = frozenset({
    "Kernel or init process", "Root, system, or another user's process",
    "AI_OS itself", "Critical system/session/desktop service", "Kernel service",
    "System service cgroup",
})
_NORMAL_STATUSES = frozenset({"running", "sleeping", "disk-sleep", "idle", "waking", "waiting", "locked", "stopped"})
_BACKGROUND_CATEGORIES = frozenset({Category.BACKGROUND_SAFE, Category.BACKGROUND_OPTIONAL})


def _important_name(name: str) -> bool:
    # Interpreter names are protective context only, never background eligibility.
    return name in _IMPORTANT_NAMES or (
        name.startswith(("python2.", "python3."))
        and name.rsplit(".", 1)[-1].isdigit()
    )


def _valid_identity(process: ProcessMetrics) -> bool:
    try:
        return (type(process.pid) is int and process.pid > 0
                and type(process.uid) is int and process.uid >= 0
                and type(process.create_time) in (int, float)
                and math.isfinite(process.create_time) and process.create_time > 0
                and type(process.exe) is str and process.exe.startswith("/")
                and "\x00" not in process.exe and ".." not in process.exe.split("/"))
    except (OverflowError, TypeError):
        return False


class ProcessClassifier:
    """Keep unknown processes unchanged and opt in only exact executables.

    Process names can strengthen protection, but can never authorize modification.
    Foreground/workload and controller descendants are expanded before ancestors,
    so a selected process's siblings do not accidentally become selected too.
    """

    def __init__(self, rules: tuple[BackgroundRule, ...] = (), owner_uid=None, self_pid=None):
        self.owner_uid = os.getuid() if owner_uid is None else owner_uid
        self.self_pid = os.getpid() if self_pid is None else self_pid
        self.rules = {}
        for rule in rules:
            if (not isinstance(rule, BackgroundRule) or not isinstance(rule.executable, str)
                    or not rule.executable.startswith("/") or "\x00" in rule.executable
                    or ".." in rule.executable.split("/")
                    or rule.category not in _BACKGROUND_CATEGORIES
                    or rule.strategy not in {Action.RENICE, Action.CGROUP, Action.CPU_LIMIT, Action.SUSPEND}):
                raise ValueError("Background rules require exact absolute executables and background categories")
            if rule.executable in self.rules:
                raise ValueError("Duplicate executable rule")
            self.rules[rule.executable] = rule

    def classify(self, snapshot: SystemSnapshot, workload: Workload) -> list[Classification]:
        controller_tree = protected_tree(snapshot, {self.self_pid})
        foreground_tree = protected_tree(snapshot, {p.pid for p in snapshot.processes if p.foreground}, respect_sessions=True)
        # Detector output already includes ancestors. Expanding descendants
        # again would pull in unrelated siblings via shared shell/init parents.
        workload_tree = workload.protected_pids
        counts = Counter(p.pid for p in snapshot.processes)
        return [self._classify_one(p, snapshot, controller_tree, foreground_tree,
                                   workload_tree, counts[p.pid] > 1)
                for p in snapshot.processes]

    def _classify_one(self, process, snapshot, controller_tree, foreground_tree, workload_tree, duplicate):
        def result(category, reason, confidence=1.0, rule=None):
            return Classification(process, category, reason, confidence, rule)

        if not _valid_identity(process):
            # Init/kernel structure remains recognizable even without an executable.
            if process.pid in {1, 2} or process.kernel_thread or process.ppid == 2:
                return result(Category.CRITICAL_SYSTEM, "Kernel or init process")
            return result(Category.UNKNOWN, "Process identity or executable is uncertain", 0.0)

        reason = protection_reason(process, owner_uid=self.owner_uid, self_pid=self.self_pid)
        if reason in _CRITICAL_REASONS:
            return result(Category.CRITICAL_SYSTEM, reason)
        if process.pid in controller_tree:
            return result(Category.USER_IMPORTANT, "AI_OS ancestor or descendant")
        if process.pid in foreground_tree:
            return result(Category.USER_FOREGROUND, "Foreground process or its dependency")
        if process.pid in workload_tree:
            return result(Category.USER_IMPORTANT, "Active workload process or its dependency")
        if process.exe.endswith(" (deleted)"):
            return result(Category.SUSPICIOUS, "Executable was deleted; requires inspection, not evidence of malware", 0.5)
        if reason:
            return result(Category.UNKNOWN, reason, 0.0)
        if duplicate or not snapshot.complete:
            return result(Category.UNKNOWN, "Process snapshot is incomplete or contains duplicate PIDs", 0.0)
        # Keep an explicitly opted-in stopped target eligible for journal
        # retention. Planning and execution independently forbid a new action
        # on stopped processes; workload release still restores our own stop.
        if process.status not in _NORMAL_STATUSES:
            return result(Category.UNKNOWN, "Process is not in a normal actionable status", 0.0)

        rule = self.rules.get(process.exe)
        if rule is not None:
            return result(rule.category, "Exact executable explicitly opted into background management", rule=rule)
        if _important_name(process.name) or _important_name(PurePosixPath(process.exe).name):
            return result(Category.USER_IMPORTANT, "Known user application or runtime; no background opt-in")
        return result(Category.UNKNOWN, "No exact executable background rule; left unchanged", 0.0)
