"""Tests for aafbrowser.core.aaf — object graph walker with cycle detection."""
from __future__ import annotations

import json

import aaf2

from aafbrowser.core.aaf import serialize_object, walk_human


def test_serialize_object_emits_typed_envelope(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        out = serialize_object(f.content)
    assert out["_type"] == "aaf_object"
    assert out["class"] == "ContentStorage"
    assert out["_cycle"] is False
    assert "properties" in out
    # ContentStorage exposes a Mobs property; should be a list of nested objects
    mobs = out["properties"].get("Mobs")
    assert isinstance(mobs, list)
    assert len(mobs) >= 1
    first = mobs[0]
    assert first["_type"] == "aaf_object"
    assert first["class"] == "MasterMob"
    assert first["mob_id"] is not None
    assert first["mob_id"].startswith("urn:smpte:umid:")
    assert first["name"] == "FixtureMob"


def test_serialize_object_full_dump_is_json_serializable(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        out = serialize_object(f.content)
    # Must round-trip through json without losing anything
    s = json.dumps(out)
    again = json.loads(s)
    assert again["class"] == "ContentStorage"


def test_serialize_object_descends_into_slot_segment(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        out = serialize_object(f.content)
    mob = out["properties"]["Mobs"][0]
    slot = mob["properties"]["Slots"][0]
    assert slot["_type"] == "aaf_object"
    segment = slot["properties"]["Segment"]
    assert segment["_type"] == "aaf_object"
    # EditRate should be a rational envelope
    edit_rate = slot["properties"]["EditRate"]
    assert edit_rate["_type"] == "rational"
    assert edit_rate["num"] == 48000


def test_serialize_object_weakref_does_not_recurse_into_dictionary(minimal_aaf):
    """
    The Sequence inside a slot has a DataDefinition WeakRef → DataDef_Sound.
    That dictionary entry must NOT be expanded as a nested aaf_object — it
    should appear as a `_type: weakref` envelope.
    """
    with aaf2.open(str(minimal_aaf), "r") as f:
        out = serialize_object(f.content)

    seg = out["properties"]["Mobs"][0]["properties"]["Slots"][0]["properties"]["Segment"]
    data_def = seg["properties"].get("DataDefinition")
    assert data_def is not None
    assert data_def["_type"] == "weakref"
    assert data_def["target_class"] == "DataDef"
    # Whatever name pyaaf2 gives it
    assert isinstance(data_def["target_name"], str)


def test_serialize_object_cycle_detection_does_not_infinite_loop():
    """
    Build an object pair that StrongRefs each other and confirm the walker
    emits a cycle marker on the second visit. We use lightweight fakes so we
    don't have to coerce pyaaf2 into a cyclic state. Class names match the
    real pyaaf2 names so the dispatch in serialize_property fires correctly.
    """

    class StrongRefProperty:
        def __init__(self, name, value):
            self.name = name
            self.value = value

    class Node:
        def __init__(self, name):
            self.name = name
            self.mob_id = None
            self._props: list = []

        def properties(self):
            return iter(self._props)

    a = Node("A")
    b = Node("B")
    a._props.append(StrongRefProperty("Child", b))
    b._props.append(StrongRefProperty("Back", a))

    out = serialize_object(a)
    back = out["properties"]["Child"]["properties"]["Back"]
    assert back["_type"] == "aaf_object_cycle"
    assert back["class"] == "Node"


def test_walk_human_renders_nested_text(minimal_aaf):
    with aaf2.open(str(minimal_aaf), "r") as f:
        lines = walk_human(f.content)
    text = "\n".join(lines)
    assert "ContentStorage" in text
    assert "MasterMob" in text
    assert "'FixtureMob'" in text
    assert "Slots" in text


def test_walk_human_emits_cycle_marker_text():
    class StrongRefProperty:
        def __init__(self, name, value):
            self.name = name
            self.value = value

    class Node:
        def __init__(self, name):
            self.name = name
            self.mob_id = None
            self._props: list = []

        def properties(self):
            return iter(self._props)

    a = Node("A")
    b = Node("B")
    a._props.append(StrongRefProperty("Child", b))
    b._props.append(StrongRefProperty("Back", a))

    lines = walk_human(a)
    joined = "\n".join(lines)
    assert "<cycle: Node" in joined
