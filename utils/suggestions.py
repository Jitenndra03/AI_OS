"""
utils/suggestions.py
=====================
System stats dekho aur smart suggestions do — koi AI API nahi, pure logic!
"""

from system_monitor.cpu    import get_cpu_stats,    get_top_processes
from system_monitor.memory import get_memory_stats
from system_monitor.disk   import get_all_disks


def get_suggestions() -> list[str]:
    """
    System ki current state dekho aur suggestions return karo.

    Returns:
        List of suggestion strings (Hindi mein)
    """
    suggestions = []

    # ── CPU check ─────────────────────────────────────────────
    try:
        cpu = get_cpu_stats()
        if cpu["is_high"]:
            top = get_top_processes(3)
            names = ", ".join(p["name"] for p in top if p.get("name"))
            suggestions.append(
                f"🔴 CPU {cpu['usage_percent']:.0f}% pe hai! "
                f"Heavy processes: {names}. 'processes dikhao' likho."
            )
    except:
        pass

    # ── RAM check ─────────────────────────────────────────────
    try:
        mem = get_memory_stats()
        if mem["is_high"]:
            suggestions.append(
                f"🔴 RAM {mem['percent']:.0f}% bhar gayi! "
                f"Sirf {mem['free_gb']:.1f} GB free hai. Kuch apps band karo."
            )
    except:
        pass

    # ── Disk check ────────────────────────────────────────────
    try:
        disks = get_all_disks()
        for d in disks:
            if d.get("is_high"):
                suggestions.append(
                    f"🔴 {d.get('device', 'Disk')} almost full "
                    f"({d['percent']:.0f}%)! "
                    f"Sirf {d['free_gb']:.1f} GB free hai."
                )
    except:
        pass

    return suggestions
