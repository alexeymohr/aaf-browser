"""
Read-only invariant test for the web layer.

Open a per-test copy of the fixture via the API, exercise every
endpoint, close — and assert the SHA-256 of the input file is unchanged
throughout. This is the same defense-in-depth that test_readonly.py
provides for the CLI.
"""
from __future__ import annotations

import hashlib

import aaf2
import pytest

from aafbrowser.web import state as state_mod
from aafbrowser.web.app import create_app


def _sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


@pytest.fixture
def client():
    app = create_app()
    app.testing = True
    with app.test_client() as c:
        yield c
    with state_mod.state_lock():
        state_mod.close_file()


def _stream_path_with_some_bytes(aaf_path: str) -> str:
    with aaf2.open(aaf_path, "r") as f:
        for _, _, streams in f.cfb.walk():
            for st in streams:
                if st.byte_size >= 8:
                    return st.path()
    raise AssertionError("no stream of size >= 8 in fixture")


def test_every_endpoint_leaves_input_byte_identical(client, minimal_aaf_copy):
    aaf_path = str(minimal_aaf_copy)
    initial = _sha256(aaf_path)

    # Hash never changes from any sequence of GET/POST.
    def check(label):
        post = _sha256(aaf_path)
        assert post == initial, f"file mutated after {label}"

    # Open
    r = client.post("/api/open", json={"path": aaf_path})
    assert r.status_code == 200
    check("open")

    # File metadata
    r = client.get("/api/file")
    assert r.status_code == 200
    check("file")

    # Mobs index + filters
    r = client.get("/api/mobs")
    mobs = r.get_json()["mobs"]
    assert mobs
    check("mobs")
    client.get("/api/mobs?class=MasterMob")
    client.get("/api/mobs?name_contains=Fixture")
    check("mobs-filtered")

    # Object by mob_id and by path
    urn = mobs[0]["mob_id"]
    r = client.get(f"/api/object?mob_id={urn}")
    assert r.status_code == 200
    check("object-by-mob-id")
    r = client.get(f"/api/object?path=Mobs/{urn}/Slots/0/Segment")
    assert r.status_code == 200
    check("object-by-path")

    # CFB tree default and include-metadict
    client.get("/api/cfb/tree")
    client.get("/api/cfb/tree?include_metadict=1")
    check("cfb-tree")

    # CFB stream hex (lazy read)
    sp = _stream_path_with_some_bytes(aaf_path)
    client.get(f"/api/cfb/stream?path={sp}&offset=0&length=8")
    check("cfb-stream")

    # Find — both layers, both scopes
    client.get("/api/find?pattern=(?i)mob")
    client.get("/api/find?pattern=(?i)mob&layer=aaf&in=names")
    client.get("/api/find?pattern=(?i)mob&layer=cfb")
    check("find")

    # Resolve by mob-id and by path
    client.get(f"/api/resolve?ref={urn}")
    client.get(f"/api/resolve?ref=Mobs/{urn}/Slots/0/Segment")
    check("resolve")

    # Close
    r = client.post("/api/close")
    assert r.status_code == 200
    check("close")

    # And one more re-open to confirm sequencing doesn't drift the file
    client.post("/api/open", json={"path": aaf_path})
    client.post("/api/close")
    check("reopen-cycle")
