"""
aafbrowser CLI — five Click commands consuming aafbrowser.core.

Each command takes exactly one positional argument: a path to an AAF file.
All commands are read-only — pyaaf2 is opened with mode 'r' and inputs are
never written back.
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
from aafbrowser.core import chain as chain_mod
from aafbrowser.core import operator as operator_mod
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
@click.option(
    "--class",
    "mob_classes",
    multiple=True,
    help="Restrict the AAF-layer walk to Mobs of this class "
         "(e.g. CompositionMob). Repeatable.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON one match per line.")
def find(
    aaf_path: str,
    pattern: str,
    in_scope: str,
    layer: str,
    mob_classes: tuple,
    as_json: bool,
) -> None:
    """Regex search across the AAF object graph and/or CFB tree."""
    try:
        regex = re.compile(pattern)
    except re.error as exc:
        raise click.ClickException(f"invalid regex: {exc}") from exc

    in_names = in_scope in ("names", "both")
    in_values = in_scope in ("values", "both")
    mob_class_set = set(mob_classes) if mob_classes else None

    with _open_readonly(aaf_path) as f:
        matches: list = []
        if layer in ("aaf", "both"):
            matches.extend(
                resolver_mod.find_in_aaf(
                    f, regex,
                    in_names=in_names, in_values=in_values,
                    mob_class=mob_class_set,
                )
            )
        if layer in ("cfb", "both"):
            # CFB doesn't distinguish names/values the same way; the regex
            # is applied to entry names and class_ids. The --class filter
            # is AAF-layer only, since CFB storages aren't grouped by Mob
            # class.
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


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--mob-id", "mob_id", default=None, help="MobID URN/dotted/plain hex.")
@click.option("--path", "path", default=None,
              help="Slash-separated property path (start at f.content).")
@click.option("--slot", "slot_id", type=int, default=None,
              help="Slot ID to enter when starting at a Mob (defaults to first slot).")
@click.option("--max-hops", type=int, default=64, show_default=True)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON.")
def walk(
    aaf_path: str,
    mob_id: Optional[str],
    path: Optional[str],
    slot_id: Optional[int],
    max_hops: int,
    as_json: bool,
) -> None:
    """Walk the SourceClip chain hop-by-hop from a Mob or property path."""
    if not mob_id and not path:
        raise click.UsageError("Provide --mob-id or --path.")
    if mob_id and path:
        raise click.UsageError("Provide --mob-id OR --path, not both.")

    sha = _file_sha256(aaf_path)
    with _open_readonly(aaf_path) as f:
        if mob_id:
            target = resolver_mod.resolve_mob(f, mob_id)
            if target is None:
                raise click.ClickException(f"mob not found: {mob_id!r}")
            start = target
        else:
            try:
                start = resolver_mod.resolve_path(f, path)
            except ValueError as exc:
                raise click.ClickException(str(exc)) from exc

        try:
            hops = chain_mod.walk_chain(f, start, slot_id=slot_id, max_hops=max_hops)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

        if as_json:
            payload = {
                "file": str(Path(aaf_path).resolve()),
                "sha256": sha,
                "start": {
                    "class": type(start).__name__,
                    "name": getattr(start, "name", None) if isinstance(getattr(start, "name", None), str) else None,
                    "mob_id": str(getattr(start, "mob_id", "")) or None,
                    "slot_id": slot_id,
                },
                "hops": [h.to_dict() for h in hops],
            }
            click.echo(json.dumps(payload, indent=2, ensure_ascii=False))
            return

        # Human form
        for i, h in enumerate(hops):
            marker = "*" if h.terminal else " "
            ptn = f" ptn={h.physical_track_number}" if h.physical_track_number is not None else ""
            edit = f" edit={h.edit_rate}" if h.edit_rate else ""
            name = f" {h.mob_name!r}" if h.mob_name else ""
            line = (
                f"{marker} [{i}] {h.mob_class}{name} slot={h.slot_id} "
                f"segment={h.segment_class}{ptn}{edit}"
            )
            if h.terminal and h.terminal_reason:
                line += f"  -- terminal: {h.terminal_reason}"
            click.echo(line)


@cli.command(name="web")
@click.argument(
    "aaf_path",
    type=click.Path(exists=True, dir_okay=False, readable=True),
    required=False,
)
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=5173, show_default=True, type=int)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Don't open the URL in the system browser on startup.",
)
def web(aaf_path: Optional[str], host: str, port: int, no_browser: bool) -> None:
    """Start the local web GUI. Optionally open AAF_PATH at launch."""
    import webbrowser
    from wsgiref.simple_server import make_server

    from aafbrowser.web import state as state_mod
    from aafbrowser.web.app import create_app

    if aaf_path:
        try:
            with state_mod.state_lock():
                state_mod.open_file(aaf_path)
        except Exception as exc:
            raise click.ClickException(f"failed to open {aaf_path!r}: {exc}") from exc

    app = create_app()
    # wsgiref.simple_server: stdlib, single-threaded by default. We use
    # both a global state lock AND a single-threaded server — the lock
    # guards pyaaf2 access end-to-end, and the single-threaded server
    # avoids accidental contention. wsgiref also works on Python 3.14
    # where werkzeug's dev server hangs at startup.
    server = make_server(host, port, app)
    url = f"http://{host}:{port}/"
    click.echo(f"aafbrowser web serving on {url}")
    click.echo("Press Ctrl-C to stop.")
    if not no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        with state_mod.state_lock():
            state_mod.close_file()


# --- operator-layer CLI commands --------------------------------------------


def _file_size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _emit_envelope(aaf_path: str, sha: str, payload_key: str, payload) -> None:
    """JSON envelope for the operator CLI commands. Mirrors the shape
    of the corresponding /api/... endpoint, with file + sha at the top."""
    click.echo(json.dumps({
        "file": str(Path(aaf_path).resolve()),
        "sha256": sha,
        payload_key: payload,
    }, indent=2, ensure_ascii=False))


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def session(aaf_path: str, as_json: bool) -> None:
    """Headline summary of an AAF: composition, track + clip + mob counts,
    audio specs, timecode, authoring, duration. Mirrors /api/session."""
    sha = _file_sha256(aaf_path)
    size = _file_size(aaf_path)
    with _open_readonly(aaf_path) as f:
        summary = operator_mod.session_summary(f, file_size_bytes=size)

    if as_json:
        _emit_envelope(aaf_path, sha, "session", summary.to_dict())
        return

    s = summary
    click.echo(f"file: {Path(aaf_path).resolve()}")
    click.echo(f"sha256: {sha}")
    click.echo("")
    click.echo(f"composition: {s.topmost_composition_name or '(none)'}")
    click.echo(f"  mob counts: comp={s.composition_mob_count} master={s.master_mob_count} source={s.source_mob_count}")
    click.echo(f"tracks: {s.audio_track_count} audio · {s.video_track_count} video"
               + (f" · {s.timecode_track_count} timecode" if s.timecode_track_count else ""))
    click.echo(f"clips: {s.total_clip_count} total")
    if s.timecode is not None:
        tc = s.timecode
        click.echo(f"timecode: rate={tc.edit_rate} fps={tc.fps_nominal} drop={tc.drop}"
                   f" start={tc.start_timecode} (frame {tc.start_frames})")
    if s.duration_timecode or s.duration_seconds is not None:
        secs = (f"{s.duration_seconds:.3f}s" if s.duration_seconds is not None else "")
        click.echo(f"duration: {s.duration_timecode or '—'} ({secs})")
    if s.authoring is not None:
        a = s.authoring
        click.echo(f"authored by: {a.product_name or '(unknown)'}"
                   + (f" · {a.platform}" if a.platform else "")
                   + (f" · kind={a.kind}" if a.kind else ""))
    if s.last_modified:
        click.echo(f"modified: {s.last_modified}")
    if s.audio is not None and s.audio.audio_source_count > 0:
        au = s.audio
        click.echo(f"audio sources: {au.audio_source_count}"
                   f" · rates={dict(au.sample_rates)}"
                   f" · bits={dict(au.bit_depths)}"
                   f" · channels={dict(au.channel_counts)}")


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def tracks(aaf_path: str, as_json: bool) -> None:
    """Audio + video tracks on the topmost CompositionMob. Each row carries
    PT-style positional + slot id + clip count. Mirrors /api/tracks."""
    sha = _file_sha256(aaf_path)
    with _open_readonly(aaf_path) as f:
        ts = operator_mod.list_tracks(f)
        comp = operator_mod.pick_topmost_composition(f)
        comp_meta = None
        if comp is not None:
            nm = getattr(comp, "name", None)
            comp_meta = {
                "mob_id": str(getattr(comp, "mob_id", "")) or None,
                "name": nm if isinstance(nm, str) else None,
            }
        tc_dict = None
        if comp is not None:
            tc = operator_mod._build_timecode_info(comp)
            if tc is not None:
                tc_dict = tc.to_dict()

    if as_json:
        click.echo(json.dumps({
            "file": str(Path(aaf_path).resolve()),
            "sha256": sha,
            "topmost_composition": comp_meta,
            "timecode": tc_dict,
            "tracks": [t.to_dict() for t in ts],
        }, indent=2, ensure_ascii=False))
        return

    click.echo(f"file: {Path(aaf_path).resolve()}")
    click.echo(f"sha256: {sha}")
    click.echo(f"topmost: {(comp_meta or {}).get('name') or '(none)'}")
    click.echo(f"{len(ts)} track{'' if len(ts) == 1 else 's'}")
    click.echo("")
    # Per-kind sequential index, mirrors the frontend's A1/V1 labels.
    audio_idx = 0
    video_idx = 0
    for t in ts:
        if t.kind == "audio":
            audio_idx += 1
            pos = f"A{audio_idx}"
        elif t.kind == "video":
            video_idx += 1
            pos = f"V{video_idx}"
        else:
            pos = f"slot {t.slot_id}"
        name = t.name or pos
        pan = f" [{t.pan_channel}]" if t.pan_channel else ""
        click.echo(
            f"  {pos:<5} slot={t.slot_id:<4} kind={t.kind:<5}"
            f" clips={t.clip_count:<5} {name}{pan}"
        )


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--slot", type=int, required=True,
              help="Topmost-composition slot id to enumerate clips on.")
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def clips(aaf_path: str, slot: int, as_json: bool) -> None:
    """Clips on a single track: timeline TC, length, mic identity,
    recovery_status. Mirrors /api/track/clips."""
    sha = _file_sha256(aaf_path)
    with _open_readonly(aaf_path) as f:
        try:
            cs = operator_mod.list_clips(f, slot)
        except ValueError as exc:
            raise click.ClickException(str(exc)) from exc

    if as_json:
        click.echo(json.dumps({
            "file": str(Path(aaf_path).resolve()),
            "sha256": sha,
            "slot_id": slot,
            "clips": [c.to_dict() for c in cs],
        }, indent=2, ensure_ascii=False))
        return

    click.echo(f"file: {Path(aaf_path).resolve()}")
    click.echo(f"sha256: {sha}")
    click.echo(f"slot {slot}: {len(cs)} clip{'' if len(cs) == 1 else 's'}")
    click.echo("")
    for c in cs:
        # Compact one-liner per clip. Recoverable mic identity gets
        # bracketed for visibility; non-source-clip components show
        # their class.
        if c.component_class == "SourceClip":
            label_parts = []
            if c.mic_identity:
                label_parts.append(f"[{c.mic_identity}]")
            if c.source_mob_name:
                label_parts.append(c.source_mob_name)
            label = " ".join(label_parts) or "(unknown source)"
            extra = f" recovery={c.recovery_status}"
            if c.recovery_method and c.recovery_status != "recoverable":
                extra += f"({c.recovery_method})"
            click.echo(
                f"  [{c.index:>4}] ts={c.timeline_start} len={c.length}"
                f" {label}{extra}"
            )
        else:
            click.echo(
                f"  [{c.index:>4}] ts={c.timeline_start} len={c.length}"
                f" <{c.component_class}>"
            )
        if c.sub_clips:
            for sub in c.sub_clips:
                sub_label = sub.mic_identity or sub.source_mob_name or "(unknown)"
                click.echo(
                    f"      └─ input{sub.index}: [{sub_label}] recovery={sub.recovery_status}"
                )


@cli.command()
@click.argument("aaf_path", type=click.Path(exists=True, dir_okay=False, readable=True))
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
@click.option("--unused", is_flag=True,
              help="Include sources with use_count=0 (default skips them).")
def sources(aaf_path: str, as_json: bool, unused: bool) -> None:
    """Cross-track deduplicated source-mob inventory with use counts.
    Mirrors /api/sources."""
    sha = _file_sha256(aaf_path)
    with _open_readonly(aaf_path) as f:
        inv = operator_mod.source_inventory(f)
    if not unused:
        inv = [e for e in inv if e.use_count > 0]

    if as_json:
        click.echo(json.dumps({
            "file": str(Path(aaf_path).resolve()),
            "sha256": sha,
            "sources": [e.to_dict() for e in inv],
            "total": len(inv),
        }, indent=2, ensure_ascii=False))
        return

    click.echo(f"file: {Path(aaf_path).resolve()}")
    click.echo(f"sha256: {sha}")
    click.echo(f"{len(inv)} source mob{'' if len(inv) == 1 else 's'}"
               f" {'(all)' if unused else '(used)'}")
    click.echo("")
    for e in inv:
        bits = []
        if e.sample_rate:
            bits.append(e.sample_rate)
        if e.bits_per_sample:
            bits.append(f"{e.bits_per_sample}-bit")
        if e.channels:
            bits.append(f"{e.channels}ch")
        if e.descriptor_class:
            bits.append(e.descriptor_class)
        fmt = " · ".join(bits) if bits else "—"
        name = e.name or "(unnamed)"
        loc = e.locators[0]["url"] if e.locators else ""
        click.echo(f"  used={e.use_count:>4}  {name:<40}  {fmt}"
                   + (f"  loc={loc}" if loc else ""))


if __name__ == "__main__":
    cli()
