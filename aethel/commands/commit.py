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
