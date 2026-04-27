"""
Raw CFB (MS Compound File Binary) walker.

Provides:

- `walk_cfb(aaf_file, *, include_metadict)`: traversal yielding entry dicts.
- `cfb_tree(aaf_file, ...)`: full JSON-friendly tree with optional
   collapsing of repetitive sibling sequences.
- `cfb_human(aaf_file, ...)`: indented text rendering for the `cfb`
   CLI command.
- `read_stream_bytes(aaf_file, path, offset, length)`: lazy byte-range
   read for the `--show-bytes` flag. Never loads the whole stream eagerly.
- `format_hex_view(data, base_offset)`: classic 16-byte hex+ASCII dump.

Background: even an empty AAF contains thousands of streams from the
MetaDictionary subtree (ClassDefinitions, TypeDefinitions, ...). The
default tree output filters that subtree out; opt back in with
`include_metadict=True`. Sibling collapse keeps the output legible when
e.g. `Properties` lists hundreds of identically-shaped entries.
"""
from __future__ import annotations

from typing import Any, Iterator, Optional


METADICT_NAME = "MetaDictionary-1"
DEFAULT_COLLAPSE_THRESHOLD = 16  # repeating sibling streams collapse beyond this
DEFAULT_HEX_PREVIEW = 4096  # bytes; matches phase 1 brief --show-bytes default


def _entry_dict(entry: Any, kind: str) -> dict[str, Any]:
    """Common identifying info for a DirEntry (storage or stream)."""
    class_id = getattr(entry, "class_id", None)
    return {
        "name": entry.name,
        "kind": kind,
        "path": entry.path(),
        "byte_size": int(getattr(entry, "byte_size", 0) or 0),
        "class_id": str(class_id) if class_id is not None else None,
    }


def _is_under_metadict(path: str) -> bool:
    return path == "/" + METADICT_NAME or path.startswith("/" + METADICT_NAME + "/")


def walk_cfb(
    aaf_file: Any,
    *,
    include_metadict: bool = False,
) -> Iterator[tuple[str, list[dict[str, Any]], list[dict[str, Any]]]]:
    """
    Yield `(path, storages_info, streams_info)` tuples mirroring
    `f.cfb.walk()` but with the entries already projected to JSON-friendly
    dicts and the MetaDictionary subtree filtered out by default.
    """
    cfb = aaf_file.cfb
    for path, storages, streams in cfb.walk():
        # The walker's `path` is the entry name; we need the full path of
        # the *current* directory (root has empty/Root Entry name). Use
        # whichever storage's parent we're inspecting.
        # Easier: compute current dir path from the first storage/stream.
        cur_path = "/"
        if storages:
            cur_path = "/".join(storages[0].path().rsplit("/", 1)[:-1]) or "/"
        elif streams:
            cur_path = "/".join(streams[0].path().rsplit("/", 1)[:-1]) or "/"
        if not include_metadict and _is_under_metadict(cur_path):
            continue
        # Per-storage filter so that we do not list the MetaDictionary-1
        # storage entry itself when filtered, but still surface it as a
        # collapsed marker at the root level — see cfb_tree for the marker.
        kept_storages = [
            s for s in storages
            if include_metadict or s.name != METADICT_NAME
        ]
        yield (
            cur_path,
            [_entry_dict(s, "storage") for s in kept_storages],
            [_entry_dict(st, "stream") for st in streams],
        )


def _collapse_repeats(entries: list[dict[str, Any]], threshold: int) -> list[dict[str, Any]]:
    """
    Collapse runs of streams whose `byte_size` and `class_id` agree into a
    single summary entry once the run length crosses `threshold`. Keeps the
    output legible when the same shape repeats hundreds of times (e.g.
    Properties-XXXX streams in a property set).
    """
    if threshold <= 0 or len(entries) <= threshold:
        return entries

    out: list[dict[str, Any]] = []
    run: list[dict[str, Any]] = []

    def shape(e: dict[str, Any]) -> tuple:
        # Same kind + size + class_id, ignoring numeric/brace suffix in the
        # name (so `Properties-9{0}` and `Properties-9{1}` collapse together)
        stripped = e["name"].rstrip("0123456789-{}")
        return (e["kind"], e["byte_size"], e["class_id"], stripped)

    def flush():
        if not run:
            return
        if len(run) > threshold:
            sample = run[0]
            out.append(
                {
                    "_type": "cfb_run_collapsed",
                    "count": len(run),
                    "first_name": run[0]["name"],
                    "last_name": run[-1]["name"],
                    "byte_size": sample["byte_size"],
                    "class_id": sample["class_id"],
                    "kind": sample["kind"],
                }
            )
        else:
            out.extend(run)
        run.clear()

    cur_shape = None
    for e in entries:
        s = shape(e)
        if s != cur_shape:
            flush()
            cur_shape = s
        run.append(e)
    flush()
    return out


def cfb_tree(
    aaf_file: Any,
    *,
    include_metadict: bool = False,
    collapse_threshold: int = DEFAULT_COLLAPSE_THRESHOLD,
) -> dict[str, Any]:
    """
    Full CFB tree as a JSON-friendly nested dict, anchored at the root.
    Each node is `{ "_type": "cfb_storage", "path": ..., "name": ...,
    "class_id": ..., "storages": [...], "streams": [...] }` for storages
    and a flat dict for streams.
    """
    cfb = aaf_file.cfb
    # Build a lookup keyed by path -> (storages, streams)
    by_path: dict[str, tuple[list[Any], list[Any]]] = {}
    metadict_summary: Optional[dict[str, Any]] = None

    for path, storages, streams in cfb.walk():
        cur_path = "/"
        if storages:
            cur_path = "/".join(storages[0].path().rsplit("/", 1)[:-1]) or "/"
        elif streams:
            cur_path = "/".join(streams[0].path().rsplit("/", 1)[:-1]) or "/"

        if not include_metadict and _is_under_metadict(cur_path):
            # Aggregate MetaDictionary descendants into a summary placeholder.
            if metadict_summary is None:
                metadict_summary = {
                    "_type": "cfb_metadict_filtered",
                    "path": "/" + METADICT_NAME,
                    "storage_count": 0,
                    "stream_count": 0,
                    "note": "MetaDictionary subtree filtered; use --include-metadict.",
                }
            metadict_summary["storage_count"] += len(storages)
            metadict_summary["stream_count"] += len(streams)
            continue
        by_path[cur_path] = (storages, streams)

    def build(path: str, name: str, class_id: Optional[str]) -> dict[str, Any]:
        storages, streams = by_path.get(path, ([], []))
        # Filter storages here so MetaDictionary disappears from listings
        # (it appears under Root Entry); we replace it with a summary.
        kept_storages = []
        for s in storages:
            if not include_metadict and s.name == METADICT_NAME:
                continue
            kept_storages.append(s)
        children = []
        for s in kept_storages:
            cid = getattr(s, "class_id", None)
            children.append(
                build(s.path(), s.name, str(cid) if cid is not None else None)
            )
        stream_entries = [_entry_dict(st, "stream") for st in streams]
        stream_entries = _collapse_repeats(stream_entries, collapse_threshold)
        node = {
            "_type": "cfb_storage",
            "path": path,
            "name": name,
            "class_id": class_id,
            "storages": children,
            "streams": stream_entries,
        }
        return node

    root_node = build("/", "/", None)
    if metadict_summary is not None:
        root_node["storages"].append(metadict_summary)
    return root_node


def cfb_human(
    aaf_file: Any,
    *,
    include_metadict: bool = False,
    collapse_threshold: int = DEFAULT_COLLAPSE_THRESHOLD,
) -> list[str]:
    """Render the CFB tree as a list of indented human-readable lines."""
    tree = cfb_tree(
        aaf_file,
        include_metadict=include_metadict,
        collapse_threshold=collapse_threshold,
    )

    lines: list[str] = []

    def emit(node: dict[str, Any], indent: int) -> None:
        pad = "  " * indent
        if node.get("_type") == "cfb_metadict_filtered":
            lines.append(
                f"{pad}[storage] {METADICT_NAME}/  "
                f"<filtered: {node['storage_count']} storages, "
                f"{node['stream_count']} streams; "
                f"--include-metadict to expand>"
            )
            return
        if node.get("_type") == "cfb_run_collapsed":
            lines.append(
                f"{pad}[stream] x{node['count']}  "
                f"{node['first_name']}..{node['last_name']}  "
                f"({node['byte_size']} bytes each, class_id={node['class_id']})"
            )
            return

        # Storage
        cid = node.get("class_id")
        cid_part = f"  class_id={cid}" if cid else ""
        lines.append(f"{pad}[storage] {node['name']}/{cid_part}")
        for child in node["storages"]:
            emit(child, indent + 1)
        for st in node["streams"]:
            if st.get("_type") == "cfb_run_collapsed":
                emit(st, indent + 1)
            else:
                pad2 = "  " * (indent + 1)
                cid2 = st.get("class_id")
                cid_part2 = f"  class_id={cid2}" if cid2 else ""
                lines.append(
                    f"{pad2}[stream]  {st['name']}  ({st['byte_size']} bytes){cid_part2}"
                )

    emit(tree, 0)
    return lines


# --- byte-range stream reads -----------------------------------------------


def _find_stream(aaf_file: Any, target_path: str) -> Any:
    """Locate a stream DirEntry by its CFB path. Returns None if not found."""
    for _, _, streams in aaf_file.cfb.walk():
        for st in streams:
            if st.path() == target_path:
                return st
    return None


def read_stream_bytes(
    aaf_file: Any,
    stream_path: str,
    *,
    offset: int = 0,
    length: int = DEFAULT_HEX_PREVIEW,
) -> tuple[bytes, dict[str, Any]]:
    """
    Lazily read up to `length` bytes starting at `offset` from the stream
    at `stream_path`. Never loads the full stream eagerly.

    Returns (data, info_dict). info_dict carries total stream size, the
    requested offset/length, and a `truncated` flag if the stream extends
    beyond what we read.
    """
    entry = _find_stream(aaf_file, stream_path)
    if entry is None:
        raise FileNotFoundError(f"CFB stream not found: {stream_path}")

    total = int(getattr(entry, "byte_size", 0) or 0)
    stream = entry.open("r")
    stream.seek(offset)
    data = stream.read(length)
    end = offset + len(data)
    return data, {
        "path": stream_path,
        "total_size": total,
        "offset": offset,
        "read_length": len(data),
        "truncated": end < total,
    }


def format_hex_view(data: bytes, *, base_offset: int = 0, width: int = 16) -> list[str]:
    """Classic offset / hex / ASCII dump, one line per `width`-byte row."""
    lines: list[str] = []
    for i in range(0, len(data), width):
        chunk = data[i : i + width]
        hex_part = " ".join(f"{b:02x}" for b in chunk)
        # Pad hex to width so ASCII column aligns on short final row
        hex_part = hex_part.ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"{base_offset + i:08x}  {hex_part}  |{ascii_part}|")
    return lines
