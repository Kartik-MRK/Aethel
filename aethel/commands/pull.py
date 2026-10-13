"""Retrieve a published branch using fast-forward updates only."""

import os

import typer

from aethel.commands._common import console, handle_errors
from aethel.commands.push import resolve_remote, resolve_repo_name
from aethel.core.errors import AethelError
from aethel.core.repo import Repo
from aethel.remote.http import HubClient
from aethel.remote.sync import pull_repository

app = typer.Typer()


@app.callback(invoke_without_command=True)
def pull_callback(
    ctx: typer.Context,
    remote: str | None = typer.Option(None, "--remote", "-r", help="Hub URL. Defaults to environment, saved URL, or localhost."),
    repo_name: str | None = typer.Option(None, "--repo", help="Published name. Defaults to saved name or directory name."),
    branch: str | None = typer.Option(None, "--branch", "-b", help="Must match the current branch."),
    token: str | None = typer.Option(None, "--token", help="Hub token. Defaults to $AETHEL_HUB_TOKEN."),
):
    """Fast-forward the current branch and restore its workspace."""
    if ctx.invoked_subcommand is None:
        run_pull(remote=remote, repo_name=repo_name, branch=branch, token=token)


@handle_errors
def run_pull(
    *, remote: str | None = None, repo_name: str | None = None,
    branch: str | None = None, token: str | None = None,
) -> None:
    repo = Repo.discover()
    url = resolve_remote(remote, configured=repo.read_config().get("hub_url"))
    try:
        with HubClient(url, token=token or os.environ.get("AETHEL_HUB_TOKEN")) as client:
            message = pull_repository(client, repo, resolve_repo_name(repo, repo_name), branch=branch)
    except OSError as exc:
        raise AethelError(f"Could not pull repository: {exc}") from exc
    console.print(message, markup=False)
