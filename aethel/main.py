import typer
from aethel.commands import init, commit, status

app = typer.Typer(
    help="Aethel-Git: Decentralized Version Control for AI Models",
    add_completion=False
)

app.add_typer(init.app, name="init")
app.add_typer(commit.app, name="commit")
app.add_typer(status.app, name="status")

# Alias log command to top level
@app.command()
def log():
    """
    Display the commit history.
    """
    status.log()
