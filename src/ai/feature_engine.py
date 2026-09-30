"""Feature engineering for ML workload classification.

Extracts numerical feature vectors from SystemSnapshot and per-process
metrics.  Features are designed to capture workload characteristics:
process-tree shape, resource distribution, temporal patterns, and
executable-name hints.

All features are deterministic given the same snapshot — no random state
or external lookups.  Missing or inaccessible fields produce zero values.
"""

from __future__ import annotations

import math
import os
import re
from collections import deque
from dataclasses import dataclass

import numpy as np

from src.optimization.models import ProcessMetrics, SystemSnapshot, WorkloadState


# ── Executable hint patterns (same vocabulary as the deterministic detector) ──

_HINT_PATTERNS: list[tuple[WorkloadState, re.Pattern]] = [
    (WorkloadState.AI_ML,
     re.compile(r"\b(torchrun|tensorflow|pytorch|jupyter|ollama|llama|training|train\.py|inference)\b", re.I)),
    (WorkloadState.COMPILATION,
     re.compile(r"\b(gcc|g\+\+|cc1plus|cc1|clang\+\+|clang|rustc|cargo|make|ninja|cmake|javac|gradle)\b", re.I)),
    (WorkloadState.GAMING,
     re.compile(r"\b(steam|steamwebhelper|wine|wine64|proton|gamescope|dota2|cs2|minecraft)\b", re.I)),
    (WorkloadState.EDITING,
     re.compile(r"\b(blender|gimp|krita|inkscape|kdenlive|shotcut|davinci|resolve|ffmpeg|audacity)\b", re.I)),
    (WorkloadState.DEVELOPMENT,
     re.compile(r"\b(code|codium|pycharm|idea|intellij|eclipse|neovim|nvim|vim|node|npm|webpack|vite|pytest)\b", re.I)),
]

# One-hot indices for hint categories
_HINT_INDEX = {
    WorkloadState.AI_ML: 0,
    WorkloadState.COMPILATION: 1,
    WorkloadState.GAMING: 2,
    WorkloadState.EDITING: 3,
    WorkloadState.DEVELOPMENT: 4,
}
_NUM_HINT_CATEGORIES = len(_HINT_INDEX)

# ── Feature names (stable ordering, used for model training and inference) ────

SYSTEM_FEATURES = [
    "sys_cpu_percent",
    "sys_memory_percent",
    "sys_load_avg_1m",
    "sys_load_avg_ratio",       # load_avg_1m / cpu_count
    "sys_disk_read_bps",
    "sys_disk_write_bps",
    "sys_cpu_pressure",
    "sys_memory_pressure",
    "sys_io_pressure",
]

AGGREGATE_FEATURES = [
    "n_user_processes",
    "n_busy_processes",         # cpu > 10%
    "total_user_cpu",
    "total_user_memory",
    "total_user_io_bps",
    "max_process_cpu",
    "max_process_memory",
    "max_process_io_bps",
    "std_process_cpu",
    "busy_process_ratio",       # n_busy / n_user
    "top1_cpu_share",           # max_cpu / total_cpu
    "top3_cpu_share",
    "n_threads_total",
    "mean_threads_per_process",
]

HINT_FEATURES = [f"hint_{state.value.lower()}" for state in _HINT_INDEX]

TREE_FEATURES = [
    "max_tree_depth",
    "max_tree_width",           # max children of any single process
    "n_process_trees",          # distinct root groups
]

FEATURE_NAMES = SYSTEM_FEATURES + AGGREGATE_FEATURES + HINT_FEATURES + TREE_FEATURES
NUM_FEATURES = len(FEATURE_NAMES)


def _safe(value: float) -> float:
    """Clamp to finite; NaN/Inf → 0."""
    return value if isinstance(value, (int, float)) and math.isfinite(value) else 0.0


def _hint_vector(processes: list[ProcessMetrics], owner_uid: int) -> np.ndarray:
    """One-hot hint presence for user-owned processes."""
    vec = np.zeros(_NUM_HINT_CATEGORIES, dtype=np.float64)
    for proc in processes:
        if proc.uid != owner_uid or proc.kernel_thread or not proc.accessible:
            continue
        text = " ".join((os.path.basename(proc.exe), proc.name, *proc.cmdline))
        for state, pattern in _HINT_PATTERNS:
            if pattern.search(text):
                vec[_HINT_INDEX[state]] = 1.0
                break  # First match wins per process
    return vec


def _tree_features(processes: list[ProcessMetrics], owner_uid: int) -> np.ndarray:
    """Process-tree shape descriptors for user-owned processes."""
    user_procs = {p.pid: p for p in processes
                  if p.uid == owner_uid and not p.kernel_thread and p.accessible}
    children: dict[int, list[int]] = {}
    for pid, proc in user_procs.items():
        if proc.ppid in user_procs:
            children.setdefault(proc.ppid, []).append(pid)

    # Find roots (processes whose parent is not in user_procs)
    roots = [pid for pid, proc in user_procs.items() if proc.ppid not in user_procs]

    max_depth = 0
    max_width = 0
    for root in roots:
        # BFS to find depth and width
        queue = deque([(root, 0)])
        visited = set()
        while queue:
            node, depth = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            max_depth = max(max_depth, depth)
            kids = children.get(node, [])
            max_width = max(max_width, len(kids))
            for kid in kids:
                queue.append((kid, depth + 1))

    return np.array([float(max_depth), float(max_width), float(len(roots))],
                    dtype=np.float64)


@dataclass(frozen=True)
class FeatureVector:
    """Extracted features from a single SystemSnapshot."""
    values: np.ndarray          # shape (NUM_FEATURES,)
    feature_names: tuple[str, ...] = tuple(FEATURE_NAMES)
    owner_uid: int = -1
    timestamp: float = 0.0


def extract_features(snapshot: SystemSnapshot, owner_uid: int) -> FeatureVector:
    """Extract a feature vector from a SystemSnapshot.

    Returns a FeatureVector with deterministic, finite values.
    """
    user_processes = [p for p in snapshot.processes
                      if p.uid == owner_uid and not p.kernel_thread
                      and p.accessible and p.pid > 1]

    # ── System-level features ────────────────────────────────────────────
    sys_feats = np.array([
        _safe(snapshot.cpu_percent),
        _safe(snapshot.memory_percent),
        _safe(snapshot.load_average[0]) if snapshot.load_average else 0.0,
        _safe(snapshot.load_average[0] / max(1, snapshot.cpu_count))
            if snapshot.load_average else 0.0,
        _safe(snapshot.disk_read_bytes_per_sec),
        _safe(snapshot.disk_write_bytes_per_sec),
        _safe(snapshot.cpu_pressure),
        _safe(snapshot.memory_pressure),
        _safe(snapshot.io_pressure),
    ], dtype=np.float64)

    # ── Aggregate process features ───────────────────────────────────────
    n_user = len(user_processes)
    if n_user == 0:
        agg_feats = np.zeros(len(AGGREGATE_FEATURES), dtype=np.float64)
    else:
        cpus = np.array([_safe(p.cpu_percent) for p in user_processes])
        mems = np.array([_safe(p.memory_percent) for p in user_processes])
        ios = np.array([_safe(p.io_bytes_per_sec) for p in user_processes])
        threads = np.array([max(0, p.num_threads) for p in user_processes], dtype=np.float64)

        n_busy = int(np.sum(cpus > 10.0))
        total_cpu = float(np.sum(cpus))
        total_mem = float(np.sum(mems))
        total_io = float(np.sum(ios))
        max_cpu = float(np.max(cpus))
        max_mem = float(np.max(mems))
        max_io = float(np.max(ios))
        std_cpu = float(np.std(cpus))
        busy_ratio = n_busy / n_user if n_user > 0 else 0.0
        top1_share = max_cpu / total_cpu if total_cpu > 0 else 0.0

        sorted_cpus = np.sort(cpus)[::-1]
        top3_cpu = float(np.sum(sorted_cpus[:3]))
        top3_share = top3_cpu / total_cpu if total_cpu > 0 else 0.0

        total_threads = float(np.sum(threads))
        mean_threads = total_threads / n_user if n_user > 0 else 0.0

        agg_feats = np.array([
            float(n_user), float(n_busy), total_cpu, total_mem, total_io,
            max_cpu, max_mem, max_io, std_cpu, busy_ratio,
            top1_share, top3_share, total_threads, mean_threads,
        ], dtype=np.float64)

    # ── Hint features (one-hot presence of known application categories) ─
    hint_feats = _hint_vector(user_processes, owner_uid)

    # ── Tree shape features ──────────────────────────────────────────────
    tree_feats = _tree_features(user_processes, owner_uid)

    # ── Assemble ─────────────────────────────────────────────────────────
    values = np.concatenate([sys_feats, agg_feats, hint_feats, tree_feats])
    assert values.shape == (NUM_FEATURES,), f"Expected {NUM_FEATURES} features, got {values.shape[0]}"

    # Final safety: replace any non-finite values
    values = np.nan_to_num(values, nan=0.0, posinf=0.0, neginf=0.0)

    return FeatureVector(values=values, owner_uid=owner_uid,
                         timestamp=snapshot.timestamp)


def extract_features_batch(snapshots: list[SystemSnapshot],
                           owner_uid: int) -> np.ndarray:
    """Extract feature matrix from multiple snapshots.

    Returns shape (n_snapshots, NUM_FEATURES).
    """
    if not snapshots:
        return np.empty((0, NUM_FEATURES), dtype=np.float64)
    return np.vstack([extract_features(s, owner_uid).values for s in snapshots])
