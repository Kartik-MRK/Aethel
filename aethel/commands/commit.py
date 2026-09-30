"""``aethel commit`` -- snapshot the workspace as a new commit.

Reads whatever `aethel train` staged into the workspace and records it in the
object store. Like the original design decision (and unlike the earliest
prototype), commit does NOT train -- the two are decoupled.

The detached-HEAD guard is preserved from the original implementation
(commands/commit.py:315-329).
"""

from datetime import datetime, timezone

import typer

from aethel.commands._common import (
    console,
    err_console,
    handle_errors,
    render_detached_head,
    short,
)
from aethel.core.commits import build_base_object, create_commit, require_staged_adapter
from aethel.core.errors import DetachedHead, InvalidRef
from aethel.core.hashing import hash_bytes
from aethel.core.repo import Repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def commit_callback(
    ctx: typer.Context,
    message: str = typer.Option(..., "-m", "--message", help="Commit message."),
    require_evaluation: bool = typer.Option(False, "--require-evaluation", help="Refuse to commit if held-out evaluation fails."),
):
    """Create a commit from the current workspace."""
    if ctx.invoked_subcommand is None:
        run_commit(message=message, require_evaluation=require_evaluation)


@handle_errors
def run_commit(message: str, require_evaluation: bool = False) -> None:
    repo = Repo.discover()
    config = repo.read_config()
    try:
        branch = repo.refs.require_attached_branch()
        parent_hash = repo.refs.read_branch(branch)
    except DetachedHead as exc:
        render_detached_head(exc, attempted=message)
        raise typer.Exit(code=1) from exc
    require_staged_adapter(repo.workspace_dir)

    # The base reference object is derived from the pinned revision; write it
    # (idempotently) so every commit has a base to point at.
    base_hash = repo.objects.write_json(
        "bases", build_base_object(config["model_id"], config["revision_sha"])
    )

    training_info_path = repo.workspace_dir / "training_info.json"
    training_info = {}
    training_info_digest = None
    if training_info_path.is_file():
        import json

        try:
            raw_info = training_info_path.read_bytes()
            training_info_digest = hash_bytes(raw_info)
            training_info = json.loads(raw_info)
            if not isinstance(training_info, dict):
                training_info = {}
        except json.JSONDecodeError as exc:
            err_console.print(
                f"[yellow]warning: training_info.json is invalid JSON, "
                f"recording an empty record: {exc}[/yellow]"
            )

    training_info.pop("evaluation", None)
    evaluation = None
    evaluation_state = {"status": "skipped", "reason": "ML dependencies are unavailable", "current": None}

    try:
        from aethel.evaluation.evaluator import evaluate_current_vs_parent
    except ImportError:
        evaluate_current_vs_parent = None

    if evaluate_current_vs_parent is not None:
        try:
            evaluation = evaluate_current_vs_parent(
                repo,
                repo.workspace_dir,
            )
        except Exception as exc:
            evaluation_state = {"status": "failed", "reason": str(exc), "current": None}
            err_console.print(
                f"[yellow]warning: evaluation failed: {exc}[/yellow]"
            )

    if evaluation is None:
        training_info["evaluation"] = evaluation_state
        if require_evaluation:
            err_console.print("[red]Commit refused: held-out evaluation is required.[/red]")
            raise typer.Exit(code=1)

    if evaluation is not None:
        current = evaluation["current"]
        if current.get("training_info_sha256") and current["training_info_sha256"] != training_info_digest:
            raise InvalidRef("Training metadata changed before evaluation. Retry the commit.")
        parent = evaluation["parent"]
        comparison = evaluation["comparison"]

        console.print("\n[bold]Evaluation Results[/bold]")

        console.print(
            f"Current accuracy: "
            f"[cyan]{current['accuracy']:.4f}[/cyan]"
        )
        console.print(
            f"Current loss: "
            f"[cyan]{current['eval_loss']:.6f}[/cyan]"
        )
        if "macro_f1" in current:
            console.print(f"Macro-F1: {current['macro_f1']:.4f}; samples: {current['sample_count']}")
        console.print(
            f"Adapter size: "
            f"[cyan]{current['adapter_size_mb']:.2f} MiB[/cyan]"
        )

        if parent is not None:
            console.print(
                f"Parent accuracy: "
                f"[cyan]{parent['accuracy']:.4f}[/cyan]"
            )
            console.print(
                f"Parent loss: "
                f"[cyan]{parent['eval_loss']:.6f}[/cyan]"
            )

            console.print(
                f"Accuracy change: "
                f"[cyan]{comparison['accuracy_change']:+.4f}[/cyan]"
            )

            console.print(
                f"Loss change: "
                f"[cyan]{comparison['loss_change']:+.6f}[/cyan]"
            )

            console.print(
                f"Improved: "
                f"[green]{comparison['improved']}[/green]"
            )
        elif evaluation.get("comparison_status") == "not_comparable":
            console.print(f"[yellow]Parent not comparable: {evaluation['comparison_reason']}[/yellow]")
        else:
            console.print("Parent: [yellow]None (first commit)[/yellow]")

        training_info["evaluation"] = evaluation

    try:
        commit_hash = create_commit(
            repo,
            message=message,
            author=config.get("author", "unknown"),
            base_hash=base_hash,
            training_info=training_info,
            timestamp=datetime.now(timezone.utc).isoformat(),
            expected_branch=branch,
            expected_parent=parent_hash,
        )
    except DetachedHead as exc:
        render_detached_head(exc, attempted=message)
        raise typer.Exit(code=1) from exc

    console.print(f"[bold green]Committed[/bold green] [white]{short(commit_hash)}[/white]")
    console.print(f"Branch: [cyan]{repo.refs.require_attached_branch()}[/cyan]")
