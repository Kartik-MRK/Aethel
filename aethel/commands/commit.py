import typer
import os
import re
import sqlite3
import json
import hashlib
import shutil
from datetime import datetime
from typing import Optional
from rich.console import Console

console = Console()
app = typer.Typer()

AETHEL_DIR = ".aethel"
OBJECTS_DIR = os.path.join(AETHEL_DIR, "objects")
WORKSPACE_DIR = os.path.join(AETHEL_DIR, "workspace")
DB_NAME = "repo.db"
CONFIG_NAME = "config.json"
TRAINING_INFO_FILE = "training_info.json"
HEAD_FILE = os.path.join(AETHEL_DIR, "HEAD")
HEAD_REF_PREFIX = "ref: "
COMMIT_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


class CommitStorageError(Exception):
    """Raised when commit object storage operations fail."""


class DetachedHeadError(Exception):
    """Raised when HEAD is detached and a commit is attempted."""
    def __init__(self, commit_hash: str):
        self.commit_hash = commit_hash
        super().__init__(commit_hash)


def calculate_file_hash(file_path: str) -> str:
    """Calculate SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            while chunk := f.read(8192):
                sha256.update(chunk)
    except OSError as e:
        raise CommitStorageError(f"Failed to read file for hashing: {e}") from e
    return sha256.hexdigest()


def calculate_text_hash(content: str) -> str:
    """Calculate SHA256 hash of a UTF-8 string."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def load_repo_config() -> dict:
    config_path = os.path.join(AETHEL_DIR, CONFIG_NAME)
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError as e:
        raise CommitStorageError("Repository config not found. Run 'aethel init' first.") from e
    except json.JSONDecodeError as e:
        raise CommitStorageError(f"Repository config is invalid JSON: {e}") from e
    except OSError as e:
        raise CommitStorageError(f"Could not read repository config: {e}") from e


def resolve_active_branch_ref_path() -> str:
    """Resolve the current branch reference path from HEAD.

    Raises DetachedHeadError if HEAD is in detached state (i.e. points directly
    to a commit hash rather than a branch reference).
    """
    if not os.path.isfile(HEAD_FILE):
        raise CommitStorageError("HEAD is missing. Re-run 'aethel init --model <repo>'.")

    try:
        with open(HEAD_FILE, "r", encoding="utf-8") as f:
            head_content = f.read().strip()
    except OSError as e:
        raise CommitStorageError(f"Failed to read HEAD: {e}") from e

    # Detached HEAD: content is a raw commit hash, not a branch reference
    if not head_content.startswith(HEAD_REF_PREFIX):
        if COMMIT_HASH_PATTERN.fullmatch(head_content):
            raise DetachedHeadError(head_content)
        raise CommitStorageError(
            "HEAD is invalid. Expected 'ref: refs/heads/<branch>' or a valid commit hash."
        )

    ref_rel = head_content[len(HEAD_REF_PREFIX):].strip()
    if not ref_rel:
        raise CommitStorageError("HEAD reference is empty.")

    ref_abs = os.path.abspath(os.path.join(AETHEL_DIR, *ref_rel.split("/")))
    repo_abs = os.path.abspath(AETHEL_DIR)

    if not (ref_abs == repo_abs or ref_abs.startswith(repo_abs + os.sep)):
        raise CommitStorageError("HEAD reference points outside repository metadata directory.")

    if not os.path.exists(ref_abs):
        raise CommitStorageError("Active branch reference is missing. Re-run 'aethel init --model <repo>'.")

    if os.path.isdir(ref_abs):
        raise CommitStorageError("Active branch reference is invalid.")

    return ref_abs


def read_branch_tip_hash(branch_ref_path: str) -> Optional[str]:
    """Read branch tip hash from the active branch ref file."""
    try:
        with open(branch_ref_path, "r", encoding="utf-8") as f:
            value = f.read().strip()
    except OSError as e:
        raise CommitStorageError(f"Failed to read active branch reference: {e}") from e

    if not value:
        return None

    if not COMMIT_HASH_PATTERN.fullmatch(value):
        raise CommitStorageError("Active branch reference does not contain a valid commit hash.")

    return value.lower()


def update_branch_tip_hash(branch_ref_path: str, commit_hash: str) -> None:
    """Advance active branch reference to the new commit hash."""
    try:
        with open(branch_ref_path, "w", encoding="utf-8") as f:
            f.write(f"{commit_hash}\n")
    except OSError as e:
        raise CommitStorageError(f"Failed to update active branch reference: {e}") from e


def get_workspace_adapter_path() -> str:
    """Resolve adapter artifact path from workspace."""
    safetensors_path = os.path.join(WORKSPACE_DIR, "adapter_model.safetensors")
    bin_path = os.path.join(WORKSPACE_DIR, "adapter_model.bin")

    if os.path.isfile(safetensors_path):
        return safetensors_path
    if os.path.isfile(bin_path):
        return bin_path

    raise CommitStorageError(
        "No adapter_model.safetensors found in workspace. Run 'aethel train --config <yaml>' first."
    )


def load_workspace_training_info() -> dict:
    """Load training metadata emitted by the train command."""
    training_info_path = os.path.join(WORKSPACE_DIR, TRAINING_INFO_FILE)

    if not os.path.isfile(training_info_path):
        raise CommitStorageError(
            "training_info.json not found in workspace. Run 'aethel train --config <yaml>' first."
        )

    try:
        with open(training_info_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        raise CommitStorageError(f"training_info.json is invalid JSON: {e}") from e
    except OSError as e:
        raise CommitStorageError(f"Failed to read training_info.json: {e}") from e


def ensure_objects_dir() -> None:
    """Ensure the object store directory exists."""
    try:
        os.makedirs(OBJECTS_DIR, exist_ok=True)
    except OSError as e:
        raise CommitStorageError(f"Failed to initialize object store: {e}") from e


def restore_workspace_from_folder(folder_path: str) -> None:
    """After commit, repopulate workspace from the stored adapter folder.

    This keeps the workspace in sync with the current HEAD — the user can
    verify what was committed without running checkout again.
    Skips commit.json since that is metadata, not a model artifact.
    """
    try:
        if os.path.exists(WORKSPACE_DIR):
            shutil.rmtree(WORKSPACE_DIR)
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        for item in os.listdir(folder_path):
            if item == "commit.json":
                continue
            src = os.path.join(folder_path, item)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(WORKSPACE_DIR, item))
    except OSError as e:
        raise CommitStorageError(f"Failed to restore workspace after commit: {e}") from e


def store_adapter_folder(workspace_dir: str, adapter_hash: str) -> str:
    """Store all workspace adapter artifacts in a folder named by adapter hash."""
    folder_path = os.path.join(OBJECTS_DIR, adapter_hash)

    if os.path.isdir(folder_path):
        console.print("[yellow]Adapter folder already exists, deduplicating.[/yellow]")
        return folder_path

    # Remove stale flat file if one exists from a previous layout
    if os.path.isfile(folder_path):
        try:
            os.remove(folder_path)
        except OSError as e:
            raise CommitStorageError(f"Failed to remove stale flat object: {e}") from e

    try:
        os.makedirs(folder_path, exist_ok=True)
        for item in os.listdir(workspace_dir):
            src = os.path.join(workspace_dir, item)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(folder_path, item))
    except OSError as e:
        raise CommitStorageError(f"Failed to store adapter folder: {e}") from e

    return folder_path


def save_commit_metadata(folder_path: str, commit_json: str) -> str:
    """Write commit.json inside the adapter folder."""
    commit_path = os.path.join(folder_path, "commit.json")
    try:
        with open(commit_path, "w", encoding="utf-8") as f:
            f.write(commit_json)
    except OSError as e:
        raise CommitStorageError(f"Failed to save commit metadata: {e}") from e
    return commit_path


def insert_commit_record(commit_hash: str, metadata: dict) -> None:
    """Insert commit record into SQLite metadata database."""
    db_path = os.path.join(AETHEL_DIR, DB_NAME)

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            '''
            INSERT INTO commits (hash, parent_hash, message, author, metadata_cid, adapter_cid)
            VALUES (?, ?, ?, ?, ?, ?)
            ''',
            (
                commit_hash,
                metadata["parent_hash"],
                metadata["message"],
                metadata["author"],
                commit_hash,
                metadata["adapter_blob"],
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError as e:
        raise CommitStorageError(f"Commit already exists in database: {e}") from e
    except sqlite3.Error as e:
        raise CommitStorageError(f"Failed to write commit metadata to database: {e}") from e
    finally:
        try:
            conn.close()
        except Exception:
            pass

@app.callback(invoke_without_command=True)
def commit(ctx: typer.Context, message: str = typer.Option(..., "-m", "--message", help="Commit message")):
    """
    Create a pure VCS commit from workspace artifacts.
    """
    if not os.path.exists(AETHEL_DIR):
        console.print("[bold red]Not an Aethel repository. Run 'aethel init' first.[/bold red]")
        raise typer.Exit(code=1)

    try:
        ensure_objects_dir()
        config = load_repo_config()
        branch_ref_path = resolve_active_branch_ref_path()
        adapter_file = get_workspace_adapter_path()
        training_info = load_workspace_training_info()

        # 1. Hash the adapter weights file
        adapter_hash = calculate_file_hash(adapter_file)

        # 2. Store all workspace files in folder named by adapter hash
        folder_path = store_adapter_folder(WORKSPACE_DIR, adapter_hash)

        # 3. Build commit metadata
        parent_hash = read_branch_tip_hash(branch_ref_path)
        metadata = {
            "adapter_blob": adapter_hash,
            "author": config.get("author", "User"),
            "base_model": config.get("model_id"),
            "message": message,
            "parent_hash": parent_hash,
            "revision_hash": config.get("revision_hash"),
            "timestamp": datetime.now().isoformat(),
            "training_info": training_info,
        }

        metadata_json = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        commit_hash = calculate_text_hash(metadata_json)

        # 4. Write commit.json inside the adapter folder
        save_commit_metadata(folder_path, metadata_json)

        # 5. Update database and branch ref
        insert_commit_record(commit_hash, metadata)
        update_branch_tip_hash(branch_ref_path, commit_hash)

        # 6. Restore workspace from stored folder so it's never left empty
        restore_workspace_from_folder(folder_path)

    except DetachedHeadError as e:
        detached_hash = e.commit_hash
        console.print("[bold yellow]⚠  Detached HEAD — commit blocked.[/bold yellow]")
        console.print("")
        console.print("You are not on any branch.")
        console.print(f"HEAD points directly to commit: [cyan]{detached_hash[:16]}...[/cyan]")
        console.print("")
        console.print("Commits made in this state would be lost when you checkout another branch.")
        console.print("[bold]To save your work, create a new branch first:[/bold]")
        console.print("")
        console.print(f"  [green]aethel branch <new-branch-name>[/green]")
        console.print(f"  [green]aethel checkout <new-branch-name>[/green]")
        console.print(f"  [green]aethel commit -m \"{message}\"[/green]")
        console.print("")
        raise typer.Exit(code=1)

    except CommitStorageError as e:
        console.print(f"[bold red]Commit failed: {e}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"[bold green]✅ Commit successful![/bold green]")
    console.print(f"Commit:   [white]{commit_hash}[/white]")
    console.print(f"Adapter:  [cyan]{adapter_hash}[/cyan]")
    console.print(f"Stored:   [dim]{folder_path}[/dim]")
    console.print(f"Workspace: [green]restored ✓[/green]")
