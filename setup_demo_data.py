"""
setup_demo_data.py
Downloads 5 small NLP classification datasets from Hugging Face Hub
and saves them locally as CSV files for Aethel-Git training demos.

Datasets chosen for distilbert-base-uncased (sequence classification):
  1. SST-2            Binary sentiment analysis  (positive / negative)
  2. Rotten Tomatoes  Movie review sentiment (positive / negative)
  3. Emotion          6-class emotion detection  (joy, sadness, anger, etc.)
  4. AG News          4-class topic classification (World, Sports, Business, Sci/Tech)
  5. Tweet Eval Hate  Binary hate-speech detection

Each dataset is saved as datasets/<name>/train.csv with columns: text, label
"""

import csv
import os
import sys


def save_to_csv(rows: list[dict], output_path: str) -> int:
    """Write a list of dicts with 'text' and 'label' keys to a CSV file."""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["text", "label"])
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def download_sst2(base_dir: str, max_samples: int = 800) -> None:
    """Stanford Sentiment Treebank v2: binary sentiment."""
    from datasets import load_dataset
    print("⬇  Downloading SST-2...")
    ds = load_dataset("glue", "sst2", split="train")
    rows = []
    for i, item in enumerate(ds):
        if i >= max_samples:
            break
        rows.append({"text": item["sentence"], "label": item["label"]})
    count = save_to_csv(rows, os.path.join(base_dir, "sst2", "train.csv"))
    print(f"   ✅ SST-2: {count} samples → {base_dir}/sst2/train.csv")


def download_rotten_tomatoes(base_dir: str, max_samples: int = 800) -> None:
    """Rotten Tomatoes: movie review binary sentiment."""
    from datasets import load_dataset
    print("⬇  Downloading Rotten Tomatoes...")
    ds = load_dataset("rotten_tomatoes", split="train")
    rows = []
    for i, item in enumerate(ds):
        if i >= max_samples:
            break
        rows.append({"text": item["text"], "label": item["label"]})
    count = save_to_csv(rows, os.path.join(base_dir, "rotten_tomatoes", "train.csv"))
    print(f"   ✅ Rotten Tomatoes: {count} samples → {base_dir}/rotten_tomatoes/train.csv")


def download_emotion(base_dir: str, max_samples: int = 800) -> None:
    """Emotion: 6-class emotion detection (sadness, joy, love, anger, fear, surprise)."""
    from datasets import load_dataset
    print("⬇  Downloading Emotion...")
    ds = load_dataset("dair-ai/emotion", split="train")
    rows = []
    for i, item in enumerate(ds):
        if i >= max_samples:
            break
        rows.append({"text": item["text"], "label": item["label"]})
    count = save_to_csv(rows, os.path.join(base_dir, "emotion", "train.csv"))
    print(f"   ✅ Emotion: {count} samples → {base_dir}/emotion/train.csv")


def download_ag_news(base_dir: str, max_samples: int = 800) -> None:
    """AG News: 4-class topic classification (World, Sports, Business, Sci/Tech)."""
    from datasets import load_dataset
    print("⬇  Downloading AG News...")
    ds = load_dataset("fancyzhx/ag_news", split="train")
    rows = []
    for i, item in enumerate(ds):
        if i >= max_samples:
            break
        rows.append({"text": item["text"], "label": item["label"]})
    count = save_to_csv(rows, os.path.join(base_dir, "ag_news", "train.csv"))
    print(f"   ✅ AG News: {count} samples → {base_dir}/ag_news/train.csv")


def download_tweet_eval_hate(base_dir: str, max_samples: int = 800) -> None:
    """Tweet Eval Hate: binary hate speech detection."""
    from datasets import load_dataset
    print("⬇  Downloading Tweet Eval (Hate Speech)...")
    ds = load_dataset("tweet_eval", "hate", split="train")
    rows = []
    for i, item in enumerate(ds):
        if i >= max_samples:
            break
        rows.append({"text": item["text"], "label": item["label"]})
    count = save_to_csv(rows, os.path.join(base_dir, "tweet_eval_hate", "train.csv"))
    print(f"   ✅ Tweet Eval Hate: {count} samples → {base_dir}/tweet_eval_hate/train.csv")


def main():
    base_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "datasets")
    max_samples = 800  # Enough for demo, fast to train

    print("=" * 60)
    print("  Aethel-Git Demo Data Setup")
    print(f"  Target: {base_dir}")
    print(f"  Max samples per dataset: {max_samples}")
    print("=" * 60)

    try:
        import datasets  # noqa: F401
    except ImportError:
        print("❌ 'datasets' library not installed. Run: pip install datasets")
        sys.exit(1)

    download_sst2(base_dir, max_samples)
    download_rotten_tomatoes(base_dir, max_samples)
    download_emotion(base_dir, max_samples)
    download_ag_news(base_dir, max_samples)
    download_tweet_eval_hate(base_dir, max_samples)

    print()
    print("=" * 60)
    print("  ✅ All datasets downloaded successfully!")
    print("=" * 60)


if __name__ == "__main__":
    main()
