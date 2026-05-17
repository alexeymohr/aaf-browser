"""Tests for the core.mob_index seam: enumeration + filter semantics."""
from __future__ import annotations

from aafbrowser.core import mob_index as mob_index_mod
from aafbrowser.core import source as source_mod


def _entries(aaf_path):
    with source_mod.open_readonly(str(aaf_path)) as f:
        return mob_index_mod.list_mobs(f)


def test_list_mobs_returns_entries_with_expected_shape(two_mob_aaf):
    entries = _entries(two_mob_aaf)
    assert len(entries) >= 2
    e = entries[0]
    assert isinstance(e.mob_id, str)
    assert e.mob_class in {"CompositionMob", "MasterMob", "SourceMob"}
    assert e.slot_count >= 0


def test_to_dict_renames_mob_class_to_class_for_wire(two_mob_aaf):
    entries = _entries(two_mob_aaf)
    d = entries[0].to_dict()
    # The web API has shipped 'class' as the field name; preserve.
    assert "class" in d and "mob_class" not in d
    assert set(d.keys()) == {"mob_id", "class", "name", "slot_count"}


def test_filter_entries_name_contains_is_case_insensitive(two_mob_aaf):
    entries = _entries(two_mob_aaf)
    if not any(e.name for e in entries):
        return  # fixture without names — skip
    needle = next(e.name for e in entries if e.name)[0:2]
    upper = mob_index_mod.filter_entries(entries, name_contains=needle.upper())
    lower = mob_index_mod.filter_entries(entries, name_contains=needle.lower())
    assert [e.mob_id for e in upper] == [e.mob_id for e in lower]
    assert all(needle.lower() in (e.name or "").lower() for e in upper)


def test_filter_entries_excludes_unnamed_when_name_contains_set(two_mob_aaf):
    entries = _entries(two_mob_aaf)
    out = mob_index_mod.filter_entries(entries, name_contains="zzzzz_no_match")
    assert out == []


def test_filter_entries_class_exact_match(two_mob_aaf):
    entries = _entries(two_mob_aaf)
    classes = {e.mob_class for e in entries}
    if not classes:
        return
    target = next(iter(classes))
    out = mob_index_mod.filter_entries(entries, mob_class=target)
    assert all(e.mob_class == target for e in out)


def test_list_mobs_passes_filters_through(two_mob_aaf):
    """list_mobs(handle, ...) and list_mobs(handle) + filter_entries(...)
    must agree — same filter predicate, two timings."""
    with source_mod.open_readonly(str(two_mob_aaf)) as f:
        direct = mob_index_mod.list_mobs(f, mob_class="MasterMob")
        all_entries = mob_index_mod.list_mobs(f)
    via_filter = mob_index_mod.filter_entries(all_entries, mob_class="MasterMob")
    assert [e.mob_id for e in direct] == [e.mob_id for e in via_filter]
