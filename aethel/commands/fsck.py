"""``aethel fsck`` -- verify repository integrity.

Every object is named by the SHA-256 of its own content, so verification is
exact: re-hash the file, compare to its name. A mismatch is corruption or
tampering, never a stale cache.

Three classes of problem are reported separately, because they mean very
different things:

  corrupt    an object's content no longer matches its name -- real damage
  missing    something references an object that is not in the store
  unreachable  objects no ref can reach -- harmless, usually an interrupted
             commit; reported so `gc` has something to act on later
"""

import typer

from aethel.commands._common import console, err_console, handle_errors, short
from aethel.core.errors import AethelError
from aethel.core.objects import OBJECT_KINDS
from aethel.core.repo import Repo

app = typer.Typer()


@app.callback(invoke_without_command=True)
def fsck_callback(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="List every object checked."),
):
    """Verify that every stored object matches its content hash."""
    if ctx.invoked_subcommand is None:
        run_fsck(verbose=verbose)


@handle_errors
def run_fsck(verbose: bool) -> None:
    repo = Repo.discover()

    corrupt: list[tuple[str, str]] = []
    missing: list[str] = []
    counts: dict[str, int] = {}

    # 1. Every object hashes to its own name.
    for kind in OBJECT_KINDS:
        hashes = list(repo.objects.iter_hashes(kind))
        counts[kind] = len(hashes)

        for object_hash in hashes:
            if repo.objects.verify(kind, object_hash):
                if verbose:
                    console.print(f"[dim]ok   {kind[:-1]:7} {short(object_hash)}[/dim]")
            else:
                corrupt.append((kind, object_hash))

    # 2. Every reference resolves, and every referenced object exists.
    reachable: set[str] = set()

    for branch in repo.refs.list_branches():
        tip = repo.refs.read_branch(branch)
        if tip is None:
            continue

        try:
            from aethel.core.commits import walk_history

            for commit_hash, commit in walk_history(repo, tip):
                reachable.add(commit_hash)

                tree_hash = commit.get("tree")
                if tree_hash and not repo.objects.exists("trees", tree_hash):
                    missing.append(f"tree {short(tree_hash)} (commit {short(commit_hash)})")
                    continue

                reachable.add(tree_hash)
                for name, blob_hash in repo.objects.read_tree(tree_hash).items():
                    if repo.objects.exists("blobs", blob_hash):
                        reachable.add(blob_hash)
                    else:
                        missing.append(f"blob {short(blob_hash)} ({name})")

                base_hash = commit.get("base")
                if base_hash:
                    if repo.objects.exists("bases", base_hash):
                        reachable.add(base_hash)
                    else:
                        missing.append(f"base {short(base_hash)} (commit {short(commit_hash)})")
        except AethelError as exc:
            missing.append(f"branch '{branch}': {exc}")

    total = sum(counts.values())
    unreachable = sum(
        1
        for kind in OBJECT_KINDS
        for object_hash in repo.objects.iter_hashes(kind)
        if object_hash not in reachable
    )

    console.print(
        f"Checked [bold]{total}[/bold] objects "
        f"({counts['commits']} commits, {counts['trees']} trees, "
        f"{counts['blobs']} blobs, {counts['bases']} bases)"
    )

    if corrupt:
        err_console.print()
        err_console.print(f"[bold red]{len(corrupt)} CORRUPT object(s):[/bold red]")
        for kind, object_hash in corrupt:
            err_console.print(
                f"  [red]{kind[:-1]}[/red] {object_hash}\n"
                f"    {repo.objects.path_for(kind, object_hash)}"
            )

    if missing:
        err_console.print()
        err_console.print(f"[bold red]{len(missing)} MISSING object(s):[/bold red]")
        for item in missing:
            err_console.print(f"  [red]{item}[/red]")

    if unreachable:
        console.print(
            f"[yellow]{unreachable} unreachable object(s)[/yellow] "
            f"[dim]— not referenced by any branch; harmless (interrupted commits)[/dim]"
        )

    if corrupt or missing:
        err_console.print()
        err_console.print("[bold red]Repository integrity check FAILED.[/bold red]")
        raise typer.Exit(code=1)

    console.print("[bold green]Repository integrity OK.[/bold green]")
