"""Tests for aafbrowser.core.serialize."""
from __future__ import annotations

import base64
import datetime as _dt

import pytest

from aaf2.auid import AUID
from aaf2.mobid import MobID
from aaf2.rational import AAFRational

from aafbrowser.core.serialize import (
    DEFAULT_BYTES_PREVIEW_LIMIT,
    serialize_property,
    serialize_scalar,
    weakref_target_info,
)


# --- scalars ----------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [None, True, False, 0, 1, -42, "hello", "", 3.14, 0.0],
)
def test_primitive_passthrough(value):
    assert serialize_scalar(value) == value


def test_datetime_envelope():
    dt = _dt.datetime(2026, 4, 27, 12, 34, 56)
    out = serialize_scalar(dt)
    assert out == {"_type": "datetime", "value": "2026-04-27T12:34:56"}


def test_rational_envelope_keeps_num_den_and_float_value():
    out = serialize_scalar(AAFRational(48000, 1001))
    assert out["_type"] == "rational"
    assert out["num"] == 48000
    assert out["den"] == 1001
    assert out["value"] == pytest.approx(48000 / 1001)


def test_rational_integer_value_is_float():
    out = serialize_scalar(AAFRational(25, 1))
    assert out["_type"] == "rational"
    assert out["num"] == 25
    assert out["den"] == 1
    assert out["value"] == pytest.approx(25.0)


def test_auid_envelope():
    auid = AUID("01030202-0200-0000-060e-2b3404010101")
    out = serialize_scalar(auid)
    assert out == {"_type": "auid", "value": "01030202-0200-0000-060e-2b3404010101"}


def test_mobid_envelope_uses_urn_form():
    mid = MobID.new()
    out = serialize_scalar(mid)
    assert out["_type"] == "mobid"
    assert out["value"].startswith("urn:smpte:umid:")


def test_bytes_short_payload_full_base64():
    data = b"\x00\x01\x02hello"
    out = serialize_scalar(data)
    assert out["_type"] == "bytes"
    assert out["length"] == len(data)
    assert out["truncated"] is False
    assert base64.b64decode(out["base64"]) == data


def test_bytes_truncated_by_default_when_over_limit():
    data = b"x" * (DEFAULT_BYTES_PREVIEW_LIMIT + 100)
    out = serialize_scalar(data)
    assert out["length"] == len(data)
    assert out["truncated"] is True
    assert out["preview_length"] == DEFAULT_BYTES_PREVIEW_LIMIT
    assert len(base64.b64decode(out["base64"])) == DEFAULT_BYTES_PREVIEW_LIMIT


def test_bytes_full_when_full_bytes_flag():
    data = b"x" * (DEFAULT_BYTES_PREVIEW_LIMIT + 100)
    out = serialize_scalar(data, full_bytes=True)
    assert out["truncated"] is False
    assert base64.b64decode(out["base64"]) == data


def test_unknown_type_emits_envelope_and_warning(capsys):
    class Custom:
        def __repr__(self):
            return "<Custom>"

    with pytest.warns(UserWarning, match="unknown value type"):
        out = serialize_scalar(Custom())
    assert out["_type"] == "unknown"
    assert "Custom" in out["python_type"]
    err = capsys.readouterr().err
    assert "unknown value type" in err


# --- weakref target ---------------------------------------------------------


def test_weakref_target_info_extracts_class_name_auid():
    class FakeTarget:
        name = "DataDef_Sound"
        auid = AUID("01030202-0200-0000-060e-2b3404010101")

    info = weakref_target_info(FakeTarget())
    assert info["_type"] == "weakref"
    assert info["target_class"] == "FakeTarget"
    assert info["target_name"] == "DataDef_Sound"
    assert info["target_auid"] == "01030202-0200-0000-060e-2b3404010101"


def test_weakref_target_info_handles_missing_name_and_auid():
    class Bare:
        pass

    info = weakref_target_info(Bare())
    assert info["_type"] == "weakref"
    assert info["target_class"] == "Bare"
    assert info["target_name"] is None
    assert info["target_auid"] is None


# --- serialize_property dispatch -------------------------------------------


class _FakeProp:
    """Minimal Property-shaped object whose class name drives dispatch."""

    def __init__(self, name, value, kind):
        self.name = name
        self.value = value
        # Override class name via a subclass — see _make_prop
        raise AssertionError("use _make_prop")


def _make_prop(kind: str, value):
    cls = type(kind, (), {})
    obj = cls()
    obj.value = value
    obj.name = f"Test{kind}"
    return obj


def test_serialize_property_scalar_property_passthrough():
    prop = _make_prop("Property", 42)
    assert serialize_property(prop, recurse=lambda x: x) == 42


def test_serialize_property_strong_ref_calls_recurse():
    sentinel = object()
    captured = []

    def recurse(obj):
        captured.append(obj)
        return {"_type": "aaf_object", "class": "X"}

    prop = _make_prop("StrongRefProperty", sentinel)
    out = serialize_property(prop, recurse=recurse)
    assert out == {"_type": "aaf_object", "class": "X"}
    assert captured == [sentinel]


def test_serialize_property_strong_ref_none_value():
    prop = _make_prop("StrongRefProperty", None)
    assert serialize_property(prop, recurse=lambda x: x) is None


def test_serialize_property_strong_ref_vector_recurses_each():
    items = ["a", "b", "c"]
    prop = _make_prop("StrongRefVectorProperty", items)
    out = serialize_property(prop, recurse=lambda v: f"R({v})")
    assert out == ["R(a)", "R(b)", "R(c)"]


def test_serialize_property_strong_ref_set_recurses_each_in_iteration_order():
    items = iter(["x", "y", "z"])
    prop = _make_prop("StrongRefSetProperty", items)
    out = serialize_property(prop, recurse=lambda v: v.upper())
    assert out == ["X", "Y", "Z"]


def test_serialize_property_weakref_does_not_recurse():
    class Target:
        name = "Foo"
        auid = AUID("01030202-0200-0000-060e-2b3404010101")

    target = Target()
    called = []
    prop = _make_prop("WeakRefProperty", target)
    out = serialize_property(prop, recurse=lambda x: called.append(x) or x)
    assert called == []
    assert out["_type"] == "weakref"
    assert out["target_class"] == "Target"


def test_serialize_property_weakref_none_value():
    prop = _make_prop("WeakRefProperty", None)
    assert serialize_property(prop, recurse=lambda x: x) is None


def test_serialize_property_passes_full_bytes_flag_to_scalar():
    big = b"x" * (DEFAULT_BYTES_PREVIEW_LIMIT + 50)
    prop = _make_prop("Property", big)
    out = serialize_property(prop, recurse=lambda x: x, full_bytes=True)
    assert out["truncated"] is False
    assert out["length"] == len(big)
