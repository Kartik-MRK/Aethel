import typer
import sqlite3
import os
from rich.console import Console
from rich.table import Table

console = Console()
app = typer.Typer()

AETHEL_DIR = ".aethel"
DB_NAME = "repo.db"

@app.callback(invoke_without_command=True)
def default_status(ctx: typer.Context):
    """
    Shows the commit logs.
    """
    if ctx.invoked_subcommand is None:
        log()

@app.command()
def log():
    """
    Display the commit history.
    """
    if not os.path.exists(AETHEL_DIR):
        console.print("[bold red]Not an Aethel repository.[/bold red]")
        raise typer.Exit(code=1)

    conn = sqlite3.connect(os.path.join(AETHEL_DIR, DB_NAME))
    cursor = conn.cursor()
    
    cursor.execute("SELECT hash, timestamp, author, message FROM commits ORDER BY timestamp DESC")
    rows = cursor.fetchall()
    conn.close()
    
    if not rows:
        console.print("[yellow]No commits found.[/yellow]")
        return
        
    table = Table(title="Commit History")
    table.add_column("Hash", style="cyan", no_wrap=True)
    table.add_column("Timestamp", style="magenta")
    table.add_column("Author", style="green")
    table.add_column("Message", style="white")
    
    for row in rows:
        table.add_row(row[0], row[1], row[2], row[3])
        
    console.print(table)
