import typer
import os
import sqlite3
import json
from typing import Optional
from huggingface_hub import model_info
from rich.console import Console
from rich.prompt import Prompt

console = Console()
app = typer.Typer()

AETHEL_DIR = ".aethel"
DB_NAME = "repo.db"
CONFIG_NAME = "config.json"
WORKSPACE_DIR_NAME = "workspace"


class InitError(Exception):
    """Raised when repository initialization fails."""


def resolve_model_id(input_model: Optional[str]) -> str:
    """Resolve model id from CLI option or interactive prompt."""
    if input_model and input_model.strip():
        return input_model.strip()

    model_id = Prompt.ask("Enter Hugging Face model repository (owner/model)").strip()
    if not model_id:
        raise InitError("Model repository cannot be empty.")
    return model_id


def fetch_model_revision_hash(model_id: str) -> str:
    """Fetch the latest immutable revision hash for a Hugging Face model."""
    try:
        info = model_info(model_id)
    except Exception as e:
        raise InitError(f"Failed to fetch model info for '{model_id}': {e}") from e

    revision_hash = getattr(info, "sha", None)
    if not revision_hash:
        raise InitError(f"Could not resolve revision hash for model '{model_id}'.")
    return revision_hash


def ensure_repo_layout(aethel_path: str) -> None:
    """Create required repository directories."""
    os.makedirs(aethel_path, exist_ok=True)
    os.makedirs(os.path.join(aethel_path, "objects"), exist_ok=True)
    os.makedirs(os.path.join(aethel_path, "refs", "heads"), exist_ok=True)
    os.makedirs(os.path.join(aethel_path, WORKSPACE_DIR_NAME), exist_ok=True)


def setup_database(db_path: str) -> None:
    """Initialize repository metadata database."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        '''
        CREATE TABLE IF NOT EXISTS commits (
            hash TEXT PRIMARY KEY,
            parent_hash TEXT,
            message TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            author TEXT,
            metadata_cid TEXT,
            adapter_cid TEXT
        )
        '''
    )
    conn.commit()
    conn.close()


def load_existing_author(config_path: str) -> str:
    """Preserve prior author identity when re-initializing."""
    if not os.path.exists(config_path):
        return os.getenv("USERNAME", "User")

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError):
        return os.getenv("USERNAME", "User")

    return config.get("author") or os.getenv("USERNAME", "User")

@app.callback(invoke_without_command=True)
def init_callback(
    ctx: typer.Context,
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Hugging Face model repository id (owner/model).",
    ),
):
    """
    Initialize a new Aethel-Git repository.
    """
    if ctx.invoked_subcommand is None:
        init(model=model)

@app.command()
def init(
    model: Optional[str] = typer.Option(
        None,
        "--model",
        help="Hugging Face model repository id (owner/model).",
    )
):
    """
    Initialize a new Aethel-Git repository in the current directory.
    """
    cwd = os.getcwd()
    aethel_path = os.path.join(cwd, AETHEL_DIR)

    try:
        model_id = resolve_model_id(model)
        revision_hash = fetch_model_revision_hash(model_id)

        if os.path.exists(aethel_path):
            console.print(f"[bold yellow]Re-initializing existing repository in {aethel_path}[/bold yellow]")
        else:
            console.print(f"[bold green]Initialized empty Aethel-Git repository in {aethel_path}[/bold green]")

        ensure_repo_layout(aethel_path)
        setup_database(os.path.join(aethel_path, DB_NAME))

        config_path = os.path.join(aethel_path, CONFIG_NAME)
        author = load_existing_author(config_path)
        config = {
            "model_id": model_id,
            "revision_hash": revision_hash,
            "author": author,
        }

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=4)

        console.print("[green]Ready to track models.[/green]")
        console.print(f"[cyan]Pinned model:[/cyan] {model_id}")
        console.print(f"[cyan]Revision hash:[/cyan] {revision_hash}")

    except InitError as e:
        console.print(f"[bold red]Initialization failed: {e}[/bold red]")
        raise typer.Exit(code=1)
    except OSError as e:
        console.print(f"[bold red]Initialization failed due to file system error: {e}[/bold red]")
        raise typer.Exit(code=1)
