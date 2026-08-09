"""``aethel checkout`` -- restore a commit's workspace.

Restores from the commit's `tree` (content-addressed) rather than from
whatever files happen to be on disk, so a checkout reproduces the exact
workspace that was committed -- adapter weights, adapter_config.json and
training_info.json alike.

Checking out a raw commit hash detaches HEAD, matching Git. `aethel commit`
refuses to run while detached, so the state is safe to be in.
"""


import typer

from aethel.commands._common import console, err_console, handle_errors, short
from aethel.core.commits import read_commit, resolve_commitish
from aethel.core.repo import Repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def checkout_callback(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Branch name, commit hash, or short hash."),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Overwrite workspace files that differ from the current commit.",
    ),
):
    """Restore the workspace to a branch or commit."""
    if ctx.invoked_subcommand is None:
        run_checkout(target=target, force=force)


@handle_errors
def run_checkout(target: str, force: bool) -> None:
    repo = Repo.discover()
    kind, commit_hash = resolve_commitish(repo, target)
    commit = read_commit(repo, commit_hash)

    workspace = repo.workspace_dir
    current_head = repo.refs.resolve_head_commit()

    # Refuse to clobber uncommitted work unless forced. Compares the workspace
    # against the current commit's tree rather than trusting mtimes.
    if not force and current_head is not None and current_head != commit_hash:
        dirty = _workspace_differs_from(repo, current_head)
        if dirty:
            err_console.print(
                "[bold yellow]Workspace has uncommitted changes:[/bold yellow]"
            )
            for name in sorted(dirty)[:10]:
                err_console.print(f"  {name}")
            err_console.print()
            err_console.print("Commit them, or re-run with [green]--force[/green] to discard.")
            raise typer.Exit(code=1)

    restored = repo.objects.extract_tree(commit["tree"], workspace)

    if kind == "branch":
        repo.refs.set_head_to_branch(target.strip())
        console.print(f"[bold green]Switched to branch[/bold green] [cyan]{target}[/cyan]")
    else:
        repo.refs.set_head_detached(commit_hash)
        console.print(
            f"[bold yellow]HEAD is now detached at[/bold yellow] {short(commit_hash)}"
        )
        console.print(
            "[dim]Commits are blocked while detached. "
            "Create a branch here with: aethel branch <name>[/dim]"
        )

    console.print(f"Commit:  [white]{short(commit_hash)}[/white] {commit['message']}")
    console.print(f"Restored {len(restored)} file(s) into {workspace}")


def _workspace_differs_from(repo: Repo, commit_hash: str) -> list[str]:
    """Return workspace paths that differ from a commit's tree."""
    from aethel.core.hashing import hash_file

    commit = read_commit(repo, commit_hash)
    tree = repo.objects.read_tree(commit["tree"])
    workspace = repo.workspace_dir

    differences: list[str] = []

    for name, blob_hash in tree.items():
        path = workspace / name
        if not path.is_file():
            differences.append(f"deleted:  {name}")
        elif hash_file(path) != blob_hash:
            differences.append(f"modified: {name}")

    if workspace.is_dir():
        for path in sorted(workspace.rglob("*")):
            if path.is_file():
                relative = path.relative_to(workspace).as_posix()
                if relative not in tree:
                    differences.append(f"new:      {relative}")

    return differences
