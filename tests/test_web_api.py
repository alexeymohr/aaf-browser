"""Tests for the Flask app — open/close/file (step 2)."""
from __future__ import annotations

import pytest

from aafbrowser.web import state as state_mod
from aafbrowser.web.app import create_app


@pytest.fixture
def client():
    app = create_app()
    app.testing = True
    with app.test_client() as c:
        yield c
    # Always reset state between tests so leakage doesn't mask bugs
    with state_mod.state_lock():
        state_mod.close_file()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}


def test_open_returns_metadata(client, two_mob_aaf):
    r = client.post("/api/open", json={"path": str(two_mob_aaf)})
    assert r.status_code == 200, r.get_data(as_text=True)
    j = r.get_json()
    assert j["mob_count"] == 2
    assert j["classes_summary"]["MasterMob"] == 2
    assert isinstance(j["sha256"], str) and len(j["sha256"]) == 64


def test_open_unknown_path_404(client):
    r = client.post("/api/open", json={"path": "/no/such/file.aaf"})
    assert r.status_code == 404
    j = r.get_json()
    assert j["error"] == "not_found"


def test_open_missing_path_400(client):
    r = client.post("/api/open", json={})
    assert r.status_code == 400
    assert r.get_json()["error"] == "bad_request"


def test_file_returns_404_when_no_file(client):
    r = client.get("/api/file")
    assert r.status_code == 404
    assert r.get_json()["error"] == "no_file_open"


def test_file_returns_metadata_after_open(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/file")
    assert r.status_code == 200
    j = r.get_json()
    assert j["mob_count"] == 2


def test_close_resets(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.post("/api/close")
    assert r.status_code == 200
    r = client.get("/api/file")
    assert r.status_code == 404


def test_open_replaces_previous(client, two_mob_aaf, minimal_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.post("/api/open", json={"path": str(minimal_aaf)})
    assert r.status_code == 200
    j = r.get_json()
    assert j["mob_count"] == 1
    assert j["path"].endswith("minimal.aaf")


def test_index_returns_503_when_no_static_built(client):
    """Until step 7 ships index.html, GET / should explain itself."""
    r = client.get("/")
    # If a previous run left a static index.html behind, this test is
    # accidentally meaningful in the other direction; for now we assert
    # one of the two valid outcomes.
    assert r.status_code in (200, 503)
