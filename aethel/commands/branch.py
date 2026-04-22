import re
from pathlib import Path

import typer
from rich.console import Console

console = Console()
app = typer.Typer()

AETHEL_DIR = Path(".aethel")
HEAD_FILE = AETHEL_DIR / "HEAD"
REFS_HEADS_DIR = AETHEL_DIR / "refs" / "heads"
HEAD_PREFIX = "ref: "
COMMIT_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
BRANCH_NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class BranchError(Exception):
    """Raised when branch operations fail."""


def ensure_repository() -> None:
    if not AETHEL_DIR.is_dir():
        raise BranchError("Not an Aethel repository. Run 'aethel init' first.")


def validate_branch_name(name: str) -> str:
    branch_name = name.strip()
    if not branch_name:
        raise BranchError("Branch name cannot be empty.")

    if branch_name in {".", ".."}:
        raise BranchError("Invalid branch name.")

    if "/" in branch_name or "\\" in branch_name:
        raise BranchError("Invalid branch name. Use letters, digits, '.', '_', and '-'.")

    if " " in branch_name:
        raise BranchError("Branch name cannot contain spaces.")

    if not BRANCH_NAME_PATTERN.fullmatch(branch_name):
        raise BranchError("Invalid branch name. Use letters, digits, '.', '_', and '-'.")

    if branch_name.endswith(".lock"):
        raise BranchError("Invalid branch name.")

    return branch_name


def resolve_current_commit_hash() -> str:
    """Return the commit hash that HEAD currently points to.

    Works for both attached HEAD (symbolic ref → branch → commit)
    and detached HEAD (raw commit hash directly in HEAD file).
    """
    if not HEAD_FILE.is_file():
        raise BranchError("HEAD is missing. Re-run 'aethel init --model <repo>'.")

    try:
        head_content = HEAD_FILE.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise BranchError(f"Failed to read HEAD: {e}") from e

    # --- Detached HEAD: HEAD contains a raw commit hash ---
    if COMMIT_HASH_PATTERN.fullmatch(head_content):
        return head_content.lower()

    # --- Attached HEAD: HEAD is a symbolic ref ---
    if not head_content.startswith(HEAD_PREFIX):
        raise BranchError(
            "HEAD is invalid. Expected 'ref: refs/heads/<name>' or a valid commit hash."
        )

    ref_rel = head_content[len(HEAD_PREFIX):].strip()
    if not ref_rel:
        raise BranchError("HEAD reference is empty.")

    ref_path = AETHEL_DIR / Path(ref_rel)

    try:
        repo_root = AETHEL_DIR.resolve()
        ref_resolved = ref_path.resolve()
    except OSError as e:
        raise BranchError(f"Failed to resolve HEAD reference: {e}") from e

    if not str(ref_resolved).startswith(str(repo_root)):
        raise BranchError("HEAD reference points outside repository metadata directory.")

    if not ref_path.exists() or not ref_path.is_file():
        raise BranchError(
            "Cannot create a branch from an empty repository. Please make your first commit."
        )

    try:
        commit_hash = ref_path.read_text(encoding="utf-8").strip()
    except OSError as e:
        raise BranchError(f"Failed to read active branch reference: {e}") from e

    if not commit_hash:
        raise BranchError(
            "Cannot create a branch from an empty repository. Please make your first commit."
        )

    if not COMMIT_HASH_PATTERN.fullmatch(commit_hash):
        raise BranchError(
            "Active branch reference does not contain a valid 64-character commit hash."
        )

    return commit_hash.lower()


def create_branch_ref_file(branch_name: str, commit_hash: str) -> None:
    branch_ref_path = REFS_HEADS_DIR / branch_name

    if branch_ref_path.exists():
        raise BranchError(f"Fatal: A branch named '{branch_name}' already exists.")

    try:
        branch_ref_path.parent.mkdir(parents=True, exist_ok=True)
        branch_ref_path.write_text(f"{commit_hash}\n", encoding="utf-8")
    except OSError as e:
        raise BranchError(f"Failed to create branch '{branch_name}': {e}") from e


@app.callback(invoke_without_command=True)
def branch_callback(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Name of the new branch to create."),
):
    """
    Create a lightweight branch reference at the current commit.
    """
    if ctx.invoked_subcommand is None:
        create_branch(name)


def create_branch(name: str) -> None:
    """
    Create a new branch from the current HEAD position without changing HEAD.
    Works in both attached (on a branch) and detached HEAD state.
    """
    try:
        ensure_repository()
        valid_name = validate_branch_name(name)
        active_commit_hash = resolve_current_commit_hash()
        create_branch_ref_file(valid_name, active_commit_hash)
    except BranchError as e:
        console.print(f"[bold red]{e}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"[bold green]Created branch '{valid_name}'.[/bold green]")
    console.print(f"[cyan]Points to commit:[/cyan] {active_commit_hash}")

