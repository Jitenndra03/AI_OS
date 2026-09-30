"""Shared contracts. Process identity is PID + start time, never PID alone."""

from dataclasses import dataclass, field
from enum import Enum


class WorkloadState(str, Enum):
    NORMAL = "NORMAL"
    DEVELOPMENT = "DEVELOPMENT"
    COMPILATION = "COMPILATION"
    GAMING = "GAMING"
    EDITING = "EDITING"
    AI_ML = "AI_ML"
    HEAVY_UNKNOWN = "HEAVY_UNKNOWN"


class Category(str, Enum):
    CRITICAL_SYSTEM = "CRITICAL_SYSTEM"
    USER_FOREGROUND = "USER_FOREGROUND"
    USER_IMPORTANT = "USER_IMPORTANT"
    BACKGROUND_SAFE = "BACKGROUND_SAFE"
    BACKGROUND_OPTIONAL = "BACKGROUND_OPTIONAL"
    UNKNOWN = "UNKNOWN"
    SUSPICIOUS = "SUSPICIOUS"


class Action(str, Enum):
    NOOP = "NOOP"
    RENICE = "RENICE"
    CGROUP = "CGROUP"
    CPU_LIMIT = "CPU_LIMIT"
    SUSPEND = "SUSPEND"
    RESTORE = "RESTORE"


@dataclass(frozen=True)
class ProcessMetrics:
    pid: int
    create_time: float
    ppid: int = 0
    uid: int = -1
    name: str = ""
    exe: str = ""
    cmdline: tuple[str, ...] = ()
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    rss: int = 0
    num_threads: int = 0
    status: str = "unknown"
    nice: int = 0
    read_bytes: int = 0
    write_bytes: int = 0
    io_bytes_per_sec: float = 0.0
    cgroup: str = ""
    foreground: bool = False
    kernel_thread: bool = False
    accessible: bool = True
    session_id: int = 0

    @property
    def identity(self) -> str:
        return f"{self.pid}:{self.create_time:.6f}"


@dataclass(frozen=True)
class SystemSnapshot:
    timestamp: float
    monotonic: float
    processes: tuple[ProcessMetrics, ...] = ()
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    memory_available: int = 0
    load_average: tuple[float, float, float] = (0.0, 0.0, 0.0)
    disk_read_bytes_per_sec: float = 0.0
    disk_write_bytes_per_sec: float = 0.0
    cpu_pressure: float = 0.0
    memory_pressure: float = 0.0
    io_pressure: float = 0.0
    cpu_count: int = 1
    complete: bool = True


@dataclass(frozen=True)
class Workload:
    state: WorkloadState = WorkloadState.NORMAL
    protected_pids: frozenset[int] = frozenset()
    reason: str = "No sustained heavy workload"
    confidence: float = 0.0
    since: float = 0.0

    @property
    def active(self) -> bool:
        return self.state != WorkloadState.NORMAL


@dataclass(frozen=True)
class BackgroundRule:
    """Explicit opt-in by exact absolute executable; never by name alone."""

    executable: str
    category: Category = Category.BACKGROUND_SAFE
    strategy: Action = Action.RENICE
    nice: int = 10
    cpu_quota_percent: float = 25.0
    memory_high_bytes: int | None = None
    allow_suspend: bool = False


@dataclass(frozen=True)
class Classification:
    process: ProcessMetrics
    category: Category
    reason: str
    confidence: float = 1.0
    rule: BackgroundRule | None = None


@dataclass(frozen=True)
class Decision:
    process: ProcessMetrics
    classification: Category
    action: Action
    reason: str
    workload: WorkloadState = WorkloadState.NORMAL
    new_nice: int | None = None
    cpu_quota_percent: float | None = None
    memory_high_bytes: int | None = None
    allow_suspend: bool = False


@dataclass
class ActionRecord:
    identity: str
    pid: int
    create_time: float
    executable: str
    uid: int
    original_nice: int
    original_cgroup: str
    original_status: str
    action: str
    timestamp: float
    workload: str
    boot_id: str = ""
    phase: str = "pending"
    applied_nice: int | None = None
    managed_cgroup: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class ControlResult:
    success: bool
    reason: str
    identity: str = ""
    changed: bool = False
