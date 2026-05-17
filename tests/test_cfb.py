"""Tests for aafbrowser.core.cfb — CFB layer walker and stream byte reads."""
from __future__ import annotations

import json

import aaf2

from aafbrowser.core.cfb import (
    METADICT_NAME,
    _collapse_repeats,
    cfb_human,
    cfb_tree,
    format_hex_view,
    render_hex_lines,
    read_stream_bytes,
    walk_cfb,
)


def test_cfb_tree_root_node_has_expected_shape(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        tree = cfb_tree(f)
    assert tree["_type"] == "cfb_storage"
    assert tree["path"] == "/"
    assert isinstance(tree["storages"], list)
    assert isinstance(tree["streams"], list)


def test_cfb_tree_default_filters_metadictionary(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        tree = cfb_tree(f)
    # No actual MetaDictionary storage child; only a filtered marker
    direct_names = [s.get("name") for s in tree["storages"]]
    assert METADICT_NAME not in direct_names
    markers = [s for s in tree["storages"] if s.get("_type") == "cfb_metadict_filtered"]
    assert len(markers) == 1
    assert markers[0]["storage_count"] > 0
    assert markers[0]["stream_count"] > 0


def test_cfb_tree_include_metadict_keeps_subtree(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        tree = cfb_tree(f, include_metadict=True)
    direct_names = [s.get("name") for s in tree["storages"]]
    assert METADICT_NAME in direct_names
    metadict = next(s for s in tree["storages"] if s.get("name") == METADICT_NAME)
    # MetaDictionary has many child storages
    assert len(metadict["storages"]) > 10


def test_cfb_tree_is_json_serializable(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        tree = cfb_tree(f)
    s = json.dumps(tree)
    again = json.loads(s)
    assert again["path"] == "/"


def test_cfb_human_excludes_metadict_by_default_with_marker(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        lines = cfb_human(f)
    text = "\n".join(lines)
    assert "[storage] /" in text  # root header
    assert "filtered" in text  # metadict marker line
    # Confirm the bulk of the metadict subtree is NOT present
    deep_metadict = "MetaDictionary-1/ClassDefinitions"
    assert deep_metadict not in text


def test_cfb_human_include_metadict_adds_subtree_lines(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        baseline = "\n".join(cfb_human(f))
        full = "\n".join(cfb_human(f, include_metadict=True, collapse_threshold=10**9))
    # full output should be substantially larger
    assert len(full) > len(baseline) * 5


def test_collapse_repeats_summarizes_long_runs():
    entries = [
        {"name": f"Properties-9{{{i}}}", "kind": "stream", "byte_size": 32,
         "class_id": "abc", "path": f"/x/{i}"}
        for i in range(20)
    ]
    out = _collapse_repeats(entries, threshold=4)
    assert len(out) == 1
    assert out[0]["_type"] == "cfb_run_collapsed"
    assert out[0]["count"] == 20
    assert out[0]["first_name"] == "Properties-9{0}"
    assert out[0]["last_name"] == "Properties-9{19}"


def test_collapse_repeats_keeps_short_runs():
    entries = [
        {"name": "a", "kind": "stream", "byte_size": 1, "class_id": "x", "path": "/a"},
        {"name": "b", "kind": "stream", "byte_size": 2, "class_id": "y", "path": "/b"},
    ]
    # threshold high enough that nothing collapses
    out = _collapse_repeats(entries, threshold=8)
    assert out == entries


def test_collapse_repeats_distinguishes_shapes():
    entries = []
    for i in range(20):
        entries.append({
            "name": f"A-{i}", "kind": "stream", "byte_size": 32,
            "class_id": "x", "path": f"/A-{i}",
        })
    for i in range(20):
        entries.append({
            "name": f"B-{i}", "kind": "stream", "byte_size": 64,
            "class_id": "y", "path": f"/B-{i}",
        })
    out = _collapse_repeats(entries, threshold=4)
    assert len(out) == 2
    assert out[0]["count"] == 20
    assert out[1]["count"] == 20


def test_walk_cfb_default_skips_metadict(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        paths_visited = [p for p, _, _ in walk_cfb(f)]
    assert any("/" + METADICT_NAME in p for p in paths_visited) is False


def test_walk_cfb_include_metadict_visits_subtree(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        paths_visited = [p for p, _, _ in walk_cfb(f, include_metadict=True)]
    assert any(p.startswith("/" + METADICT_NAME) for p in paths_visited)


def test_read_stream_bytes_lazy_with_offset_and_length(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        # Find a stream we can target
        target = None
        for _, _, streams in walk_cfb(f, include_metadict=True):
            for st in streams:
                if st["byte_size"] >= 32:
                    target = st["path"]
                    break
            if target:
                break
        assert target is not None
        data, info = read_stream_bytes(f, target, offset=0, length=8)
    assert len(data) == 8
    assert info["offset"] == 0
    assert info["read_length"] == 8
    assert info["total_size"] >= 32
    assert info["truncated"] is True


def test_read_stream_bytes_unknown_path_raises(minimal_aaf):
    import pytest

    with aaf2.open(str(minimal_aaf), "r") as f:
        with pytest.raises(FileNotFoundError):
            read_stream_bytes(f, "/no/such/stream")


def test_format_hex_view_layout():
    data = bytes(range(0, 18))  # 18 bytes -> two rows
    rows = format_hex_view(data)
    assert len(rows) == 2
    assert rows[0].offset == 0x00
    assert rows[1].offset == 0x10
    # First row contains all 16 bytes; second row contains 2 bytes.
    assert rows[0].hex_bytes.count(" ") == 15
    assert rows[1].hex_bytes.count(" ") == 1


def test_format_hex_view_base_offset_shift():
    data = b"abcd"
    rows = format_hex_view(data, base_offset=0x100)
    assert rows[0].offset == 0x100
    assert rows[0].ascii_bytes == "abcd"


def test_render_hex_lines_classic_format():
    data = bytes(range(0, 18))
    lines = render_hex_lines(format_hex_view(data))
    assert lines[0].startswith("00000000  ")
    assert lines[1].startswith("00000010  ")
    # Short final row still pads hex column so ASCII pipes align.
    assert lines[0].endswith("|")
    assert "|" in lines[0]
    # Hex column is padded so the ASCII pipe lines up across rows
    # (even when the final row is short).
    assert lines[0].index("|") == lines[1].index("|")
