"""Build and load the regulatory-affairs graph into Samyama."""
import click

@click.command()
@click.option("--limit", type=int, default=None, help="Cap rows per source for a fast demo load.")
def main(limit):
    # TODO: connect -> run schema/regulatory_affairs_kg.cypher -> load nodes/edges
    print(f"[loader] loading regulatory-affairs KG (limit={limit}) ...")

if __name__ == "__main__":
    main()
