import typer
from aethel.commands import init, commit, status, train, branch, checkout

app = typer.Typer(
    help="Aethel-Git: Decentralized Version Control for AI Models",
    add_completion=False
)

app.add_typer(init.app, name="init")
app.add_typer(commit.app, name="commit")
app.add_typer(status.app, name="status")
app.add_typer(train.app, name="train")
app.add_typer(branch.app, name="branch")
app.add_typer(checkout.app, name="checkout")

# Alias log command to top level
@app.command()
def log():
    """
    Display the commit history.
    """
    status.log()
