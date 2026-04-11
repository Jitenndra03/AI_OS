# AI Layer on Linux Kernel

> **An intelligent user-space system monitoring and anomaly detection layer for Linux.**

This project sits between the Linux kernel and user applications, using machine learning to learn normal system behaviour and automatically detect — and respond to — anomalies in real time.

---

## Architecture

```
┌─────────────────────────────────────────────────┐
│                   main.py                        │
│         (orchestrates the full pipeline)         │
└────┬──────────────┬──────────────┬──────────────┘
     │              │              │
     ▼              ▼              ▼
┌─────────┐  ┌───────────┐  ┌────────────┐
│ monitor │  │    ai/    │  │ controller │
│ (psutil)│  │ IsoForest │  │  (renice)  │
└─────────┘  └───────────┘  └────────────┘
```

### Pipeline (runs every `REFRESH_INTERVAL` seconds)

```
1. Collect  →  monitor.snapshot.collect_system_snapshot()
2. Log      →  ai.data_logger.log_snapshot()           [CSV append]
3. Train    →  ai.trainer.train_model()                [after MIN_TRAINING_SAMPLES rows]
4. Predict  →  ai.detector.predict()                   [IsolationForest inference]
5. Act      →  controller.action_dispatcher.dispatch() [renice + audit log]
```

---

## Project Structure

```
AI_OS/
├── data/
│   └── metrics.csv          ← system metric rows (auto-created)
├── logs/
│   └── ai_os.log            ← structured runtime log
├── models/
│   └── anomaly_model.joblib ← persisted IsolationForest (auto-created after warm-up)
├── src/
│   ├── main.py              ← entry point
│   ├── config/
│   │   └── settings.py      ← all tunable constants
│   ├── monitor/             ← psutil-based metric collectors
│   │   ├── cpu_monitor.py
│   │   ├── memory_monitor.py
│   │   ├── disk_monitor.py
│   │   ├── process_monitor.py
│   │   └── snapshot.py      ← combines all monitors into one flat dict
│   ├── ai/
│   │   ├── data_logger.py   ← CSV row appender
│   │   ├── trainer.py       ← IsolationForest train / save / load
│   │   └── detector.py      ← feature extraction + inference
│   ├── controller/
│   │   ├── process_actions.py   ← renice, log suspicious processes
│   │   └── action_dispatcher.py ← AI signal → OS action bridge
│   └── utils/
│       ├── formatter.py     ← unit converters + CSV column schema
│       └── logger.py        ← shared Python logging setup
└── tests/
    ├── conftest.py
    ├── test_monitor.py
    ├── test_ai_detector.py
    ├── test_data_logger.py
    └── test_system_flow.py
```

---

## Installation

```bash
git clone <your-repo-url>
cd AI_OS
pip install -r requirements.txt
```

---

## Running

```bash
cd src
python main.py
```

**Warm-up phase** (first ~2.5 minutes at default settings):
```
[✓  Normal ] CPU= 18.2%  MEM= 52.1%  DISK= 43.0%  rows=   12  (warm-up 12/30)
```

**After training** (model predicts on every snapshot):
```
[✓  Normal ] CPU= 20.1%  MEM= 53.0%  DISK= 43.0%  rows=   35
[⚠  ANOMALY] CPU= 94.7%  MEM= 88.2%  DISK= 43.0%  rows=   36
```

Anomaly events produce structured entries in `logs/ai_os.log` and renice the top CPU process.

---

## Configuration

Edit `src/config/settings.py`:

| Setting | Default | Description |
|---|---|---|
| `REFRESH_INTERVAL` | `5` | Seconds between snapshots |
| `TOP_PROCESS_LIMIT` | `10` | Top N processes tracked |
| `MIN_TRAINING_SAMPLES` | `30` | Rows needed before first training |
| `RETRAIN_EVERY_N` | `50` | Retrain after every N cycles |
| `ANOMALY_CONTAMINATION` | `0.05` | Expected anomaly fraction (0–0.5) |
| `RENICE_VALUE` | `10` | nice value applied to flagged processes |

---

## Testing

```bash
pip install -r requirements.txt
PYTHONPATH=src pytest tests/ -v
```

---

## Key Design Decisions

- **No rule-based logic** — all decisions are made by IsolationForest, not hardcoded thresholds  
- **Warm-up period** — the model learns *your* machine's normal baseline before making predictions  
- **Periodic retraining** — the model adapts as your system usage patterns change over time  
- **Safe actions only** — the controller only renices (lowers scheduling priority), never kills processes  
- **Persistent model** — the trained model is saved to disk and reloaded on restart, so warm-up is skipped after the first run
