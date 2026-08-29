"""``aethel log`` -- show commit history, and ``aethel status`` -- show state.

History is walked from the object store via refs. There is no database to
consult and none to fall out of sync: the old implementation read history
from SQLite (status.py) and resolved checkouts through it (checkout.py:51-70),
which made the object store alone insufficient.
"""


import typer
from rich.table import Table

from aethel.commands._common import console, handle_errors, short
from aethel.core.commits import read_commit, resolve_commitish, walk_history
from aethel.core.repo import Repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def log_callback(
    ctx: typer.Context,
    target: str | None = typer.Argument(None, help="Branch or commit. Defaults to HEAD."),
    limit: int = typer.Option(20, "--limit", "-n", help="Maximum commits to show."),
    all_branches: bool = typer.Option(False, "--all", help="Show every branch's history."),
):
    """Display commit history."""
    if ctx.invoked_subcommand is None:
        run_log(target=target, limit=limit, all_branches=all_branches)


@handle_errors
def run_log(target: str | None, limit: int, all_branches: bool) -> None:
    repo = Repo.discover()

    if all_branches:
        starts = []
        for branch in repo.refs.list_branches():
            tip = repo.refs.read_branch(branch)
            if tip is not None:
                starts.append((branch, tip))
        if not starts:
            console.print("[dim]No commits yet.[/dim]")
            return
    elif target:
        _, commit_hash = resolve_commitish(repo, target)
        starts = [(target, commit_hash)]
    else:
        head = repo.refs.read_head()
        tip = repo.refs.resolve_head_commit()
        if tip is None:
            console.print("[dim]No commits yet. Run 'aethel train' then 'aethel commit'.[/dim]")
            return
        starts = [(head.commit if head.is_detached else head.branch, tip)]

    seen: set[str] = set()
    rows: list[tuple[str, dict, str]] = []

    for label, tip in starts:
        for commit_hash, commit in walk_history(repo, tip):
            if commit_hash in seen:
                break
            seen.add(commit_hash)
            rows.append((commit_hash, commit, label))
            if len(rows) >= limit:
                break
        if len(rows) >= limit:
            break

    rows.sort(key=lambda r: r[1].get("timestamp", ""), reverse=True)

    table = Table(title="Commit History", show_lines=False)
    table.add_column("Commit", style="cyan", no_wrap=True)
    table.add_column("When", style="magenta", no_wrap=True)
    table.add_column("Author", style="green")
    table.add_column("Accuracy", justify="right")
    table.add_column("Message", style="white")

    for commit_hash, commit, _label in rows:
        training_info = commit.get("training_info") or {}
        evaluation = training_info.get("evaluation") or {}
        current = evaluation.get("current") or {}
        metrics = training_info.get("metrics") or {}

        accuracy = current.get(
            "accuracy",
            metrics.get("eval_accuracy", metrics.get("accuracy")),
        )
        accuracy_text = f"{accuracy:.3f}" if isinstance(accuracy, (int, float)) else "—"

        table.add_row(
            short(commit_hash),
            (commit.get("timestamp") or "")[:19].replace("T", " "),
            commit.get("author", "—"),
            accuracy_text,
            commit.get("message", ""),
        )

    console.print(table)


@handle_errors
def run_status() -> None:
    """Show the current branch, HEAD, and whether the workspace is clean."""
    from aethel.core.hashing import hash_file

    repo = Repo.discover()
    head = repo.refs.read_head()
    tip = repo.refs.resolve_head_commit()

    if head.is_detached:
        console.print(f"HEAD detached at [yellow]{short(head.commit or '')}[/yellow]")
    else:
        console.print(f"On branch [bold cyan]{head.branch}[/bold cyan]")

    if tip is None:
        console.print("[dim]No commits yet.[/dim]")
        return

    commit = read_commit(repo, tip)
    console.print(f"Commit: [white]{short(tip)}[/white] {commit['message']}")

    tree = repo.objects.read_tree(commit["tree"])
    workspace = repo.workspace_dir
    changes: list[str] = []

    for name, blob_hash in sorted(tree.items()):
        path = workspace / name
        if not path.is_file():
            changes.append(f"  [red]deleted:[/red]  {name}")
        elif hash_file(path) != blob_hash:
            changes.append(f"  [yellow]modified:[/yellow] {name}")

    if workspace.is_dir():
        for path in sorted(workspace.rglob("*")):
            if path.is_file():
                relative = path.relative_to(workspace).as_posix()
                if relative not in tree:
                    changes.append(f"  [green]new:[/green]      {relative}")

    console.print()
    if changes:
        console.print("Workspace changes:")
        for line in changes:
            console.print(line)
    else:
        console.print("[green]Workspace clean.[/green]")
