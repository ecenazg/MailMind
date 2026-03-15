"""
scripts/evaluate_classifier.py
────────────────────────────────
Evaluate classifier accuracy against a labelled CSV dataset.

CSV format (no header row required — but helpful):
  message_id, sender, subject, body_text, true_intent

true_intent must be one of: task_request | inquiry | newsletter | urgent

Usage
─────
    python scripts/evaluate_classifier.py --csv data/labelled_emails.csv
    python scripts/evaluate_classifier.py --csv data/labelled_emails.csv --limit 50

Output
──────
    • Per-class precision / recall / F1
    • Overall accuracy
    • Confusion matrix
    • Langfuse trace for every prediction (if configured)
    • Results saved to results/eval_<timestamp>.json
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from classifiers.email_classifier import EmailClassifier
from observability.logger import get_logger
from utils.models import EmailIntent, EmailMessage

log = get_logger(__name__)

INTENT_VALUES = [e.value for e in EmailIntent]


def load_dataset(csv_path: Path, limit: int | None) -> list[dict]:
    """Load labelled emails from CSV."""
    rows = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader):
            if limit and i >= limit:
                break
            rows.append(row)
    return rows


def evaluate(csv_path: Path, limit: int | None) -> dict:
    classifier = EmailClassifier()
    rows = load_dataset(csv_path, limit)
    total = len(rows)

    if total == 0:
        print("[ERROR] No rows loaded from CSV.")
        sys.exit(1)

    print(f"Evaluating {total} emails...")

    correct = 0
    results = []
    confusion: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for idx, row in enumerate(rows, 1):
        email = EmailMessage(
            message_id=row.get("message_id", f"eval-{idx}"),
            thread_id=f"eval-thread-{idx}",
            sender=row.get("sender", "eval@example.com"),
            subject=row.get("subject", ""),
            body_text=row.get("body_text", ""),
            received_at=datetime.now(tz=timezone.utc),
        )

        true_intent = row.get("true_intent", "").strip().lower()
        prediction  = classifier.classify(email)
        pred_intent = prediction.intent.value

        hit = (pred_intent == true_intent)
        if hit:
            correct += 1

        confusion[true_intent][pred_intent] += 1
        results.append({
            "message_id":   email.message_id,
            "subject":      email.subject,
            "true_intent":  true_intent,
            "pred_intent":  pred_intent,
            "confidence":   prediction.confidence,
            "correct":      hit,
        })

        if idx % 20 == 0:
            running_acc = correct / idx
            print(f"  [{idx}/{total}] running accuracy: {running_acc:.1%}")

    # ── Per-class metrics ──────────────────────────────────────────────────
    class_metrics: dict[str, dict] = {}
    for intent in INTENT_VALUES:
        tp = confusion[intent][intent]
        fp = sum(confusion[other][intent] for other in INTENT_VALUES if other != intent)
        fn = sum(confusion[intent][other] for other in INTENT_VALUES if other != intent)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall    = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1        = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0.0
        )
        class_metrics[intent] = {
            "precision": round(precision, 4),
            "recall":    round(recall, 4),
            "f1":        round(f1, 4),
            "support":   sum(confusion[intent].values()),
        }

    accuracy = correct / total
    summary = {
        "accuracy":        round(accuracy, 4),
        "correct":         correct,
        "total":           total,
        "class_metrics":   class_metrics,
        "confusion_matrix": {k: dict(v) for k, v in confusion.items()},
        "evaluated_at":    datetime.utcnow().isoformat(),
        "results":         results,
    }

    # ── Print summary ──────────────────────────────────────────────────────
    print(f"\n{'='*50}")
    print(f"  Accuracy: {accuracy:.1%}  ({correct}/{total})")
    print(f"{'='*50}")
    print(f"  {'Intent':<16} {'Precision':>10} {'Recall':>8} {'F1':>8} {'Support':>9}")
    print(f"  {'-'*52}")
    for intent, m in class_metrics.items():
        print(
            f"  {intent:<16} {m['precision']:>10.3f} "
            f"{m['recall']:>8.3f} {m['f1']:>8.3f} {m['support']:>9}"
        )
    print(f"{'='*50}\n")

    # ── Save results ───────────────────────────────────────────────────────
    out_dir = Path("results")
    out_dir.mkdir(exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"eval_{ts}.json"
    out_path.write_text(json.dumps(summary, indent=2))
    print(f"Results saved → {out_path}")

    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate MailMind classifier accuracy")
    parser.add_argument("--csv",   required=True, help="Path to labelled CSV file")
    parser.add_argument("--limit", type=int,      help="Max number of emails to evaluate")
    args = parser.parse_args()

    evaluate(Path(args.csv), args.limit)


if __name__ == "__main__":
    main()
