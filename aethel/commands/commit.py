import typer
import os
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


class CommitStorageError(Exception):
    """Raised when commit object storage operations fail."""


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


def get_last_commit_hash() -> Optional[str]:
    db_path = os.path.join(AETHEL_DIR, DB_NAME)
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT hash FROM commits ORDER BY timestamp DESC LIMIT 1")
        result = cursor.fetchone()
    except sqlite3.Error as e:
        raise CommitStorageError(f"Failed to query commit history: {e}") from e
    finally:
        try:
            conn.close()
        except Exception:
            pass
    return result[0] if result else None


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


def clear_workspace() -> None:
    """Clear workspace after successful commit while keeping directory present."""
    try:
        if os.path.exists(WORKSPACE_DIR):
            shutil.rmtree(WORKSPACE_DIR)
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
    except OSError as e:
        raise CommitStorageError(f"Failed to clear workspace: {e}") from e


def _relocate_legacy_blob_directory(blob_path: str, blob_hash: str) -> None:
    """Relocate old folder-based object layout when hash path is a directory."""
    legacy_root = os.path.join(OBJECTS_DIR, "_legacy_dirs")
    try:
        os.makedirs(legacy_root, exist_ok=True)
    except OSError as e:
        raise CommitStorageError(f"Failed to prepare legacy backup location: {e}") from e

    candidate = os.path.join(legacy_root, blob_hash)
    suffix = 1
    while os.path.exists(candidate):
        candidate = os.path.join(legacy_root, f"{blob_hash}_{suffix}")
        suffix += 1

    try:
        shutil.move(blob_path, candidate)
    except OSError as e:
        raise CommitStorageError(f"Failed to relocate legacy object directory: {e}") from e

    console.print(
        "[yellow]Detected legacy folder object layout. "
        f"Moved old directory to {candidate}.[/yellow]"
    )


def store_blob_object(source_path: str, blob_hash: str) -> str:
    """Store adapter blob as a flat object file and deduplicate by hash."""
    blob_path = os.path.join(OBJECTS_DIR, blob_hash)

    if os.path.isdir(blob_path):
        _relocate_legacy_blob_directory(blob_path, blob_hash)

    if os.path.isfile(blob_path):
        console.print("[yellow]Blob already exists, deduplicating.[/yellow]")
        return blob_path

    try:
        shutil.copy2(source_path, blob_path)
    except OSError as e:
        raise CommitStorageError(f"Failed to persist blob object: {e}") from e

    return blob_path


def save_commit_object(commit_hash: str, commit_json: str) -> str:
    """Store commit metadata as a flat object file keyed by commit hash."""
    commit_path = os.path.join(OBJECTS_DIR, commit_hash)

    if os.path.isdir(commit_path):
        raise CommitStorageError(
            f"Cannot store commit object. A directory already exists at {commit_path}."
        )

    if os.path.isfile(commit_path):
        try:
            with open(commit_path, "r", encoding="utf-8") as existing_file:
                existing = existing_file.read()
        except OSError as e:
            raise CommitStorageError(f"Failed to read existing commit object: {e}") from e

        if existing != commit_json:
            raise CommitStorageError(
                f"Commit hash collision detected for {commit_hash}. Existing content differs."
            )
        return commit_path

    try:
        with open(commit_path, "w", encoding="utf-8") as f:
            f.write(commit_json)
    except OSError as e:
        raise CommitStorageError(f"Failed to save commit object: {e}") from e

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
        adapter_file = get_workspace_adapter_path()
        training_info = load_workspace_training_info()

        # 1. Blob object: hash and deduplicate flat file storage
        blob_hash = calculate_file_hash(adapter_file)
        store_blob_object(adapter_file, blob_hash)

        # 2. Commit object: hash metadata and store as flat file
        parent_hash = get_last_commit_hash()
        metadata = {
            "adapter_blob": blob_hash,
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
        save_commit_object(commit_hash, metadata_json)

        # 3. Update commit index database
        insert_commit_record(commit_hash, metadata)
        clear_workspace()

    except CommitStorageError as e:
        console.print(f"[bold red]Commit failed: {e}[/bold red]")
        raise typer.Exit(code=1)

    console.print(f"[bold green]Commit successful![/bold green]")
    console.print(f"Commit: [white]{commit_hash}[/white]")
    console.print(f"Blob: [cyan]{blob_hash}[/cyan]")
