"""Prepare two small measured DistilBERT adapters for the publication demo.

Downloads public SST-2 rows and a pinned base. Never invents metrics or replaces
an existing demo directory. Public pinning and checkpoint transactions are separate.
"""

import argparse
import csv
import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", type=Path, default=ROOT / ".demo" / "public-review")
    parser.add_argument("--samples", type=int, default=600)
    args = parser.parse_args()
    if not 100 <= args.samples <= 5000:
        raise SystemExit("Choose between 100 and 5000 pilot examples")
    target = args.dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    work = target / "work"
    try:
        work.mkdir()
    except FileExistsError as exc:
        raise SystemExit("Review work path already exists; choose a new --dir") from exc

    import torch

    from aethel.commands.train import run_training
    from aethel.core.commits import build_base_object, create_commit
    from aethel.core.repo import Repo
    from aethel.evaluation.evaluator import evaluate_current_vs_parent
    from aethel.remote.objects import UPLOAD_ORDER, build_push_plan
    from hub.log import TransparencyLog
    from hub.storage import HubStorage

    torch.set_num_threads(min(8, os.cpu_count() or 1))
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    model_id = "distilbert/distilbert-base-uncased"
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        metadata = client.get(f"https://huggingface.co/api/models/{model_id}")
        metadata.raise_for_status()
        revision = metadata.json()["sha"]
        rows = []
        for offset in range(0, args.samples, 100):
            response = client.get("https://datasets-server.huggingface.co/rows", params={
                "dataset": "stanfordnlp/sst2", "config": "default", "split": "train",
                "offset": offset, "length": min(100, args.samples - offset),
            })
            response.raise_for_status()
            rows.extend(record["row"] for record in response.json()["rows"])
    with (work / "reviews.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["text", "label"])
        writer.writerows((row["sentence"], row["label"]) for row in rows)
    print(f"Downloaded {len(rows)} public SST-2 examples; pinned base revision {revision}", flush=True)
    repo = Repo.create(work, {"model_id": model_id, "revision_sha": revision, "author": "Sathwik"})
    base = repo.objects.write_json("bases", build_base_object(model_id, revision))
    records = []
    os.chdir(work)
    for seed in (42, 43):
        config = work / f"seed-{seed}.yaml"
        config.write_text(
            "dataset: reviews.csv\ntask_type: sequence_classification\n"
            "label_names: [negative, positive]\nlora_rank: 4\nlora_alpha: 8\n"
            "batch_size: 8\nepochs: 1\nlearning_rate: 0.0001\n"
            f"seed: {seed}\nsplit_seed: 42\nmax_length: 64\neval_batch_size: 16\n"
        )
        info = run_training(str(config))
        info["evaluation"] = evaluate_current_vs_parent(repo, repo.workspace_dir)
        digest = create_commit(repo, message=f"SST-2 pilot, training seed {seed}", author="Sathwik", base_hash=base, training_info=info)
        records.append({"commit": digest, "seed": seed, "evaluation": info["evaluation"]})
        print(f"Committed seed {seed}: {digest}", flush=True)
    storage = HubStorage(target / "hub-data")
    plan = build_push_plan(repo, "main", records[-1]["commit"])
    for kind in UPLOAD_ORDER:
        for digest in plan.commit_order if kind == "commits" else sorted(plan.objects.get(kind, set())):
            content = repo.objects.path_for(kind, digest).read_bytes()
            if kind == "blobs":
                storage.put_blob(digest, content)
            else:
                storage.put_json_object(kind, digest, content)
    storage.publish_branch("distilbert-sst2-review", "main", records[-1]["commit"], log=TransparencyLog(target / "hub-data" / "log.jsonl"))
    report = {"scope": "small real-data pilot; validation results only", "dataset": "stanfordnlp/sst2", "downloaded_rows": len(rows), "model_id": model_id, "revision": revision, "runs": records}
    (target / "models.json").write_text(json.dumps(report, indent=2) + "\n")
    print(f"Prepared two measured adapters and Hub store at {target}", flush=True)


if __name__ == "__main__":
    main()
