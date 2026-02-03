import typer
import os
import sqlite3
import json
from rich.console import Console

console = Console()
app = typer.Typer()

AETHEL_DIR = ".aethel"
DB_NAME = "repo.db"
CONFIG_NAME = "config.json"

@app.callback(invoke_without_command=True)
def init_callback(ctx: typer.Context):
    """
    Initialize a new Aethel-Git repository.
    """
    if ctx.invoked_subcommand is None:
        init()

@app.command()
def init():
    """
    Initialize a new Aethel-Git repository in the current directory.
    """
    cwd = os.getcwd()
    aethel_path = os.path.join(cwd, AETHEL_DIR)
    
    if os.path.exists(aethel_path):
        console.print(f"[bold yellow]Re-initializing existing repository in {aethel_path}[/bold yellow]")
    else:
        os.makedirs(aethel_path)
        os.makedirs(os.path.join(aethel_path, "objects")) # For storing adapters/metadata
        os.makedirs(os.path.join(aethel_path, "refs", "heads")) # For branches
        console.print(f"[bold green]Initialized empty Aethel-Git repository in {aethel_path}[/bold green]")

    # Setup Database
    db_path = os.path.join(aethel_path, DB_NAME)
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Create Commits Table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS commits (
        hash TEXT PRIMARY KEY,
        parent_hash TEXT,
        message TEXT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        author TEXT,
        metadata_cid TEXT,
        adapter_cid TEXT
    )
    ''')
    conn.commit()
    conn.close()
    
    # Create Config
    config_path = os.path.join(aethel_path, CONFIG_NAME)
    if not os.path.exists(config_path):
        default_config = {
            "model_id": "TinyLlama/TinyLlama-1.1B-Chat-v1.0",
            "author": "User"
        }
        with open(config_path, "w") as f:
            json.dump(default_config, f, indent=4)
    
    console.print("[green]Ready to track models.[/green]")
