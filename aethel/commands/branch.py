"""``aethel branch`` -- list, create, and delete branches.

A branch is a file under ``.aethel/refs/heads/`` holding one commit hash.
Creating one is cheap and involves no copying of weights.
"""


import typer

from aethel.commands._common import INTERSPERSED, console, err_console, handle_errors, short
from aethel.core.commits import read_commit, resolve_commitish
from aethel.core.errors import BranchNotFound, InvalidRef
from aethel.core.repo import Repo

app = typer.Typer()
app.info.context_settings = INTERSPERSED


@app.callback(invoke_without_command=True)
def branch_callback(
    ctx: typer.Context,
    name: str | None = typer.Argument(None, help="Branch to create. Omit to list."),
    start_point: str | None = typer.Argument(
        None, help="Commit or branch to start from. Defaults to HEAD."
    ),
    delete: str | None = typer.Option(None, "--delete", "-d", help="Delete a branch."),
):
    """List branches, or create one at HEAD."""
    if ctx.invoked_subcommand is not None:
        return

    if delete:
        run_delete(delete)
    elif name:
        run_create(name, start_point)
    else:
        run_list()


@handle_errors
def run_list() -> None:
    repo = Repo.discover()
    head = repo.refs.read_head()
    branches = repo.refs.list_branches()

    if not branches:
        console.print("[dim]No branches yet. Make your first commit.[/dim]")
        return

    for branch in branches:
        tip = repo.refs.read_branch(branch)
        marker = "*" if (not head.is_detached and head.branch == branch) else " "
        style = "bold green" if marker == "*" else "white"

        if tip is None:
            console.print(f"{marker} [{style}]{branch}[/{style}] [dim](no commits)[/dim]")
            continue

        commit = read_commit(repo, tip)
        console.print(
            f"{marker} [{style}]{branch}[/{style}] "
            f"[dim]{short(tip)}[/dim] {commit['message']}"
        )

    if head.is_detached:
        console.print()
        console.print(f"[yellow]HEAD detached at {short(head.commit or '')}[/yellow]")


@handle_errors
def run_create(name: str, start_point: str | None) -> None:
    repo = Repo.discover()

    if start_point:
        _, commit_hash = resolve_commitish(repo, start_point)
    else:
        commit_hash = repo.refs.resolve_head_commit()
        if commit_hash is None:
            raise InvalidRef(
                "No commits yet — nothing for a branch to point at. "
                "Make your first commit, then create a branch."
            )

    repo.refs.create_branch(name, commit_hash)

    console.print(
        f"[bold green]Created branch[/bold green] [cyan]{name}[/cyan] "
        f"at {short(commit_hash)}"
    )
    console.print(f"[dim]Switch to it with: aethel checkout {name}[/dim]")


@handle_errors
def run_delete(name: str) -> None:
    repo = Repo.discover()
    head = repo.refs.read_head()

    if not head.is_detached and head.branch == name:
        err_console.print(
            f"[bold red]Cannot delete '{name}' — it is the current branch.[/bold red]"
        )
        err_console.print("Check out a different branch first.")
        raise typer.Exit(code=1)

    if not repo.refs.branch_exists(name):
        raise BranchNotFound(f"Branch '{name}' does not exist.")

    tip = repo.refs.read_branch(name)
    repo.refs.branch_path(name).unlink()

    console.print(f"[bold green]Deleted branch[/bold green] [cyan]{name}[/cyan]")
    if tip:
        console.print(
            f"[dim]Its commits are still in the object store at {short(tip)} "
            f"— nothing was destroyed.[/dim]"
        )
