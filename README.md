# 🤖 AI OS — AI Interface Layer over OS

> Hindi | English | Hinglish — sab samajhta hoon 🇮🇳

## Architecture

```
User (Voice / Text)
       ↓
STT Layer         — Whisper (Voice → Text)
       ↓
NLP Layer         — Preprocessor + Semantic Analysis (NLTK)
       ↓
Command Engine    — Validator + Executor (pure Python)
       ↓
System Monitor    — CPU / RAM / Disk (psutil)
       ↓
Utils             — History + Logger + Suggestions + Colors
```

## Setup

```bash
pip install -r requirements.txt
python -m nltk.downloader punkt stopwords
```

## Run

```bash
# Text mode
python main.py

# Voice mode
python main.py --voice
```

## Folder Structure

```
AI_OS/
  ├── main.py
  ├── requirements.txt
  ├── .env               ← GROQ_API_KEY (future use)
  ├── .gitignore
  ├── stt/
  │   └── whisper_stt.py
  ├── nlp/
  │   ├── preprocessor.py
  │   └── semantic.py
  ├── command_engine/
  │   ├── executor.py
  │   └── validator.py
  ├── system_monitor/
  │   ├── cpu.py
  │   ├── memory.py
  │   └── disk.py
  └── utils/
      ├── colors.py
      ├── history.py
      ├── logger.py
      └── suggestions.py
```

## Team Setup

1. Repo clone karo
2. `pip install -r requirements.txt`
3. `python main.py`










# 🤖 AI OS — AI Interface Layer over OS

> Hindi | English | Hinglish — sab samajhta hoon 🇮🇳

## What is this?
We are building an AI-driven execution & validation layer that sits between 
natural language, compiler/runtime, and the operating system.

## Architecture
```
User (Voice / Text)
       ↓
STT Layer         — Whisper (Voice → Text)
       ↓
NLP Layer         — Preprocessor + Semantic Analysis (NLTK)
       ↓
Command Engine    — Validator + Executor (pure Python)
       ↓
System Monitor    — CPU / RAM / Disk (psutil)
```

## Prerequisites (Pehle ye install karo)

### 1. FFmpeg — Voice Mode ke liye zaroori
- Download: https://github.com/BtbN/FFmpeg-Builds/releases
- Windows wali zip lo: `ffmpeg-N-...-win64-gpl.zip`
- Extract karo `C:\ffmpeg` mein
- PATH mein add karo: `C:\ffmpeg\ffmpeg-N-...\bin`
- Terminal mein check karo: `ffmpeg -version`

### 2. Python packages
```
pip install -r requirements.txt
```

### 3. NLTK data download karo
```
python -c "import nltk; nltk.download('punkt'); nltk.download('stopwords'); nltk.download('punkt_tab')"
```

### 4. .env file banao
```
GROQ_API_KEY=tumhari_groq_key_yahan
```
Free Groq API key: https://console.groq.com/keys

## Run karo
```
# Text mode
python main.py

# Voice mode
python main.py --voice
```

## Features
- 🎤 Voice Input — Hindi/Hinglish/English
- 🧠 NLP — NLTK se intent samjho
- 📁 22+ Linux/Windows commands
- 📊 System Monitor — CPU/RAM/Disk
- ⚠️  Dangerous command protection
- 📜 Command history (JSON)
- 📝 Logs file mein save

## Team Setup
1. Repo clone karo
2. FFmpeg install karo (upar dekho)
3. `pip install -r requirements.txt`
4. Apni `.env` file banao
5. `python main.py`
```

---

Save karo → phir push karo:
```
git add .
git commit -m "AI OS v3.0 - Voice + NLP + System Monitor"
git push origin BinaryAnimesh