"""CLI entry point for the EDH Collection Deck Builder.

Provides commands to build decks, suggest commanders, and estimate brackets.
"""

import typer

app = typer.Typer(
    name="edh-builder",
    help="Build 4 balanced Commander (EDH) decks from your card collection.",
)


@app.command()
def build(
    collection: str = typer.Option(..., help="Path to ManaBox CSV export"),
    commanders: list[str] = typer.Option(
        default_factory=list,
        help="Commander name(s) to use (1-4). Tool suggests the rest.",
    ),
) -> None:
    """Build 4 balanced Commander decks from your collection."""
    typer.echo(f"Building decks from {collection}")
    typer.echo(f"Commanders: {commanders if commanders else '(will suggest all 4)'}")
    typer.echo("Not yet implemented.")
    raise typer.Exit(code=1)


@app.command()
def suggest(
    collection: str = typer.Option(..., help="Path to ManaBox CSV export"),
) -> None:
    """Suggest commanders based on your collection."""
    typer.echo(f"Suggesting commanders from {collection}")
    typer.echo("Not yet implemented.")
    raise typer.Exit(code=1)


@app.command(name="estimate-bracket")
def estimate_bracket(
    decklist: str = typer.Option(..., help="Path to a decklist file"),
) -> None:
    """Estimate the Commander Bracket for a decklist."""
    typer.echo(f"Estimating bracket for {decklist}")
    typer.echo("Not yet implemented.")
    raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
