"""Restore a published repository without loading the ML stack."""

import os
from pathlib import Path
from typing import Annotated

import typer

from aethel.commands._common import INTERSPERSED, console, handle_errors
from aethel.commands.init import default_author
from aethel.commands.push import resolve_remote
from aethel.core.errors import AethelError
from aethel.remote.http import HubClient
from aethel.remote.sync import clone_repository

app = typer.Typer()
app.info.context_settings = INTERSPERSED


@app.callback(invoke_without_command=True)
def clone_callback(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Published Hub repository name."),
    destination: Annotated[Path | None, typer.Argument(help="New directory. Defaults to the repository name.")] = None,
    remote: str | None = typer.Option(None, "--remote", "-r", help="Hub URL. Defaults to $AETHEL_HUB_URL or localhost."),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Branch to restore. Defaults to main or the only branch."),
    author: str | None = typer.Option(None, "--author", help="Author for future local commits."),
    token: str | None = typer.Option(None, "--token", help="Hub token. Defaults to $AETHEL_HUB_TOKEN."),
):
    """Download verified history and restore an adapter workspace."""
    if ctx.invoked_subcommand is None:
        run_clone(name, destination, remote=remote, branch=branch, author=author, token=token)


@handle_errors
def run_clone(
    name: str, destination: Path | None = None, *, remote: str | None = None,
    branch: str | None = None, author: str | None = None, token: str | None = None,
) -> None:
    try:
        with HubClient(resolve_remote(remote), token=token or os.environ.get("AETHEL_HUB_TOKEN")) as client:
            repo = clone_repository(
                client, name, destination if destination is not None else Path(name),
                branch=branch, author=author or default_author(),
            )
    except OSError as exc:
        raise AethelError(f"Could not create clone: {exc}") from exc
    console.print(f"Cloned {name} into {repo.root}", markup=False)
    console.print("Base weights and source datasets are downloaded separately.")
