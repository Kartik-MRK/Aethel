import json
import re
from pathlib import Path
from typing import Any, Dict, Literal, Tuple

import typer
from rich.console import Console

console = Console()
app = typer.Typer()

AETHEL_DIR = Path(".aethel")
OBJECTS_DIR = AETHEL_DIR / "objects"
REFS_HEADS_DIR = AETHEL_DIR / "refs" / "heads"
HEAD_FILE = AETHEL_DIR / "HEAD"
HEAD_PREFIX = "ref: "
COMMIT_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
BRANCH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")

ResolutionType = Literal["branch", "commit"]


class CheckoutError(Exception):
    """Raised when checkout fails."""


def ensure_repository() -> None:
    if not AETHEL_DIR.is_dir():
        raise CheckoutError("Not an Aethel repository. Run 'aethel init' first.")


def read_text_file(path: Path, failure_context: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        raise CheckoutError(f"{failure_context}: {e}") from e


def write_text_file(path: Path, content: str, failure_context: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        raise CheckoutError(f"{failure_context}: {e}") from e


def parse_commit_object(commit_hash: str) -> Dict[str, Any]:
    commit_path = OBJECTS_DIR / commit_hash

    if not commit_path.is_file():
        raise CheckoutError(f"Commit object '{commit_hash}' is missing from object store.")

    raw = read_text_file(commit_path, f"Failed to read commit object '{commit_hash}'")

    try:
        commit_obj = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CheckoutError(f"Commit object '{commit_hash}' is invalid JSON: {e}") from e

    if not isinstance(commit_obj, dict):
        raise CheckoutError(f"Commit object '{commit_hash}' is malformed.")

    adapter_blob = commit_obj.get("adapter_blob")
    if not isinstance(adapter_blob, str) or not COMMIT_HASH_PATTERN.fullmatch(adapter_blob):
        raise CheckoutError(f"Commit object '{commit_hash}' is missing a valid adapter_blob hash.")

    return commit_obj


def verify_blob_exists(commit_obj: Dict[str, Any]) -> None:
    blob_hash = commit_obj["adapter_blob"]
    blob_path = OBJECTS_DIR / blob_hash

    if not blob_path.is_file():
        raise CheckoutError("Fatal: The model weights for this commit are missing from the object store.")


def resolve_branch_target(target: str) -> Tuple[str, Dict[str, Any]]:
    if not BRANCH_NAME_PATTERN.fullmatch(target):
        raise CheckoutError("_NOT_BRANCH_")

    branch_ref = REFS_HEADS_DIR / target
    if not branch_ref.exists():
        raise CheckoutError("_NOT_BRANCH_")

    if not branch_ref.is_file():
        raise CheckoutError(f"Branch reference '{target}' is invalid.")

    commit_hash = read_text_file(branch_ref, f"Failed to read branch reference '{target}'")
    if not commit_hash:
        raise CheckoutError("Cannot checkout a branch with no commits. Please make your first commit.")

    if not COMMIT_HASH_PATTERN.fullmatch(commit_hash):
        raise CheckoutError(f"Branch '{target}' points to an invalid commit hash.")

    commit_obj = parse_commit_object(commit_hash.lower())
    verify_blob_exists(commit_obj)
    return commit_hash.lower(), commit_obj


def resolve_commit_target(target: str) -> Tuple[str, Dict[str, Any]]:
    if not COMMIT_HASH_PATTERN.fullmatch(target):
        raise CheckoutError("_NOT_COMMIT_")

    candidate = OBJECTS_DIR / target.lower()
    if not candidate.exists() or not candidate.is_file():
        raise CheckoutError("_NOT_COMMIT_")

    try:
        commit_obj = parse_commit_object(target.lower())
    except CheckoutError:
        raise CheckoutError("_NOT_COMMIT_")

    verify_blob_exists(commit_obj)
    return target.lower(), commit_obj


def resolve_target(target: str) -> Tuple[ResolutionType, str, Dict[str, Any]]:
    try:
        commit_hash, commit_obj = resolve_branch_target(target)
        return "branch", commit_hash, commit_obj
    except CheckoutError as e:
        if str(e) != "_NOT_BRANCH_":
            raise

    try:
        commit_hash, commit_obj = resolve_commit_target(target)
        return "commit", commit_hash, commit_obj
    except CheckoutError as e:
        if str(e) != "_NOT_COMMIT_":
            raise

    raise CheckoutError(f"Fatal: target '{target}' did not match any files or commits.")


def update_head_for_branch(branch_name: str) -> None:
    head_value = f"{HEAD_PREFIX}refs/heads/{branch_name}\n"
    write_text_file(HEAD_FILE, head_value, "Failed to update HEAD")


def update_head_detached(commit_hash: str) -> None:
    write_text_file(HEAD_FILE, f"{commit_hash}\n", "Failed to update HEAD")


@app.callback(invoke_without_command=True)
def checkout_callback(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Branch name or commit hash to checkout."),
):
    """
    Move HEAD to a branch ref or detached commit hash.
    """
    if ctx.invoked_subcommand is None:
        checkout_target(target)


def checkout_target(target: str) -> None:
    """
    Checkout a branch or commit (detached HEAD) after object-store verification.
    """
    try:
        ensure_repository()
        resolution, commit_hash, _ = resolve_target(target)

        if resolution == "branch":
            update_head_for_branch(target)
            console.print(f"[bold green]Switched to branch {target}.[/bold green]")
            console.print(f"[cyan]HEAD now points to commit:[/cyan] {commit_hash}")
        else:
            update_head_detached(commit_hash)
            console.print(f"[bold green]HEAD is now detached at {commit_hash}.[/bold green]")
    except CheckoutError as e:
        console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(code=1)
