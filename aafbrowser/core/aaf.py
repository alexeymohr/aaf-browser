"""
AAF object graph walker with cycle detection.

Serializes a starting AAFObject (typically `f.content`) to a JSON-friendly
dict. Each object becomes:

    {
      "_type": "aaf_object",
      "class": "<classname>",
      "mob_id": "<urn or null>",
      "name": "<str or null>",
      "properties": { "<PropName>": <serialized value>, ... },
      "_cycle": false
    }

If a recursive walk re-enters an already-visited object (`id(obj)` seen),
emit a cycle marker:

    {
      "_type": "aaf_object_cycle",
      "class": "<classname>",
      "mob_id": "<urn or null>"
    }

Cycle detection is the responsibility of *this* module — `serialize` knows
about individual values, this module knows about the graph.
"""
from __future__ import annotations

from typing import Any, Optional

from .serialize import DEFAULT_BYTES_PREVIEW_LIMIT, serialize_property


def _identifying(obj: Any) -> tuple[str, Optional[str], Optional[str]]:
    """(classname, mob_id_str_or_None, name_or_None)"""
    classname = type(obj).__name__
    mob_id = getattr(obj, "mob_id", None)
    mob_id_str = str(mob_id) if mob_id is not None else None
    name = getattr(obj, "name", None)
    if not isinstance(name, str):
        name = None
    return classname, mob_id_str, name


def serialize_object(
    obj: Any,
    *,
    full_bytes: bool = False,
    bytes_limit: int = DEFAULT_BYTES_PREVIEW_LIMIT,
    visited: Optional[set[int]] = None,
) -> dict[str, Any]:
    """
    Serialize an AAFObject (and its strong-ref subtree) to a JSON-friendly
    dict. Cycles emit markers rather than recursing.
    """
    if visited is None:
        visited = set()

    classname, mob_id_str, name = _identifying(obj)
    obj_id = id(obj)

    if obj_id in visited:
        return {
            "_type": "aaf_object_cycle",
            "class": classname,
            "mob_id": mob_id_str,
        }

    visited.add(obj_id)
    try:
        properties: dict[str, Any] = {}
        # AAFObject.properties() yields Property instances. If something we
        # walk into doesn't expose .properties() (defensive), we skip rather
        # than crashing.
        prop_iter = getattr(obj, "properties", None)
        if callable(prop_iter):
            for prop in prop_iter():
                properties[prop.name] = serialize_property(
                    prop,
                    recurse=lambda v: serialize_object(
                        v,
                        full_bytes=full_bytes,
                        bytes_limit=bytes_limit,
                        visited=visited,
                    ),
                    full_bytes=full_bytes,
                    bytes_limit=bytes_limit,
                )

        return {
            "_type": "aaf_object",
            "class": classname,
            "mob_id": mob_id_str,
            "name": name,
            "properties": properties,
            "_cycle": False,
        }
    finally:
        # Allow the same object to appear in sibling subtrees (e.g. a Mob
        # referenced by two slots in different roots) without being marked
        # as a cycle. The visited set is per-walk, but we lift the marker
        # once recursion unwinds so siblings can serialize fully too.
        visited.discard(obj_id)


def walk_human(
    obj: Any,
    *,
    indent: int = 0,
    visited: Optional[set[int]] = None,
    max_value_width: int = 80,
) -> list[str]:
    """
    Render an AAF object subtree as a list of indented human-readable lines.

    Each object line shows class + name + identifying property; scalar child
    properties get a one-line summary; strong-ref children recurse with a
    deeper indent. WeakRefs render as a single line tagged `WeakRef →`.
    Cycles emit `<cycle: ClassName>`.
    """
    if visited is None:
        visited = set()

    pad = "  " * indent
    classname, mob_id_str, name = _identifying(obj)
    obj_id = id(obj)
    header_bits = [classname]
    if name:
        header_bits.append(repr(name))
    if mob_id_str:
        header_bits.append(mob_id_str)
    header = pad + " ".join(header_bits)

    if obj_id in visited:
        return [pad + f"<cycle: {classname} {mob_id_str or ''}>".rstrip()]

    visited.add(obj_id)
    out = [header]
    try:
        prop_iter = getattr(obj, "properties", None)
        if not callable(prop_iter):
            return out

        for prop in prop_iter():
            kind = type(prop).__name__
            label = pad + "  " + prop.name

            if kind == "StrongRefProperty":
                v = prop.value
                if v is None:
                    out.append(f"{label}: <none>")
                else:
                    out.append(f"{label}:")
                    out.extend(
                        walk_human(
                            v,
                            indent=indent + 2,
                            visited=visited,
                            max_value_width=max_value_width,
                        )
                    )

            elif kind == "StrongRefVectorProperty":
                items = list(prop.value or [])
                out.append(f"{label} ({len(items)}):")
                for child in items:
                    out.extend(
                        walk_human(
                            child,
                            indent=indent + 2,
                            visited=visited,
                            max_value_width=max_value_width,
                        )
                    )

            elif kind == "StrongRefSetProperty":
                items = list(prop.value or [])
                out.append(f"{label} {{{len(items)}}}:")
                for child in items:
                    out.extend(
                        walk_human(
                            child,
                            indent=indent + 2,
                            visited=visited,
                            max_value_width=max_value_width,
                        )
                    )

            elif kind == "WeakRefProperty":
                target = prop.value
                if target is None:
                    out.append(f"{label}: <none>")
                else:
                    target_class = type(target).__name__
                    target_name = getattr(target, "name", None)
                    out.append(
                        f"{label}: WeakRef -> {target_class}"
                        + (f" {target_name!r}" if isinstance(target_name, str) else "")
                    )

            else:
                # Scalar
                v = prop.value
                summary = repr(v)
                if len(summary) > max_value_width:
                    summary = summary[: max_value_width - 3] + "..."
                out.append(f"{label}: {summary}")

        return out
    finally:
        visited.discard(obj_id)
