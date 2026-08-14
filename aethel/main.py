"""Aethel CLI entry point.

`train` is registered lazily. It is the only command that needs torch, and
importing it at module scope would make `aethel --help`, `aethel log` and
every other offline command fail on a base install that has no ML stack.
"""

import typer

from aethel.commands import branch, checkout, commit, fsck, init, log, push

app = typer.Typer(
    help="Aethel — version control and provenance for LoRA adapters.",
    add_completion=False,
    no_args_is_help=True,
)

app.add_typer(init.app, name="init", help="Initialize a repository.")
app.add_typer(commit.app, name="commit", help="Snapshot the workspace as a commit.")
app.add_typer(branch.app, name="branch", help="List, create, or delete branches.")
app.add_typer(checkout.app, name="checkout", help="Restore a branch or commit.")
app.add_typer(log.app, name="log", help="Show commit history.")
app.add_typer(push.app, name="push", help="Publish a branch to a Hub.")
app.add_typer(fsck.app, name="fsck", help="Verify repository integrity.")


@app.command()
def status():
    """Show the current branch and workspace state."""
    log.run_status()


@app.command()
def train(
    config: str = typer.Option(..., "--config", "-c", help="Path to a YAML training config."),
    allow_stub: bool = typer.Option(
        False, "--allow-stub", help="Train on synthetic data if the dataset is missing."
    ),
):
    """Run LoRA training and stage the adapter into the workspace."""
    try:
        from aethel.commands import train as train_cmd
    except ImportError as exc:
        typer.secho(
            "Training needs the ML extra. Install it with:\n"
            "  pip install 'aethel[ml]'\n"
            f"({exc})",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1) from exc

    train_cmd.train(config=config, allow_stub=allow_stub)


if __name__ == "__main__":
    app()
