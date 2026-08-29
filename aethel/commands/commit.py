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
from aethel.core.commits import build_base_object, create_commit
from aethel.core.errors import DetachedHead
from aethel.core.repo import Repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def commit_callback(
    ctx: typer.Context,
    message: str = typer.Option(..., "-m", "--message", help="Commit message."),
):
    """Create a commit from the current workspace."""
    if ctx.invoked_subcommand is None:
        run_commit(message=message)


@handle_errors
def run_commit(message: str) -> None:
    repo = Repo.discover()
    config = repo.read_config()

    # The base reference object is derived from the pinned revision; write it
    # (idempotently) so every commit has a base to point at.
    base_hash = repo.objects.write_json(
        "bases", build_base_object(config["model_id"], config["revision_sha"])
    )

    training_info_path = repo.workspace_dir / "training_info.json"
    training_info = {}
    if training_info_path.is_file():
        import json

        try:
            training_info = json.loads(training_info_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            err_console.print(
                f"[yellow]warning: training_info.json is invalid JSON, "
                f"recording an empty record: {exc}[/yellow]"
            )

    evaluation = None

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
            err_console.print(
                f"[yellow]warning: evaluation failed: {exc}[/yellow]"
            )

    if evaluation is not None:
        current = evaluation["current"]
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
        console.print(
            f"Adapter size: "
            f"[cyan]{current['adapter_size_mb']:.2f} MB[/cyan]"
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
        )
    except DetachedHead as exc:
        render_detached_head(exc, attempted=message)
        raise typer.Exit(code=1) from exc

    console.print(f"[bold green]Committed[/bold green] [white]{short(commit_hash)}[/white]")
    console.print(f"Branch: [cyan]{repo.refs.require_attached_branch()}[/cyan]")
