"""``aethel init`` -- create a repository pinned to a base model revision.

Base model weights are never stored. The repository records
``(model_id, revision_sha)`` and anyone reproducing a result downloads the
base from Hugging Face themselves. Pinning the immutable revision SHA rather
than a tag is what makes that reproducible: tags move, SHAs do not.

The revision-resolution logic here is carried over from the original
implementation (commands/init.py:37-47), which got this right.
"""

import os

import typer
from rich.prompt import Prompt

from aethel.commands._common import console, handle_errors
from aethel.core.commits import build_base_object
from aethel.core.errors import AethelError
from aethel.core.repo import Repo

app = typer.Typer()


class InitError(AethelError):
    """Raised when repository initialization fails."""


def resolve_model_id(candidate: str | None) -> str:
    """Take the model id from the flag, or prompt for it."""
    if candidate and candidate.strip():
        return candidate.strip()

    entered = Prompt.ask("Hugging Face model repository (owner/model)").strip()
    if not entered:
        raise InitError("Model repository cannot be empty.")
    return entered


def fetch_model_revision_sha(model_id: str) -> str:
    """Resolve a model's current immutable revision SHA from the Hub.

    Imported lazily so `aethel --help` and every offline command work without
    huggingface_hub installed or a network connection.
    """
    try:
        from huggingface_hub import model_info
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise InitError(
            "huggingface_hub is required to pin a model revision. "
            "Install it with: pip install 'aethel[ml]'"
        ) from exc

    try:
        info = model_info(model_id)
    except Exception as exc:
        raise InitError(f"Could not fetch model info for '{model_id}': {exc}") from exc

    revision_sha = getattr(info, "sha", None)
    if not revision_sha:
        raise InitError(f"Could not resolve a revision SHA for '{model_id}'.")

    return revision_sha


def default_author() -> str:
    """Best-effort author identity from the environment.

    Checks USER before USERNAME. The original code checked only USERNAME
    (init.py:117,123), a Windows-only variable, so on Linux every commit was
    authored by the literal string "User" (defect S3).
    """
    for variable in ("AETHEL_AUTHOR", "USER", "USERNAME", "LOGNAME"):
        value = os.environ.get(variable)
        if value and value.strip():
            return value.strip()
    return "unknown"


@app.callback(invoke_without_command=True)
def init_callback(
    ctx: typer.Context,
    model: str | None = typer.Option(
        None, "--model", "-m", help="Hugging Face model repository id (owner/model)."
    ),
    author: str | None = typer.Option(
        None, "--author", help="Author recorded on commits. Defaults to $USER."
    ),
):
    """Initialize an Aethel repository in the current directory."""
    if ctx.invoked_subcommand is None:
        run_init(model=model, author=author)


@handle_errors
def run_init(model: str | None, author: str | None) -> None:
    root = os.getcwd()
    existing = Repo(root)
    reinitializing = existing.exists()

    # Preserve a previously configured author across re-init.
    previous_author = None
    if reinitializing:
        try:
            previous_author = existing.read_config().get("author")
        except AethelError:
            previous_author = None

    model_id = resolve_model_id(model)
    revision_sha = fetch_model_revision_sha(model_id)
    resolved_author = author or previous_author or default_author()

    repo = Repo.create(
        root,
        {
            "model_id": model_id,
            "revision_sha": revision_sha,
            "author": resolved_author,
        },
    )

    # Store the base reference as an object immediately, so the very first
    # commit has something to point at and `aethel base` can verify it.
    base_hash = repo.objects.write_json("bases", build_base_object(model_id, revision_sha))

    verb = "Reinitialized" if reinitializing else "Initialized"
    console.print(f"[bold green]{verb} Aethel repository in {repo.aethel_dir}[/bold green]")
    console.print(f"[cyan]Base model:[/cyan] {model_id}")
    console.print(f"[cyan]Revision:  [/cyan] {revision_sha}")
    console.print(f"[cyan]Base ref:  [/cyan] {base_hash[:12]}")
    console.print(f"[cyan]Author:    [/cyan] {resolved_author}")
    console.print()
    console.print("[dim]Base weights are not stored — only the pinned reference.[/dim]")
