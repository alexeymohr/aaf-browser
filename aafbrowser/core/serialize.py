"""
Type-tagging serializer for AAF property values.

Converts AAF property values into JSON-friendly structures while preserving
type information via a `_type` discriminator. Round-trip fidelity is the
goal — never silently coerce an AAFRational to a float, an AUID to a generic
string, or a datetime to an ISO string without flagging the type.

The walker for AAFObject graphs (with cycle detection) lives in
`aafbrowser.core.aaf` and calls into this module for individual property
values; this module knows nothing about graph traversal.

Spec: see docs/phase1-brief.md "Property serialization spec".
"""
from __future__ import annotations

import base64
import datetime as _dt
import sys
import warnings
from typing import Any, Callable, Optional

from aaf2.auid import AUID
from aaf2.mobid import MobID
from aaf2.rational import AAFRational


DEFAULT_BYTES_PREVIEW_LIMIT = 1024  # 1 KB; opt in to full bytes via flag


def serialize_scalar(
    value: Any,
    *,
    full_bytes: bool = False,
    bytes_limit: int = DEFAULT_BYTES_PREVIEW_LIMIT,
) -> Any:
    """
    Serialize a single scalar property value to a JSON-friendly form.

    For scalars whose Python type does not unambiguously round-trip through
    JSON (datetime, AAFRational, AUID/MobID, bytes), wrap in a typed envelope
    `{"_type": ..., "value": ..., ...}`. Plain primitives pass through
    unchanged.

    Unknown types are not silently coerced — they emit a warning and a
    `{"_type": "unknown", ...}` envelope so downstream consumers see the
    leak rather than a silent loss.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value

    if isinstance(value, _dt.datetime):
        return {"_type": "datetime", "value": value.isoformat()}

    if isinstance(value, AAFRational):
        num = int(value.numerator)
        den = int(value.denominator)
        return {
            "_type": "rational",
            "num": num,
            "den": den,
            "value": (num / den) if den else None,
        }

    if isinstance(value, MobID):
        # pyaaf2 already formats MobIDs as `urn:smpte:umid:...`
        return {"_type": "mobid", "value": str(value)}

    if isinstance(value, AUID):
        # pyaaf2 stringifies AUIDs as a hyphenated UUID, not a URN.
        # The `_type` discriminator preserves identity regardless.
        return {"_type": "auid", "value": str(value)}

    if isinstance(value, (bytes, bytearray, memoryview)):
        b = bytes(value)
        out: dict[str, Any] = {"_type": "bytes", "length": len(b)}
        if full_bytes or len(b) <= bytes_limit:
            out["base64"] = base64.b64encode(b).decode("ascii")
            out["truncated"] = False
        else:
            out["base64"] = base64.b64encode(b[:bytes_limit]).decode("ascii")
            out["truncated"] = True
            out["preview_length"] = bytes_limit
        return out

    if isinstance(value, (list, tuple)):
        return [
            serialize_scalar(v, full_bytes=full_bytes, bytes_limit=bytes_limit)
            for v in value
        ]

    if isinstance(value, dict):
        return {
            str(k): serialize_scalar(v, full_bytes=full_bytes, bytes_limit=bytes_limit)
            for k, v in value.items()
        }

    # Fallback — never silently drop data; let the caller see what leaked.
    py_type = f"{type(value).__module__}.{type(value).__name__}"
    warnings.warn(
        f"serialize_scalar: unknown value type {py_type!r}; emitting 'unknown' envelope",
        stacklevel=2,
    )
    print(
        f"warning: serialize_scalar: unknown value type {py_type!r} (repr={value!r})",
        file=sys.stderr,
    )
    return {"_type": "unknown", "python_type": py_type, "repr": repr(value)}


def weakref_target_info(target: Any) -> dict[str, Any]:
    """
    Identifying info for a WeakRefProperty target. Never recurses; we just
    extract enough to identify the dictionary entry being pointed at.
    """
    classname = type(target).__name__
    name: Optional[str] = None
    auid: Optional[str] = None

    for attr in ("name", "Name"):
        v = getattr(target, attr, None)
        if isinstance(v, str):
            name = v
            break

    for attr in ("auid", "AUID", "uuid"):
        v = getattr(target, attr, None)
        if v is not None:
            auid = str(v)
            break

    return {
        "_type": "weakref",
        "target_class": classname,
        "target_name": name,
        "target_auid": auid,
    }


def serialize_property(
    prop: Any,
    *,
    recurse: Callable[[Any], Any],
    full_bytes: bool = False,
    bytes_limit: int = DEFAULT_BYTES_PREVIEW_LIMIT,
) -> Any:
    """
    Serialize a single Property instance.

    `recurse(obj)` is the AAFObject serializer (with cycle detection) — this
    function calls it for StrongRef* values and for the targets of
    StrongRefSet iterables. WeakRefs do NOT recurse.
    """
    cls_name = type(prop).__name__
    value = prop.value

    if cls_name == "StrongRefProperty":
        return None if value is None else recurse(value)

    if cls_name == "StrongRefVectorProperty":
        return [recurse(v) for v in (value or [])]

    if cls_name == "StrongRefSetProperty":
        return [recurse(v) for v in (value or [])]

    if cls_name == "WeakRefProperty":
        if value is None:
            return None
        return weakref_target_info(value)

    # Plain Property — scalar, including AAFRational, AUID, MobID,
    # datetime, bytes, str, int, bool, float, None
    return serialize_scalar(value, full_bytes=full_bytes, bytes_limit=bytes_limit)
