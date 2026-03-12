"""
╔══════════════════════════════════════════════════════════╗
║         🤖 AI OS — v3.0 (Proper Architecture)           ║
║                                                          ║
║  Layer 1: STT      — Whisper (Voice → Text)             ║
║  Layer 2: NLP      — Preprocessor + Semantic (NLTK)     ║
║  Layer 3: Engine   — Validator + Executor               ║
║  Layer 4: Monitor  — CPU / RAM / Disk (psutil)          ║
║  Utils:   History + Logger + Suggestions + Colors       ║
╚══════════════════════════════════════════════════════════╝

Run:
    python main.py          # Text mode
    python main.py --voice  # Voice mode
"""

import os
import sys
import threading
import time
import argparse

from dotenv import load_dotenv
load_dotenv()

# ── Internal modules ──────────────────────────────────────────
from nlp.semantic          import analyze
from command_engine.executor import execute
from system_monitor.cpu    import get_cpu_stats
from system_monitor.memory import get_memory_stats
from system_monitor.disk   import get_disk_stats
from utils.colors          import C, make_bar
from utils.history         import load as load_history, add_entry
from utils.logger          import log_command, log_error, log_alert, log_info
from utils.suggestions     import get_suggestions

# ── STT import (optional — sirf voice mode mein) ──────────────
STT_AVAILABLE = False
try:
    from stt.whisper_stt import listen
    STT_AVAILABLE = True
except ImportError:
    pass

# ── Config ────────────────────────────────────────────────────
WORK_DIR = r"D:\AI OS"
os.makedirs(WORK_DIR, exist_ok=True)

# ══════════════════════════════════════════════════════════════
# UI HELPERS
# ══════════════════════════════════════════════════════════════

def print_header():
    os.system("cls" if os.name == "nt" else "clear")
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════╗
║         🤖  AI OS  v3.0                              ║
║   Voice | Hindi | English | Hinglish                 ║
║   AI Interface Layer — Indian Users ke liye 🇮🇳       ║
╚══════════════════════════════════════════════════════╝{C.RESET}
{C.DIM}Workspace : {C.YELLOW}{WORK_DIR}{C.RESET}
{C.DIM}Commands  : {C.WHITE}help{C.DIM} | {C.WHITE}status{C.DIM} | {C.WHITE}history{C.DIM} | {C.WHITE}voice{C.DIM} | {C.WHITE}exit{C.RESET}
{C.CYAN}{'─'*54}{C.RESET}""")


def print_stats():
    """CPU / RAM / Disk bars print karo."""
    try:
        cpu  = get_cpu_stats()
        mem  = get_memory_stats()
        disk = get_disk_stats("C:\\")

        cc = C.RED   if cpu["is_high"]  else C.GREEN
        mc = C.RED   if mem["is_high"]  else C.GREEN
        dc = C.RED   if disk.get("is_high") else C.YELLOW if disk.get("percent",0)>75 else C.GREEN

        print(f"\n{C.BOLD}{C.CYAN}  📊 System Status{C.RESET}")
        print(f"  {C.DIM}CPU  {C.RESET}{cc}{make_bar(cpu['usage_percent'])}{C.RESET} {cc}{cpu['usage_percent']:5.1f}%{C.RESET}")
        print(f"  {C.DIM}RAM  {C.RESET}{mc}{make_bar(mem['percent'])}{C.RESET} {mc}{mem['percent']:5.1f}%{C.RESET}  {C.DIM}({mem['used_gb']:.1f}/{mem['total_gb']:.1f} GB){C.RESET}")
        print(f"  {C.DIM}DISK {C.RESET}{dc}{make_bar(disk.get('percent',0))}{C.RESET} {dc}{disk.get('percent',0):5.1f}%{C.RESET}  {C.DIM}({disk.get('used_gb',0):.0f}/{disk.get('total_gb',0):.0f} GB){C.RESET}")
        print()

        # Suggestions
        for s in get_suggestions():
            print(f"  {C.YELLOW}{s}{C.RESET}")
            log_alert(s)

    except Exception as e:
        print(f"  {C.DIM}(System stats unavailable: {e}){C.RESET}")
    print()


def print_help():
    examples = [
        ("📁 Folder banana",    '"projects naam ka folder bna do"'),
        ("📄 File banana",      '"index.html file bna do"'),
        ("📋 List karna",       '"sari files dikhao"'),
        ("✍️  File mein likhna", '"hello.txt mein Namaste likho"'),
        ("📖 File padhna",      '"index.html ka content dikhao"'),
        ("📦 Copy",             '"index.html ko backup.html mein copy karo"'),
        ("✏️  Rename",           '"old.txt ko new.txt rename karo"'),
        ("🗑️  Delete",           '"temp.txt delete karo"'),
        ("🌳 Tree",              '"folder ka structure dikhao"'),
        ("💻 System",           '"system ki info batao"'),
        ("🌐 Network",          '"mera ip address kya hai"'),
        ("⚙️  Processes",        '"kaun se processes chal rahe hain"'),
    ]
    print(f"\n{C.YELLOW}{C.BOLD}💡 Examples:{C.RESET}")
    for name, ex in examples:
        print(f"  {C.DIM}{name:<24}{C.RESET} {C.WHITE}{ex}{C.RESET}")
    print(f"\n{C.PURPLE}  Special: help | status | history | voice | clear | exit{C.RESET}\n")


def print_history(history: list):
    if not history:
        print(f"  {C.DIM}Abhi tak koi command nahi chali.{C.RESET}\n")
        return
    print(f"\n{C.YELLOW}{C.BOLD}📜 History (last 10):{C.RESET}")
    print(f"  {C.DIM}{'#':<4}{'Time':<10}{'Mode':<8}{'OK':<5}{'Input':<30}Command{C.RESET}")
    print(f"  {C.DIM}{'─'*72}{C.RESET}")
    for i, h in enumerate(history[-10:], 1):
        st   = f"{C.GREEN}✅{C.RESET}" if h["success"] else f"{C.RED}❌{C.RESET}"
        mode = f"{C.PURPLE}🎤{C.RESET}" if h.get("mode") == "voice" else f"{C.BLUE}⌨️ {C.RESET}"
        inp  = (h["input"][:28]+"..") if len(h["input"])>28 else h["input"]
        print(f"  {C.DIM}{i:<4}{h['time']:<10}{C.RESET}{mode}     {st}  {C.WHITE}{inp:<30}{C.RESET}{C.CYAN}{h.get('command','')}{C.RESET}")
    print()


def confirm_dangerous(command: str, reason: str) -> bool:
    print(f"\n  {C.RED}{C.BOLD}⚠️  DANGEROUS COMMAND!{C.RESET}")
    print(f"  {C.YELLOW}Command : {C.WHITE}{command}{C.RESET}")
    if reason:
        print(f"  {C.RED}Reason  : {reason}{C.RESET}")
    ans = input(f"  {C.YELLOW}Sach mein chalana hai? (haan/nahi): {C.RESET}").strip().lower()
    return ans in ["haan", "han", "yes", "y", "ha", "haa"]


# ══════════════════════════════════════════════════════════════
# BACKGROUND MONITOR
# ══════════════════════════════════════════════════════════════

def background_monitor():
    while True:
        time.sleep(30)
        try:
            suggestions = get_suggestions()
            for s in suggestions:
                print(f"\n  {C.RED}{C.BOLD}{s}{C.RESET}")
                log_alert(s)
                print(f"{C.CYAN}🤖 Tum:{C.RESET} ", end="", flush=True)
        except:
            pass


# ══════════════════════════════════════════════════════════════
# PROCESS USER INPUT — shared by text + voice
# ══════════════════════════════════════════════════════════════

def process_input(user_input: str, history: list, mode: str = "text") -> list:
    """
    User input lo → NLP → Execute → Show result → History save.
    """
    # ── NLP ───────────────────────────────────────────────────
    print(f"{C.DIM}   ⌛ Samajh raha hoon...{C.RESET}", end="\r")
    intent_result = analyze(user_input)

    intent   = intent_result["intent"]
    argument = intent_result.get("argument", "")
    conf     = intent_result.get("confidence", 0)

    print(f"   {C.DIM}🧠 Intent: {C.PURPLE}{intent}{C.RESET}{C.DIM} | arg: {argument} | conf: {conf:.0%}{C.RESET}")

    # ── Execute ───────────────────────────────────────────────
    result = execute(intent_result)

    explanation = result.get("explanation", "")
    command     = result.get("command", "")

    if explanation:
        print(f"   {C.DIM}💬 {explanation}{C.RESET}")
    if command:
        print(f"   {C.CYAN}$ {command}{C.RESET}")

    # ── Dangerous — confirm maango ────────────────────────────
    if result.get("blocked"):
        if confirm_dangerous(command, result.get("danger_reason", "")):
            from command_engine.executor import run_command
            result = run_command(command)
            result["explanation"] = explanation
        else:
            print(f"   {C.DIM}❌ Cancel kar diya.{C.RESET}\n")
            log_command(user_input, intent, command or "", False, mode)
            return history

    # ── Result dikhao ─────────────────────────────────────────
    if result["success"]:
        print(f"   {C.GREEN}✅ Success!{C.RESET}")
        output = result.get("output", "")
        if output and output != "✅ Command successful!":
            print(f"  {C.DIM}{'─'*50}{C.RESET}")
            print(f"  {C.WHITE}{output}{C.RESET}")
            print(f"  {C.DIM}{'─'*50}{C.RESET}")
    else:
        print(f"   {C.RED}❌ {result.get('output', 'Failed')}{C.RESET}")

    print()

    # ── Log + History ─────────────────────────────────────────
    log_command(user_input, intent, command or "", result["success"], mode)
    history = add_entry(history, user_input, command or "", result["success"], mode)
    return history


# ══════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════

def main(voice_mode: bool = False):
    history = load_history()

    print_header()
    print_stats()

    # Background monitor thread
    threading.Thread(target=background_monitor, daemon=True).start()

    log_info(f"AI OS started | voice_mode={voice_mode}")
    print(f"{C.GREEN}✅ AI OS ready! {'🎤 Voice mode ON' if voice_mode else '⌨️  Text mode'}  Kuch bhi likho...{C.RESET}\n")

    while True:
        try:
            # ── Input lo ──────────────────────────────────────
            if voice_mode and STT_AVAILABLE:
                print(f"{C.CYAN}🎤 Bol:{C.RESET} ", end="", flush=True)
                user_input = listen()
                if not user_input:
                    continue
            else:
                user_input = input(f"{C.CYAN}🤖 Tum:{C.RESET} ").strip()

            if not user_input:
                continue

            # ── Special commands ──────────────────────────────
            low = user_input.lower()

            if low in ["exit", "quit", "band karo", "bye"]:
                log_info("AI OS stopped by user.")
                print(f"\n{C.YELLOW}👋 Phir milenge!{C.RESET}\n")
                break

            if low in ["help", "madad", "?"]:
                print_help()
                continue

            if low in ["status", "system", "cpu ram"]:
                print_stats()
                continue

            if low in ["history", "purane commands"]:
                print_history(history)
                continue

            if low in ["clear", "cls", "saaf karo"]:
                print_header()
                print_stats()
                continue

            if low == "voice":
                if STT_AVAILABLE:
                    voice_mode = not voice_mode
                    print(f"  {C.GREEN}🎤 Voice mode {'ON' if voice_mode else 'OFF'}{C.RESET}\n")
                else:
                    print(f"  {C.RED}❌ Whisper install nahi hai! 'pip install openai-whisper pyaudio'{C.RESET}\n")
                continue

            # ── Process ───────────────────────────────────────
            history = process_input(user_input, history,
                                    mode="voice" if voice_mode else "text")

        except KeyboardInterrupt:
            log_info("AI OS stopped by Ctrl+C.")
            print(f"\n\n{C.YELLOW}👋 Phir milenge!{C.RESET}\n")
            break


# ══════════════════════════════════════════════════════════════
# ENTRY POINT
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI OS Assistant")
    parser.add_argument("--voice", action="store_true", help="Voice input mode start karo")
    args = parser.parse_args()
    main(voice_mode=args.voice)
