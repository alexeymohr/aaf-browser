"""
Reference resolution and search for the AAF object graph.

- `resolve_mob(f, mob_id_str)`: locate a Mob by URN, dotted-hex, or plain-hex
  MobID string.
- `resolve_path(f, "Mobs/<mob-id>/Slots/0/Segment")`: walk slash-separated
  property tokens from `f.content`. Tokens may be a property name, an
  integer (vector index), or a mob-id (for the Mobs set).
- `find_in_aaf(f, regex, where=...)`: regex search over the strong-ref
  subtree of `f.content`. Yields `Match` records with full property path,
  classname, name, and a truncated string-coerced value.
- `find_in_cfb(f, regex)`: regex search over CFB storage/stream names and
  class_ids.

Cycle-safe: the AAF search shares the same `id()` set discipline as the
serializer.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterator, Optional

from aaf2.mobid import MobID

from .cfb import walk_cfb


@dataclass
class Match:
    layer: str  # "aaf" or "cfb"
    path: str  # full property path or CFB path
    classname: str  # class of the containing object/entry
    field: str  # property/attribute name; "" for class_id matches
    value: str  # string-coerced value, truncated
    where: str  # "name" | "value" | "class_id"


def _contains_mob(collection: Any, mob: Any) -> bool:
    try:
        return mob in collection
    except Exception:
        return False


def _try_parse_mob_id(s: str) -> Optional[MobID]:
    """Tolerant MobID parser: URN, dotted-hex, or plain-hex."""
    if not isinstance(s, str):
        return None
    candidates = [s.strip()]
    # Plain hex (32 bytes = 64 hex chars) — MobID can also accept dotted
    if "." not in s and not s.startswith("urn:") and len(s.strip()) == 64:
        candidates.append(s.strip())
    for cand in candidates:
        try:
            return MobID(cand)
        except Exception:
            continue
    return None


def resolve_mob(aaf_file: Any, mob_id_str: str) -> Optional[Any]:
    """
    Look up a Mob by id. Accepts URN, dotted-hex, or plain-hex string.
    Returns the Mob object or None.
    """
    parsed = _try_parse_mob_id(mob_id_str)
    if parsed is None:
        return None
    return aaf_file.content.mobs.get(parsed)


def _get_property(obj: Any, name: str) -> Optional[Any]:
    """Find a property on an AAFObject by name (exact match)."""
    prop_iter = getattr(obj, "properties", None)
    if not callable(prop_iter):
        return None
    for prop in prop_iter():
        if prop.name == name:
            return prop
    return None


def resolve_path(aaf_file: Any, path: str) -> Any:
    """
    Walk slash-separated tokens from `f.content`. Each token resolves a
    property name, a numeric index (for vector properties), or a mob-id
    (for the Mobs set).

    Raises ValueError on a token that doesn't resolve. The resulting object
    is whatever the final token names — typically an AAFObject, sometimes a
    scalar value if the final token resolves to a plain Property.
    """
    parts = [p for p in path.split("/") if p]
    cur: Any = aaf_file.content

    for token in parts:
        prop = _get_property(cur, token) if hasattr(cur, "properties") else None

        if prop is not None:
            kind = type(prop).__name__
            if kind in ("StrongRefProperty", "WeakRefProperty"):
                cur = prop.value
            elif kind in ("StrongRefVectorProperty", "StrongRefSetProperty"):
                # Surface the collection so the next token indexes into it
                cur = prop.value
            else:
                # Scalar — caller gets the property's value
                cur = prop.value
            continue

        # Token was not a property of `cur`. Fall through several
        # resolution strategies: numeric index → mob-id → set-member by
        # `name`. Any one that matches advances; otherwise we give up.

        # Numeric index (vector property whose value is a list)
        if isinstance(cur, list):
            try:
                idx = int(token)
            except ValueError:
                idx = None
            if idx is not None:
                try:
                    cur = cur[idx]
                except IndexError:
                    raise ValueError(
                        f"path token {token!r}: index out of range "
                        f"(list len={len(cur)})"
                    )
                continue

        # Mob-set membership: try mob-id parse and lookup
        mob = resolve_mob(aaf_file, token)
        if mob is not None and (
            cur is aaf_file.content.mobs.value
            or cur is aaf_file.content.mobs
            or _contains_mob(cur, mob)
        ):
            cur = mob
            continue

        # Set lookup by `name` attribute on members
        if hasattr(cur, "__iter__") and not isinstance(cur, (str, bytes)):
            try:
                items = list(cur)
            except TypeError:
                items = []
            matched = None
            for item in items:
                nm = getattr(item, "name", None)
                if isinstance(nm, str) and nm == token:
                    matched = item
                    break
            if matched is not None:
                cur = matched
                continue

        raise ValueError(
            f"path token {token!r} could not be resolved on "
            f"{type(cur).__name__}"
        )

    return cur


# --- search -----------------------------------------------------------------


_TRUNC = 200


def _trunc(s: str) -> str:
    return s if len(s) <= _TRUNC else s[: _TRUNC - 3] + "..."


def find_in_aaf(
    aaf_file: Any,
    pattern: re.Pattern,
    *,
    in_names: bool = True,
    in_values: bool = True,
    mob_class: Optional[set[str]] = None,
) -> Iterator[Match]:
    """
    Yield Match records for every property whose name (if in_names) or
    string-coerced scalar value (if in_values) matches `pattern`. Recurses
    StrongRef* properties; never recurses into WeakRef targets. Cycle-safe.

    `mob_class`, if set, restricts the AAF-layer walk to Mobs whose class
    name (e.g. "MasterMob", "CompositionMob") is in the set. The filter
    applies at the top-level Mobs collection only — once we've descended
    into a matching Mob, the full subtree is searched. This honors how
    questions are usually phrased ("KEKE under CompositionMobs") without
    over-restricting nested matches.
    """
    visited: set[int] = set()
    skip_unmatched_mobs = bool(mob_class)

    def walk(obj: Any, path: str) -> Iterator[Match]:
        if id(obj) in visited:
            return
        visited.add(id(obj))
        prop_iter = getattr(obj, "properties", None)
        if not callable(prop_iter):
            return
        classname = type(obj).__name__
        try:
            for prop in prop_iter():
                pname = prop.name
                pkind = type(prop).__name__
                child_path = f"{path}/{pname}" if path else pname

                if in_names and pattern.search(pname):
                    yield Match(
                        layer="aaf",
                        path=child_path,
                        classname=classname,
                        field=pname,
                        value="",
                        where="name",
                    )

                if pkind == "StrongRefProperty":
                    v = prop.value
                    if v is not None:
                        yield from walk(v, child_path)
                elif pkind in ("StrongRefVectorProperty", "StrongRefSetProperty"):
                    # Top-level Mobs collection is the one place we apply
                    # the mob_class filter. Identified by ContentStorage
                    # parent + property name "Mobs". Anywhere else
                    # (StrongRefSet of Slots, etc.) is searched in full.
                    is_mobs = (
                        skip_unmatched_mobs
                        and pname == "Mobs"
                        and classname == "ContentStorage"
                    )
                    for i, child in enumerate(prop.value or []):
                        if is_mobs and type(child).__name__ not in mob_class:
                            continue
                        yield from walk(child, f"{child_path}/{i}")
                elif pkind == "WeakRefProperty":
                    target = prop.value
                    if target is not None and in_values:
                        # WeakRef target identity (name + class) is meaningful
                        target_name = getattr(target, "name", None)
                        if isinstance(target_name, str) and pattern.search(target_name):
                            yield Match(
                                layer="aaf",
                                path=child_path,
                                classname=classname,
                                field=pname,
                                value=f"-> {type(target).__name__} {target_name!r}",
                                where="value",
                            )
                else:
                    # Scalar
                    if in_values:
                        v = prop.value
                        try:
                            sv = str(v)
                        except Exception:
                            sv = repr(v)
                        if pattern.search(sv):
                            yield Match(
                                layer="aaf",
                                path=child_path,
                                classname=classname,
                                field=pname,
                                value=_trunc(sv),
                                where="value",
                            )
        finally:
            visited.discard(id(obj))

    yield from walk(aaf_file.content, "")


def find_in_cfb(aaf_file: Any, pattern: re.Pattern) -> Iterator[Match]:
    """
    Yield Match records for every CFB entry whose name or class_id matches
    `pattern`. Searches the full tree including the MetaDictionary subtree
    (search has no reason to filter — that's a presentation concern only).
    """
    for cur_path, storages, streams in walk_cfb(aaf_file, include_metadict=True):
        for entries, kind in ((storages, "storage"), (streams, "stream")):
            for e in entries:
                if pattern.search(e["name"]):
                    yield Match(
                        layer="cfb",
                        path=e["path"],
                        classname=kind,
                        field="name",
                        value=_trunc(e["name"]),
                        where="name",
                    )
                if e["class_id"] and pattern.search(e["class_id"]):
                    yield Match(
                        layer="cfb",
                        path=e["path"],
                        classname=kind,
                        field="class_id",
                        value=e["class_id"],
                        where="class_id",
                    )
