"""Shared conservative protection rules used at planning AND execution time."""

import math
import os

from src.optimization.models import ProcessMetrics, SystemSnapshot


CRITICAL_NAMES = frozenset({
    "systemd", "init", "kthreadd", "sshd", "sshd-session", "login", "agetty",
    "gdm", "gdm3", "gdm-session-worker", "sddm", "lightdm", "seatd", "logind",
    "systemd-logind", "systemd-udevd", "udevd", "dbus-daemon", "dbus-broker",
    "polkitd", "NetworkManager", "wpa_supplicant", "iwd", "dhclient",
    "systemd-networkd", "systemd-resolved", "Xorg", "Xwayland", "gnome-shell",
    "gnome-session", "gnome-session-binary", "kwin_wayland", "kwin_x11",
    "plasmashell", "sway", "weston", "Hyprland", "pipewire", "pipewire-pulse",
    "wireplumber", "pulseaudio", "udisksd", "nfsd", "rpcbind", "rpc.mountd",
    "jbd2", "kswapd0", "cryptsetup", "fusermount", "fusermount3",
})


def protected_tree(snapshot: SystemSnapshot, seeds: set[int] | frozenset[int],
                   *, include_descendants: bool = True, respect_sessions: bool = False) -> frozenset[int]:
    """Protect selected subtrees and ancestors, never siblings via an ancestor."""
    by_pid = {p.pid: p for p in snapshot.processes}
    protected = set(seeds)
    if include_descendants:
        while True:
            children = {p.pid for p in snapshot.processes if p.ppid in protected
                        and (not respect_sessions or not p.session_id
                             or not by_pid.get(p.ppid) or not by_pid[p.ppid].session_id
                             or p.session_id == by_pid[p.ppid].session_id)}
            if children.issubset(protected):
                break
            protected.update(children)
    for pid in tuple(protected):
        visited = set()
        while pid in by_pid and pid not in visited:
            visited.add(pid)
            pid = by_pid[pid].ppid
            protected.add(pid)
    return frozenset(protected)


def protection_reason(process: ProcessMetrics, *, owner_uid: int,
                      protected_pids: set[int] | frozenset[int] = frozenset(),
                      self_pid: int | None = None) -> str | None:
    """Return a rejection reason, or None. Unknown identity is never writable."""
    if process.pid <= 2 or process.kernel_thread or process.ppid == 2:
        return "Kernel or init process"
    if not process.accessible or process.uid < 0:
        return "Process metadata is inaccessible"
    if owner_uid <= 0 or process.uid == 0 or process.uid != owner_uid:
        return "Root, system, or another user's process"
    if (not math.isfinite(process.create_time) or process.create_time <= 0
            or not process.exe.startswith("/") or process.exe.endswith(" (deleted)")):
        return "Process identity or executable is uncertain"
    if process.status in {"unknown", "zombie", "dead", "tracing-stop"}:
        return "Process status is unsafe or unknown"
    if process.pid == (self_pid if self_pid is not None else os.getpid()):
        return "AI_OS itself"
    if process.name in CRITICAL_NAMES or os.path.basename(process.exe) in CRITICAL_NAMES:
        return "Critical system/session/desktop service"
    if process.name.startswith(("kworker/", "ksoftirqd/", "rcu_", "migration/", "watchdog/", "jbd2/")):
        return "Kernel service"
    if "/system.slice/" in process.cgroup or process.cgroup in {"/system.slice", "/init.scope"}:
        return "System service cgroup"
    if process.foreground or process.pid in protected_pids:
        return "Foreground/workload/AI_OS dependency"
    return None
