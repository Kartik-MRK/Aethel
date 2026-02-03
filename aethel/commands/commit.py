import typer
import os
import sqlite3
import json
import hashlib
from datetime import datetime
from rich.console import Console
from aethel.core.model import ModelManager

console = Console()
app = typer.Typer()

AETHEL_DIR = ".aethel"
OBJECTS_DIR = os.path.join(AETHEL_DIR, "objects")
DB_NAME = "repo.db"
CONFIG_NAME = "config.json"

def calculate_hash(file_path):
    """Calculates SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(8192):
            sha256.update(chunk)
    return sha256.hexdigest()

def get_last_commit_hash():
    conn = sqlite3.connect(os.path.join(AETHEL_DIR, DB_NAME))
    cursor = conn.cursor()
    cursor.execute("SELECT hash FROM commits ORDER BY timestamp DESC LIMIT 1")
    result = cursor.fetchone()
    conn.close()
    return result[0] if result else None

@app.callback(invoke_without_command=True)
def commit(ctx: typer.Context, message: str = typer.Option(..., "-m", "--message", help="Commit message")):
    """
    Simulates a training run, saves the LoRA adapter, hashes it, and records the commit.
    """
    if not os.path.exists(AETHEL_DIR):
        console.print("[bold red]Not an Aethel repository. Run 'aethel init' first.[/bold red]")
        raise typer.Exit(code=1)

    # 1. Load Config
    with open(os.path.join(AETHEL_DIR, CONFIG_NAME), "r") as f:
        config = json.load(f)
    
    # 2. Initialize Model & Simulate Training
    manager = ModelManager(model_id=config.get("model_id"))
    if not manager.load_base_model():
        raise typer.Exit(code=1)
    
    # Check if we have a parent commit to load from (TODO: Implement checkout logic later)
    # For now, we always start fresh or assume HEAD is loaded (simplified for Phase 2 PoC)
    manager.create_or_load_adapter()
    
    # Simulate work
    manager.train_dummy_step()
    
    # 3. Save Adapter to temporary location first
    temp_path = os.path.join(AETHEL_DIR, "temp_stage")
    manager.save_adapter(temp_path)
    
    # 4. Hash and Move Artifacts
    # We are interested in 'adapter_model.safetensors' primarily
    adapter_file = os.path.join(temp_path, "adapter_model.safetensors")
    if not os.path.exists(adapter_file):
        # Fallback for bin
        adapter_file = os.path.join(temp_path, "adapter_model.bin")
        
    adapter_hash = calculate_hash(adapter_file)
    console.print(f"[cyan]Adapter Hash: {adapter_hash}[/cyan]")
    
    # Move to permanent objects storage (Content Addressable Storage style)
    # We store it in a folder named after the hash to avoid collisions
    storage_path = os.path.join(OBJECTS_DIR, adapter_hash)
    if not os.path.exists(storage_path):
        os.rename(temp_path, storage_path)
    else:
        console.print("[yellow]Content already exists, deduplicating...[/yellow]")
        # Still need to clean up temp
        import shutil
        shutil.rmtree(temp_path)

    # 5. Create Metadata (The Commit Object)
    parent_hash = get_last_commit_hash()
    metadata = {
        "author": config.get("author"),
        "message": message,
        "parent": parent_hash,
        "timestamp": datetime.now().isoformat(),
        "adapter_content_hash": adapter_hash,
        "base_model": config.get("model_id")
    }
    
    # Hash the metadata itself to get the Commit Hash
    metadata_json = json.dumps(metadata, sort_keys=True)
    commit_hash = hashlib.sha256(metadata_json.encode()).hexdigest()
    
    # Save metadata
    with open(os.path.join(storage_path, "commit.json"), "w") as f:
        f.write(metadata_json)
        
    # 6. Update Database
    conn = sqlite3.connect(os.path.join(AETHEL_DIR, DB_NAME))
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO commits (hash, parent_hash, message, author, metadata_cid, adapter_cid)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (commit_hash, parent_hash, message, metadata["author"], "IPFS_TODO", adapter_hash))
    conn.commit()
    conn.close()
    
    console.print(f"[bold green]Commit successful![/bold green]")
    console.print(f"Hash: [white]{commit_hash}[/white]")
