"""Command line interface for Orcha."""

import typer

app = typer.Typer(help="Agent orchestration tool.")


@app.command()
def main() -> None:
    """Run Orcha."""
    typer.echo("Hello from orcha!")
