"""Shared CLI helpers: error rendering and common option handling.

The core raises `AethelError` and never prints. This module is the single
boundary where those errors become terminal output and exit codes, so every
command renders failures the same way and no command grows its own ad-hoc
`console.print(...)` + `raise typer.Exit` pair.
"""

import functools
from collections.abc import Callable

import typer
from rich.console import Console

from aethel.core.errors import AethelError, DetachedHead, RepositoryNotFound

console = Console()
err_console = Console(stderr=True)

#: Context settings for a command that takes a positional argument.
#:
#: Every command here is registered with ``add_typer`` and so is a Click group,
#: and a group stops parsing options at the first positional. Without this,
#: ``aethel checkout main --force`` fails with "Missing argument 'TARGET'" while
#: ``aethel checkout --force main`` works -- and nobody types them in that order
#: under pressure. Applies to ``checkout`` and ``branch``; the option-only
#: commands are unaffected either way.
INTERSPERSED = {"allow_interspersed_args": True}


def render_detached_head(exc: DetachedHead, attempted: str) -> None:
    """Explain detached HEAD and how to recover.

    Preserved from the original implementation (commands/commit.py:315-329),
    which blocked the commit rather than warning -- stricter than Git, and the
    right call: a commit made here is referenced by nothing and the next
    checkout orphans it.
    """
    err_console.print("[bold yellow]Detached HEAD — commit blocked.[/bold yellow]")
    err_console.print()
    err_console.print("You are not on any branch.")
    err_console.print(f"HEAD points directly at commit [cyan]{exc.commit_hash[:12]}[/cyan]")
    err_console.print()
    err_console.print("A commit made here would be referenced by nothing and lost")
    err_console.print("as soon as you check out another branch.")
    err_console.print()
    err_console.print("[bold]To keep this work, put a branch here first:[/bold]")
    err_console.print("  [green]aethel branch <new-branch>[/green]")
    err_console.print("  [green]aethel checkout <new-branch>[/green]")
    err_console.print(f"  [green]aethel commit -m \"{attempted}\"[/green]")


def handle_errors(func: Callable) -> Callable:
    """Turn AethelError into a clean message and exit code 1.

    Anything that is not an AethelError propagates with its traceback intact:
    an unexpected exception is a bug in Aethel, and swallowing it into a tidy
    one-liner is how bugs get hidden. The original code caught broad
    `Exception` in places and did exactly that.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except RepositoryNotFound as exc:
            err_console.print(f"[bold red]{exc}[/bold red]")
            raise typer.Exit(code=1) from exc
        except AethelError as exc:
            err_console.print(f"[bold red]{type(exc).__name__}: {exc}[/bold red]")
            raise typer.Exit(code=1) from exc

    return wrapper


def short(commit_hash: str, length: int = 12) -> str:
    """Abbreviate a hash for display."""
    return commit_hash[:length]
