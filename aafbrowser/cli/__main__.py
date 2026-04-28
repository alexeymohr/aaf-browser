"""
aafbrowser CLI — five Click commands consuming aafbrowser.core.

Each command takes exactly one positional argument: a path to an AAF file.
All commands are read-only — pyaaf2 is opened with mode 'r' and inputs are
never written back.

Spec: docs/phase1-brief.md "CLI command spec".
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Optional

import aaf2
import click

from aafbrowser.core import aaf as aaf_walker
from aafbrowser.core import cfb as cfb_walker
from aafbrowser.core import resolver as resolver_mod
from aafbrowser.core.serialize import DEFAULT_BYTES_PREVIEW_LIMIT


def _file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _open_readonly(path: str):
    """Open AAF in read-only mode and refuse to proceed if it isn't."""
    f = aaf2.open(path, "r")
    if f.writeable or f.mode != "rb":
        f.close()
        raise click.ClickException(
            f"refusing to proceed: file opened with mode={f.mode!r} writeable={f.writeable!r}"
        )
    return f


@click.group()
@click.version_option(package_name="aafbrowser")
def cli():
    """aafbrowser - read-only inspection of AAF files."""


# --- tree ------------------------------------------------------------------


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
def tree(aaf_path: str) -> None:
    """Pretty indented tree of the AAF object graph rooted at f.content."""
    with _open_readonly(aaf_path) as f:
        for line in aaf_walker.walk_human(f.content):
            click.echo(line)


# --- dump ------------------------------------------------------------------


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--object-only", is_flag=True, help="Skip CFB layer.")
@click.option("--cfb-only", is_flag=True, help="Skip AAF object layer.")
@click.option(
    "--full-bytes",
    is_flag=True,
    help="Include full base64 of binary stream payloads (default truncates to 1KB).",
)
def dump(
    aaf_path: str,
    as_json: bool,
    object_only: bool,
    cfb_only: bool,
    full_bytes: bool,
) -> None:
    """Full structural dump of both layers."""
    if object_only and cfb_only:
        raise click.UsageError("--object-only and --cfb-only are mutually exclusive")

    sha = _file_sha256(aaf_path)
    with _open_readonly(aaf_path) as f:
        bytes_limit = DEFAULT_BYTES_PREVIEW_LIMIT
        result = {
            "file": str(Path(aaf_path).resolve()),
            "sha256": sha,
            "aaf": None,
            "cfb": None,
        }
        if not cfb_only:
            result["aaf"] = aaf_walker.serialize_object(
                f.content, full_bytes=full_bytes, bytes_limit=bytes_limit
            )
        if not object_only:
            result["cfb"] = cfb_walker.cfb_tree(f)

        if as_json:
            click.echo(json.dumps(result, indent=2, ensure_ascii=False))
            return

        # Human form
        click.echo(f"file: {result['file']}")
        click.echo(f"sha256: {sha}")
        click.echo("")
        if not cfb_only:
            click.echo("--- AAF object graph ---")
            for line in aaf_walker.walk_human(f.content):
                click.echo(line)
            click.echo("")
        if not object_only:
            click.echo("--- CFB tree ---")
            for line in cfb_walker.cfb_human(f):
                click.echo(line)


# --- cfb -------------------------------------------------------------------


@cli.command(name="cfb")
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option(
    "--include-metadict",
    is_flag=True,
    help="Include the MetaDictionary-1 subtree (off by default; output is huge).",
)
@click.option(
    "--show-bytes",
    "show_bytes",
    type=str,
    default=None,
    help="Switch to hex-view mode for a single stream by full path.",
)
@click.option("--offset", type=int, default=0, help="Byte offset for --show-bytes.")
@click.option(
    "--length",
    type=int,
    default=cfb_walker.DEFAULT_HEX_PREVIEW,
    help="Number of bytes to read for --show-bytes (default 4KB).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON tree instead of text.")
def cfb_cmd(
    aaf_path: str,
    include_metadict: bool,
    show_bytes: Optional[str],
    offset: int,
    length: int,
    as_json: bool,
) -> None:
    """Raw CFB storage tree, or a hex view of a single stream."""
    with _open_readonly(aaf_path) as f:
        if show_bytes:
            try:
                data, info = cfb_walker.read_stream_bytes(
                    f, show_bytes, offset=offset, length=length
                )
            except FileNotFoundError as exc:
                raise click.ClickException(str(exc)) from exc
            if as_json:
                import base64

                click.echo(
                    json.dumps(
                        {
                            **info,
                            "base64": base64.b64encode(data).decode("ascii"),
                        },
                        indent=2,
                    )
                )
                return
            click.echo(f"stream: {info['path']}")
            click.echo(
                f"total_size: {info['total_size']}  offset: {info['offset']}  "
                f"read_length: {info['read_length']}"
                + ("  (truncated)" if info["truncated"] else "")
            )
            click.echo("")
            for line in cfb_walker.format_hex_view(data, base_offset=info["offset"]):
                click.echo(line)
            return

        if as_json:
            click.echo(
                json.dumps(
                    cfb_walker.cfb_tree(f, include_metadict=include_metadict),
                    indent=2,
                )
            )
            return
        for line in cfb_walker.cfb_human(f, include_metadict=include_metadict):
            click.echo(line)


# --- inspect ---------------------------------------------------------------


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--mob-id", "mob_id", default=None, help="MobID URN or hex.")
@click.option("--path", "path", default=None, help="Slash-separated property path.")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def inspect(
    aaf_path: str,
    mob_id: Optional[str],
    path: Optional[str],
    as_json: bool,
) -> None:
    """Detailed dump of a single object with one level of reference resolution."""
    if not mob_id and not path:
        raise click.UsageError("Provide --mob-id or --path.")
    if mob_id and path:
        raise click.UsageError("Provide --mob-id OR --path, not both.")

    with _open_readonly(aaf_path) as f:
        if mob_id:
            target = resolver_mod.resolve_mob(f, mob_id)
            if target is None:
                raise click.ClickException(f"mob not found: {mob_id!r}")
        else:
            try:
                target = resolver_mod.resolve_path(f, path)
            except ValueError as exc:
                raise click.ClickException(str(exc)) from exc

        if hasattr(target, "properties"):
            obj_serial = aaf_walker.serialize_object(target)
        else:
            # Scalar leaf — emit a typed envelope so output stays parseable
            from aafbrowser.core.serialize import serialize_scalar

            obj_serial = {
                "_type": "scalar_leaf",
                "value": serialize_scalar(target),
            }

        if as_json:
            click.echo(json.dumps(obj_serial, indent=2, ensure_ascii=False))
            return

        if obj_serial.get("_type") == "scalar_leaf":
            click.echo("scalar leaf:")
            click.echo(json.dumps(obj_serial["value"], indent=2))
            return

        # Human form: walk_human gives an indented tree; one-level reference
        # resolution is exactly what serialize_object already does (WeakRefs
        # render as a target-info line).
        for line in aaf_walker.walk_human(target):
            click.echo(line)


# --- find ------------------------------------------------------------------


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--pattern", required=True, help="Regex to match (Python re flavor).")
@click.option(
    "--in",
    "in_scope",
    type=click.Choice(["names", "values", "both"]),
    default="both",
    help="What to match against. Default both.",
)
@click.option(
    "--layer",
    type=click.Choice(["aaf", "cfb", "both"]),
    default="both",
    help="Which layer to search. Default both.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON one match per line.")
def find(
    aaf_path: str,
    pattern: str,
    in_scope: str,
    layer: str,
    as_json: bool,
) -> None:
    """Regex search across the AAF object graph and/or CFB tree."""
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise click.ClickException(f"invalid regex: {exc}") from exc

    in_names = in_scope in ("names", "both")
    in_values = in_scope in ("values", "both")

    with _open_readonly(aaf_path) as f:
        matches: list = []
        if layer in ("aaf", "both"):
            matches.extend(
                resolver_mod.find_in_aaf(
                    f, regex, in_names=in_names, in_values=in_values
                )
            )
        if layer in ("cfb", "both"):
            # CFB doesn't distinguish names/values the same way; the regex
            # is applied to entry names and class_ids.
            matches.extend(resolver_mod.find_in_cfb(f, regex))

        if as_json:
            for m in matches:
                click.echo(
                    json.dumps(
                        {
                            "layer": m.layer,
                            "path": m.path,
                            "classname": m.classname,
                            "field": m.field,
                            "value": m.value,
                            "where": m.where,
                        },
                        ensure_ascii=False,
                    )
                )
            return

        if not matches:
            click.echo(f"no matches for {pattern!r}", err=True)
            return

        for m in matches:
            line = f"[{m.layer}/{m.where}] {m.path}  ({m.classname}.{m.field})"
            if m.value:
                line += f"  = {m.value}"
            click.echo(line)


if __name__ == "__main__":
    cli()
