"""
╔══════════════════════════════════════════════════════════╗
║           🤖 AI OS Assistant — VS Code Version           ║
║     Hindi | English | Hinglish — sab samajhta hoon       ║
║         Seedha tumhare PC pe files/folders banega!       ║
╚══════════════════════════════════════════════════════════╝

SETUP:
  1. pip install groq
  2. GROQ_API_KEY neeche daalo
  3. python ai_os.py

Get FREE Groq API key: https://console.groq.com/keys
"""


import os
import subprocess
import json
import re
from datetime import datetime
from groq import Groq
from dotenv import load_dotenv
load_dotenv()

# ============================================================
# CONFIG — Yahan apni Groq API key daalo
# ============================================================
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")   # <-- Sirf ye badlo!




#=============================================================
# WORKSPACE — Yahan apni PC pe seedha folder daalo
# Ye folder tumhare PC pe banega — change kar sakte ho


# WORK_DIR = os.path.join(os.path.expanduser("~"), "AI_OS_Workspace")
WORK_DIR = os.path.join(os.path.expanduser("~"), "Desktop", "AI_OS_Workspace")

#=============================================================




# ============================================================
# COLORS for terminal (Windows + Linux dono pe kaam karta hai)
# ============================================================
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    RED    = "\033[91m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    BLUE   = "\033[94m"
    CYAN   = "\033[96m"
    WHITE  = "\033[97m"
    DIM    = "\033[2m"

# ============================================================
# GROQ CLIENT SETUP
# ============================================================
client = Groq(api_key=GROQ_API_KEY)
MODEL_NAME = "llama-3.3-70b-versatile"

# ============================================================
# AI BRAIN — System Prompt
# ============================================================
SYSTEM_PROMPT = """
You are an AI OS assistant. User will give instructions in Hindi, English, or Hinglish.
Understand what they want and return ONLY a valid JSON response — no extra text.

SUPPORTED COMMANDS:
1.  ls / dir     - list files/folders
2.  ls -la       - list with details
3.  pwd          - current directory
4.  mkdir        - create folder
5.  touch        - create empty file
6.  rm           - remove file
7.  rm -rf       - remove folder forcefully (DANGEROUS)
8.  mv           - move or rename file/folder
9.  cp           - copy file/folder
10. cat          - show file content
11. echo         - write text to file
12. find         - search file by name
13. grep         - search text inside file
14. chmod        - change permissions
15. head         - show first N lines of file
16. tail         - show last N lines of file
17. wc           - count lines/words in file
18. du -sh       - folder size
19. df -h        - disk space info
20. ps           - running processes
21. clear        - clear terminal
22. zip          - compress files

WINDOWS NOTE: Use Windows-compatible commands when needed.
For listing: use 'dir' style but translate to ls -la equivalent.

RESPONSE FORMAT (JSON only, nothing else):
{
  "understood": "What user wants in simple English",
  "command": "exact command to run",
  "explanation": "Kya karega ye command — Hindi/Hinglish mein batao",
  "is_dangerous": false
}

If dangerous (rm -rf, delete all, format etc):
{
  "understood": "...",
  "command": "...",
  "explanation": "...",
  "is_dangerous": true,
  "danger_reason": "Ye command permanently data delete kar dega!"
}

If unclear:
{
  "understood": "unclear",
  "command": null,
  "explanation": "Samajh nahi aaya, thoda aur batao!",
  "is_dangerous": false
}
"""

def understand_command(user_input):
    """AI se samjho user kya chahta hai"""
    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_input}
            ],
            temperature=0.1,
            max_tokens=400
        )
        text = response.choices[0].message.content.strip()
        text = re.sub(r'```json|```', '', text).strip()
        return json.loads(text)
    except json.JSONDecodeError:
        return {
            "understood": "error",
            "command": None,
            "explanation": "AI ka response parse nahi hua, dobara try karo!",
            "is_dangerous": False
        }
    except Exception as e:
        return {
            "understood": "error",
            "command": None,
            "explanation": f"AI Error: {str(e)}",
            "is_dangerous": False
        }

def execute_command(command):
    """Command ko PC pe actually chalao"""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=WORK_DIR,
            capture_output=True,
            text=True,
            timeout=15,
            encoding='utf-8',
            errors='replace'
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0:
            return True, stdout if stdout else "✅ Command successful!"
        else:
            return False, stderr if stderr else "Unknown error"
    except subprocess.TimeoutExpired:
        return False, "⏱️ Command timeout!"
    except Exception as e:
        return False, str(e)

def print_header():
    """Sundar header print karo"""
    os.system('cls' if os.name == 'nt' else 'clear')
    print(f"""
{C.CYAN}{C.BOLD}╔══════════════════════════════════════════════════════╗
║           🤖  AI OS Assistant  v1.0                  ║
║     Hindi | English | Hinglish — sab samajhta hoon   ║
╚══════════════════════════════════════════════════════╝{C.RESET}
{C.DIM}Workspace: {C.YELLOW}{WORK_DIR}{C.RESET}
{C.DIM}Model    : {C.GREEN}{MODEL_NAME}{C.RESET}
{C.DIM}Commands : type {C.WHITE}'help'{C.DIM} to see examples | {C.WHITE}'exit'{C.DIM} to quit{C.RESET}
{C.CYAN}{'─' * 54}{C.RESET}""")

def print_help():
    """Examples dikhao"""
    examples = [
        ("📁 Folder banana",    "projects naam ka folder bna do"),
        ("📄 File banana",      "index.html file bna do"),
        ("📋 Files dekhna",     "sari files dikhao"),
        ("✍️  File mein likhna", "hello.txt mein 'namaste duniya' likho"),
        ("📖 File padhna",      "index.html ka content dikhao"),
        ("📦 Copy karna",       "index.html ko backup.html mein copy karo"),
        ("✏️  Rename karna",     "old.txt ko new.txt mein rename karo"),
        ("🗑️  Delete karna",     "temp.txt file delete karo"),
        ("📏 Size dekhna",      "projects folder ka size batao"),
        ("🔍 Search karna",     "is folder mein .txt files dhundo"),
    ]
    print(f"\n{C.YELLOW}{C.BOLD}💡 Examples — Kuch bhi aise likh sakte ho:{C.RESET}")
    for icon_name, example in examples:
        print(f"  {C.DIM}{icon_name:<22}{C.RESET} {C.WHITE}\"{example}\"{C.RESET}")
    print()

def confirm_dangerous(cmd, reason):
    """Dangerous command ke liye confirm maango"""
    print(f"\n{C.RED}{C.BOLD}⚠️  DANGEROUS COMMAND!{C.RESET}")
    print(f"{C.YELLOW}Command : {C.WHITE}{cmd}{C.RESET}")
    print(f"{C.RED}Warning : {reason}{C.RESET}")
    ans = input(f"{C.YELLOW}Kya sach mein chalana hai? (haan/nahi): {C.RESET}").strip().lower()
    return ans in ['haan', 'han', 'yes', 'y', 'ha', 'haa']

# History
history = []

def run_ai_os():
    """Main loop"""
    # Workspace create karo agar nahi hai
    os.makedirs(WORK_DIR, exist_ok=True)

    print_header()
    print(f"{C.GREEN}✅ Workspace ready! Ab kuch bhi likho...{C.RESET}\n")

    while True:
        try:
            # Input lo
            user_input = input(f"{C.CYAN}🤖 Tum:{C.RESET} ").strip()

            if not user_input:
                continue

            # Special commands
            if user_input.lower() in ['exit', 'quit', 'band karo', 'bye']:
                print(f"\n{C.YELLOW}👋 AI OS band ho raha hai! Phir milenge!{C.RESET}\n")
                break

            if user_input.lower() in ['help', 'madad', 'examples']:
                print_help()
                continue

            if user_input.lower() in ['history', 'purane commands']:
                if not history:
                    print(f"{C.DIM}  Abhi tak koi command nahi chali.{C.RESET}\n")
                else:
                    print(f"\n{C.YELLOW}📜 Command History:{C.RESET}")
                    for i, h in enumerate(history[-10:], 1):
                        status = f"{C.GREEN}✅{C.RESET}" if h['success'] else f"{C.RED}❌{C.RESET}"
                        print(f"  {C.DIM}{i:2}. {h['time']}{C.RESET} {status} {C.CYAN}$ {h['cmd']}{C.RESET}")
                    print()
                continue

            if user_input.lower() in ['clear', 'saaf karo']:
                print_header()
                continue

            # AI se samjho
            print(f"{C.DIM}   ⌛ Samajh raha hoon...{C.RESET}", end='\r')
            result = understand_command(user_input)

            cmd         = result.get('command')
            explanation = result.get('explanation', '')
            is_dangerous = result.get('is_dangerous', False)
            danger_reason = result.get('danger_reason', '')

            # Agar samajh nahi aaya
            if not cmd:
                print(f"   {C.YELLOW}🤷 {explanation}{C.RESET}\n")
                continue

            # Command dikhao
            print(f"   {C.DIM}💬 {explanation}{C.RESET}")
            print(f"   {C.CYAN}$ {cmd}{C.RESET}")

            # Dangerous check
            if is_dangerous:
                if not confirm_dangerous(cmd, danger_reason):
                    print(f"   {C.DIM}❌ Command cancel kar diya.{C.RESET}\n")
                    continue

            # Execute karo
            success, output = execute_command(cmd)
            timestamp = datetime.now().strftime('%H:%M:%S')

            if success:
                print(f"   {C.GREEN}✅ Success! [{timestamp}]{C.RESET}")
                if output and output != "✅ Command successful!":
                    print(f"{C.DIM}{'─'*50}{C.RESET}")
                    print(f"{C.WHITE}{output}{C.RESET}")
                    print(f"{C.DIM}{'─'*50}{C.RESET}")
            else:
                print(f"   {C.RED}❌ Error: {output}{C.RESET}")

            print()

            # History save karo
            history.append({
                'input': user_input,
                'cmd': cmd,
                'success': success,
                'time': timestamp
            })

        except KeyboardInterrupt:
            print(f"\n\n{C.YELLOW}👋 Ctrl+C dabaya! AI OS band ho raha hai.{C.RESET}\n")
            break

# ============================================================
# START
# ============================================================
if __name__ == "__main__":
    run_ai_os()