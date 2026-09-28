"""Evaluate a staged adapter or a stored commit without modifying either."""

import json
import tempfile
from pathlib import Path

import typer

from aethel.commands._common import INTERSPERSED, console, handle_errors
from aethel.core.commits import resolve_commitish
from aethel.core.errors import AethelError
from aethel.core.repo import Repo

app = typer.Typer(context_settings=INTERSPERSED)


@app.callback(invoke_without_command=True)
@handle_errors
def evaluate(
    target: str | None = typer.Argument(None, help="Commit or branch; defaults to the workspace."),
    split: str = typer.Option("validation", "--split", help="validation or test; keep test for final reporting."),
    json_output: bool = typer.Option(False, "--json", help="Print the complete evaluation record as JSON."),
):
    if split not in {"validation", "test"}:
        raise AethelError("Evaluation split must be validation or test")
    try:
        from aethel.evaluation.evaluator import evaluate_patch
    except ImportError as exc:
        raise AethelError("Evaluation needs the ML extra: pip install -e '.[ml]'") from exc
    repo = Repo.discover()
    try:
        if target:
            _, digest = resolve_commitish(repo, target)
            commit = repo.objects.read_json("commits", digest)
            with tempfile.TemporaryDirectory(prefix="aethel_eval_") as directory:
                repo.objects.extract_tree(commit["tree"], directory)
                result = evaluate_patch(repo, Path(directory), split=split)
        else:
            result = evaluate_patch(repo, repo.workspace_dir, split=split)
    except (OSError, ValueError) as exc:
        raise AethelError(str(exc)) from exc
    if json_output:
        typer.echo(json.dumps(result, indent=2, allow_nan=False))
    else:
        console.print(f"{split}: {result['sample_count']} examples")
        console.print(f"Accuracy: {result['accuracy']:.4f}; macro-F1: {result['macro_f1']:.4f}; loss: {result['eval_loss']:.6f}")
        console.print(f"Evaluation specification: {result['evaluation_spec']['hash']}")
