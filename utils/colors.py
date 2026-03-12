"""utils/colors.py — Terminal color codes."""

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    PURPLE = "\033[95m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"


def make_bar(percent: float, width: int = 20) -> str:
    """CPU/RAM progress bar: [████████░░░░]"""
    filled = int(width * percent / 100)
    return f"[{'█' * filled}{'░' * (width - filled)}]"
