"""CLI entry — implemented in step 6. Stub here so the console script registers."""
import click


@click.group()
@click.version_option(package_name="aafbrowser")
def cli():
    """aafbrowser — read-only inspection of AAF files."""


if __name__ == "__main__":
    cli()
