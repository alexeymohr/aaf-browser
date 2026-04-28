"""Tests for aafbrowser.core.resolver — mob/path resolution + search."""
from __future__ import annotations

import re

import aaf2

from aafbrowser.core.resolver import (
    find_in_aaf,
    find_in_cfb,
    resolve_mob,
    resolve_path,
)


def test_resolve_mob_by_urn(two_mob_aaf):
    with aaf2.open(str(two_mob_aaf), "r") as f:
        target = next(iter(f.content.mobs))
        urn = str(target.mob_id)
    with aaf2.open(str(two_mob_aaf), "r") as f:
        mob = resolve_mob(f, urn)
    assert mob is not None
    assert mob.name == target.name


def test_resolve_mob_by_dotted_hex(two_mob_aaf):
    with aaf2.open(str(two_mob_aaf), "r") as f:
        target = next(iter(f.content.mobs))
        urn = str(target.mob_id)
    dotted = urn.split("umid:")[-1]
    with aaf2.open(str(two_mob_aaf), "r") as f:
        mob = resolve_mob(f, dotted)
    assert mob is not None
    assert mob.name == target.name


def test_resolve_mob_by_plain_hex(two_mob_aaf):
    with aaf2.open(str(two_mob_aaf), "r") as f:
        target = next(iter(f.content.mobs))
        urn = str(target.mob_id)
    plain = urn.split("umid:")[-1].replace(".", "")
    with aaf2.open(str(two_mob_aaf), "r") as f:
        mob = resolve_mob(f, plain)
    assert mob is not None


def test_resolve_mob_unknown_returns_none(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        assert resolve_mob(f, "not-a-real-id") is None


def test_resolve_path_to_mob_via_mob_id(two_mob_aaf):
    with aaf2.open(str(two_mob_aaf), "r") as f:
        target = next(iter(f.content.mobs))
        urn = str(target.mob_id)
    with aaf2.open(str(two_mob_aaf), "r") as f:
        mob = resolve_path(f, f"Mobs/{urn}")
    assert mob is not None
    assert getattr(mob, "name", None) == target.name


def test_resolve_path_descends_into_slot_and_segment(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        mob = next(iter(f.content.mobs))
        urn = str(mob.mob_id)
    with aaf2.open(str(minimal_aaf), "r") as f:
        seg = resolve_path(f, f"Mobs/{urn}/Slots/0/Segment")
    assert type(seg).__name__ == "Sequence"


def test_resolve_path_unknown_token_raises(minimal_aaf):
    import pytest

    with aaf2.open(str(minimal_aaf), "r") as f:
        with pytest.raises(ValueError):
            resolve_path(f, "Mobs/0/Slots/NonExistent")


def test_find_in_aaf_matches_property_name(two_mob_aaf):
    with aaf2.open(str(two_mob_aaf), "r") as f:
        matches = list(find_in_aaf(f, re.compile(r"(?i)mobid")))
    assert any(m.field == "MobID" and m.where == "name" for m in matches)


def test_find_in_aaf_matches_value_string(two_mob_aaf):
    """Mob names contain `Channel_1_Host` and `Channel_2_Contestant`."""
    with aaf2.open(str(two_mob_aaf), "r") as f:
        matches = list(find_in_aaf(f, re.compile(r"(?i)channel")))
    value_hits = [m for m in matches if m.where == "value"]
    assert any("Channel_1_Host" in m.value for m in value_hits)
    assert any("Channel_2_Contestant" in m.value for m in value_hits)


def test_find_in_aaf_can_restrict_to_names_only(two_mob_aaf):
    with aaf2.open(str(two_mob_aaf), "r") as f:
        matches = list(
            find_in_aaf(f, re.compile(r"(?i)channel"), in_values=False)
        )
    # No `Channel` in any property NAME of the AAF schema
    assert matches == []


def test_find_in_aaf_can_restrict_to_values_only(two_mob_aaf):
    with aaf2.open(str(two_mob_aaf), "r") as f:
        matches = list(
            find_in_aaf(f, re.compile(r"^MobID$"), in_names=False)
        )
    # Only matches via stringified UMID values, none from name (we
    # excluded names). MobIDs as values stringify as URNs, so no match.
    assert matches == []


def test_find_in_cfb_matches_storage_name(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        matches = list(find_in_cfb(f, re.compile(r"(?i)metadictionary")))
    assert any(m.layer == "cfb" for m in matches)


def test_find_in_cfb_matches_class_id_substring(minimal_aaf):
    """Class IDs are AUID hex; pick a fragment present in many AAF class_ids."""
    with aaf2.open(str(minimal_aaf), "r") as f:
        matches = list(find_in_cfb(f, re.compile(r"060e-2b34")))
    assert any(m.where == "class_id" for m in matches)
