# ML workload recognition

The manager works without a model. ML predictions are optional. Deterministic
resource thresholds and sustained duration establish active work and protected
processes. A sufficiently confident prediction can refine only the label of an
already protected `HEAVY_UNKNOWN` workload. It cannot activate work, change
confidence/protection, choose process targets, or bypass optimizer safety rules.

From the repository root, using the environment containing NumPy and scikit-learn:

```bash
python scripts/train_demo_model.py --output-dir /tmp/ai-os-ml-demo --seed 42
```

This writes `workload_model.json`, `training_data.csv` and `metrics.json`. All data
is **synthetic**, with deliberately separable executable hints. The reported
accuracy validates the demonstration pipeline, not real-world accuracy. The demo
path is separate from the production model; loading it is an explicit opt-in.
The fixture can also be imported as `scripts.train_demo_model.make_demo_dataset`.

For real captures, retain chronological row order and use
`TrainingDataCollector.load()` followed by `WorkloadClassifierModel.train(X, y)`.
The first 80% train the forest, and the untouched final 20% evaluate it. Every
class must occur in both partitions; at least 100 total samples and 20 per class
are required. Validation accuracy below 0.60, zero/NaN scores, invalid features or
unknown labels reject promotion. `metrics.cv_accuracy` is retained for API
compatibility but means chronological holdout accuracy; `validation_method`
records that explicitly. Labels collected from rules measure agreement with
those rules, not independent ground truth. Collect separate workload sessions
and manually verify labels before claiming generalization to unseen sessions.

Training uses one worker, at most 150 trees, depth 12 and 50,000 samples. Online
training backs off at least 30 seconds after each attempt and requires new
samples. Retention does not reset the monotonic ingestion counter. Quiet NORMAL
samples are downsampled without requiring a positive detector confidence;
pending busy warmup samples are excluded. `close()` joins in-flight training.

Models are data-only JSON; legacy pickle/joblib models are ignored without
unpickling. Retrain them from trusted CSV. Load validates exact feature order,
labels, finite probabilities, reachable acyclic bounded trees, sizes and quality
metadata. Model files must belong to the executing user and cannot be group or
world writable or symlinks. Writes use private atomic replacement. Training CSV
files must be owned private regular files with the exact feature header.
