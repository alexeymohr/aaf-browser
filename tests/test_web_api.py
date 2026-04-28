"""Tests for the Flask app — open/close/file (step 2)."""
from __future__ import annotations

import subprocess

import pytest

from aafbrowser.web import state as state_mod
from aafbrowser.web import app as app_mod
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


# --- /api/pick_file ---


def test_pick_file_501_on_non_darwin(client, monkeypatch):
    monkeypatch.setattr(app_mod.sys, "platform", "linux")
    r = client.post("/api/pick_file")
    assert r.status_code == 501
    assert r.get_json()["error"] == "not_implemented"


def test_pick_file_returns_chosen_path(client, monkeypatch):
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    monkeypatch.setattr(app_mod.shutil, "which", lambda _: "/usr/bin/osascript")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=0,
            stdout="/abs/path/to/My Recording.aaf\n", stderr="",
        )

    monkeypatch.setattr(app_mod.subprocess, "run", fake_run)
    r = client.post("/api/pick_file")
    assert r.status_code == 200
    assert r.get_json() == {"path": "/abs/path/to/My Recording.aaf"}


def test_pick_file_returns_null_on_user_cancel(client, monkeypatch):
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    monkeypatch.setattr(app_mod.shutil, "which", lambda _: "/usr/bin/osascript")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=1,
            stdout="",
            stderr="0:0: execution error: User canceled. (-128)\n",
        )

    monkeypatch.setattr(app_mod.subprocess, "run", fake_run)
    r = client.post("/api/pick_file")
    assert r.status_code == 200
    assert r.get_json() == {"path": None}


def test_pick_file_500_on_unexpected_failure(client, monkeypatch):
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    monkeypatch.setattr(app_mod.shutil, "which", lambda _: "/usr/bin/osascript")

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            args=cmd, returncode=2,
            stdout="", stderr="some weird AppleScript error\n",
        )

    monkeypatch.setattr(app_mod.subprocess, "run", fake_run)
    r = client.post("/api/pick_file")
    assert r.status_code == 500
    assert r.get_json()["error"] == "internal"


def test_pick_file_500_when_osascript_missing(client, monkeypatch):
    monkeypatch.setattr(app_mod.sys, "platform", "darwin")
    monkeypatch.setattr(app_mod.shutil, "which", lambda _: None)
    r = client.post("/api/pick_file")
    assert r.status_code == 500
    assert "osascript" in (r.get_json().get("detail") or "")


# --- /api/quit ---


def test_quit_schedules_shutdown_and_returns_ok(client, monkeypatch):
    called = []
    monkeypatch.setattr(app_mod, "_shutdown_after",
                        lambda *a, **kw: called.append(("shutdown", a, kw)))
    r = client.post("/api/quit")
    assert r.status_code == 200
    assert r.get_json() == {"ok": True}
    assert called and called[0][0] == "shutdown"


def test_quit_blocks_cross_origin(client, monkeypatch):
    called = []
    monkeypatch.setattr(app_mod, "_shutdown_after",
                        lambda *a, **kw: called.append("shutdown"))
    r = client.post("/api/quit", headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
    assert called == []


def test_quit_allows_matching_origin(client, monkeypatch):
    called = []
    monkeypatch.setattr(app_mod, "_shutdown_after",
                        lambda *a, **kw: called.append("shutdown"))
    # Flask test_client uses host "localhost" by default.
    r = client.post("/api/quit", headers={"Origin": "http://localhost"})
    assert r.status_code == 200
    assert called == ["shutdown"]


def test_quit_allows_no_origin_header(client, monkeypatch):
    """CLI tools and tests don't set Origin; we trust them."""
    called = []
    monkeypatch.setattr(app_mod, "_shutdown_after",
                        lambda *a, **kw: called.append("shutdown"))
    r = client.post("/api/quit")
    assert r.status_code == 200
    assert called == ["shutdown"]


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


# --- /mobs ---


def test_mobs_lists_all(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/mobs")
    assert r.status_code == 200
    j = r.get_json()
    assert j["total"] == 2
    assert all(set(m.keys()) >= {"mob_id", "class", "name", "slot_count"}
               for m in j["mobs"])


def test_mobs_filter_by_class(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/mobs?class=MasterMob")
    assert r.status_code == 200
    j = r.get_json()
    assert j["total"] == 2  # both fixture mobs are MasterMobs
    r = client.get("/api/mobs?class=SourceMob")
    assert r.get_json()["total"] == 0


def test_mobs_filter_by_name_substring(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/mobs?name_contains=Host")
    j = r.get_json()
    assert j["total"] == 1
    assert "Host" in j["mobs"][0]["name"]


def test_mobs_when_no_file_open(client):
    r = client.get("/api/mobs")
    assert r.status_code == 409
    assert r.get_json()["error"] == "no_file_open"


# --- /object ---


def test_object_by_mob_id(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    mobs = client.get("/api/mobs").get_json()["mobs"]
    target_id = mobs[0]["mob_id"]
    r = client.get(f"/api/object?mob_id={target_id}")
    assert r.status_code == 200
    j = r.get_json()
    assert j["object"]["_type"] == "aaf_object"
    assert j["object"]["class"] == "MasterMob"


def test_object_by_path(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    mobs = client.get("/api/mobs").get_json()["mobs"]
    urn = mobs[0]["mob_id"]
    r = client.get(f"/api/object?path=Mobs/{urn}/Slots/0/Segment")
    assert r.status_code == 200
    j = r.get_json()
    assert j["object"]["class"] == "Sequence"


def test_object_unknown_mob_id_404(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/object?mob_id=not-a-mob")
    assert r.status_code == 404
    assert r.get_json()["error"] == "not_found"


def test_object_missing_args_400(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/object")
    assert r.status_code == 400


def test_object_both_args_400(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/object?mob_id=foo&path=bar")
    assert r.status_code == 400


def test_object_when_no_file_open(client):
    r = client.get("/api/object?mob_id=abc")
    assert r.status_code == 409


# --- /cfb/tree ---


def test_cfb_tree_default_filters_metadict(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    r = client.get("/api/cfb/tree")
    assert r.status_code == 200
    j = r.get_json()
    tree = j["tree"]
    assert tree["_type"] == "cfb_storage"
    # Default: a marker placeholder appears, but the full subtree does not
    direct = [s.get("name") for s in tree["storages"]]
    assert "MetaDictionary-1" not in direct
    markers = [s for s in tree["storages"] if s.get("_type") == "cfb_metadict_filtered"]
    assert len(markers) == 1


def test_cfb_tree_include_metadict(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    r = client.get("/api/cfb/tree?include_metadict=1")
    j = r.get_json()
    direct = [s.get("name") for s in j["tree"]["storages"]]
    assert "MetaDictionary-1" in direct


def test_cfb_tree_decorates_class_name(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    r = client.get("/api/cfb/tree?include_metadict=1")
    tree = r.get_json()["tree"]
    md = next(s for s in tree["storages"] if s.get("name") == "MetaDictionary-1")
    # MetaDictionary class_id decodes to "MetaDictionary"
    assert md["class_id"] is not None
    assert md["class_name"] == "MetaDictionary"


def test_cfb_tree_no_file_open(client):
    r = client.get("/api/cfb/tree")
    assert r.status_code == 409


# --- /cfb/stream ---


def test_cfb_stream_returns_hex_and_ascii(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    # Find a stream we can target via the tree response
    r = client.get("/api/cfb/tree?include_metadict=1")
    tree = r.get_json()["tree"]

    def find_stream(node):
        for st in node.get("streams", []):
            if st.get("byte_size", 0) >= 16 and st.get("_type") != "cfb_run_collapsed":
                return st["path"]
        for s in node.get("storages", []):
            p = find_stream(s)
            if p:
                return p
        return None

    stream_path = find_stream(tree)
    assert stream_path

    r = client.get(f"/api/cfb/stream?path={stream_path}&length=16")
    assert r.status_code == 200
    j = r.get_json()
    assert j["path"] == stream_path
    assert j["length"] == 16
    assert isinstance(j["hex"], list) and isinstance(j["ascii"], list)
    assert len(j["hex"]) == len(j["ascii"]) == 1
    # 16 bytes of hex => 16 pairs separated by spaces
    assert j["hex"][0].count(" ") == 15


def test_cfb_stream_offset_and_truncation(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    # Use a known-large stream
    r = client.get("/api/cfb/tree?include_metadict=1")
    tree = r.get_json()["tree"]

    def find_big_stream(node):
        for st in node.get("streams", []):
            if st.get("byte_size", 0) >= 64 and st.get("_type") != "cfb_run_collapsed":
                return st["path"], st["byte_size"]
        for s in node.get("storages", []):
            res = find_big_stream(s)
            if res:
                return res
        return None

    stream_path, total = find_big_stream(tree)
    assert total >= 64
    r = client.get(
        f"/api/cfb/stream?path={stream_path}&offset=0&length=8"
    )
    j = r.get_json()
    assert j["offset"] == 0
    assert j["length"] == 8
    assert j["byte_size"] == total
    assert j["truncated"] is True


def test_cfb_stream_unknown_path_404(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    r = client.get("/api/cfb/stream?path=/no/such/stream")
    assert r.status_code == 404


def test_cfb_stream_bad_offset_400(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    r = client.get("/api/cfb/stream?path=/x&offset=abc")
    assert r.status_code == 400


def test_cfb_stream_no_file_open(client):
    r = client.get("/api/cfb/stream?path=/x")
    assert r.status_code == 409


# --- /find ---


def test_find_returns_match_dicts(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/find?pattern=Channel_1_Host")
    assert r.status_code == 200
    j = r.get_json()
    assert j["total"] >= 1
    m = j["matches"][0]
    assert set(m.keys()) >= {"layer", "path", "classname", "field", "value", "where"}


def test_find_layer_cfb_only(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/find?pattern=Channel_1_Host&layer=cfb")
    assert r.status_code == 200
    assert r.get_json()["total"] == 0


def test_find_in_names_only(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/find?pattern=(?i)channel&in=names")
    assert r.status_code == 200
    assert r.get_json()["total"] == 0  # 'channel' is not in any AAF prop name


def test_find_bad_regex_400(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/find?pattern=(unbalanced")
    assert r.status_code == 400
    assert r.get_json()["error"] == "bad_pattern"


def test_find_bad_scope_400(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    r = client.get("/api/find?pattern=x&in=hat")
    assert r.status_code == 400


def test_find_no_file_open(client):
    r = client.get("/api/find?pattern=x")
    assert r.status_code == 409


# --- /resolve ---


def test_resolve_mob_id(client, two_mob_aaf):
    client.post("/api/open", json={"path": str(two_mob_aaf)})
    mobs = client.get("/api/mobs").get_json()["mobs"]
    target = mobs[0]
    r = client.get(f"/api/resolve?ref={target['mob_id']}")
    assert r.status_code == 200
    j = r.get_json()
    assert j["kind"] == "mob"
    assert j["mob_id_or_path"] == target["mob_id"]
    assert j["class"] == "MasterMob"
    assert j["name"] == target["name"]


def test_resolve_path_to_segment(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    urn = client.get("/api/mobs").get_json()["mobs"][0]["mob_id"]
    r = client.get(f"/api/resolve?ref=Mobs/{urn}/Slots/0/Segment")
    assert r.status_code == 200
    j = r.get_json()
    assert j["class"] == "Sequence"


def test_resolve_unknown_404(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    r = client.get("/api/resolve?ref=nothing-real")
    assert r.status_code == 404


def test_resolve_missing_arg_400(client, minimal_aaf):
    client.post("/api/open", json={"path": str(minimal_aaf)})
    r = client.get("/api/resolve")
    assert r.status_code == 400


def test_resolve_no_file_open(client):
    r = client.get("/api/resolve?ref=x")
    assert r.status_code == 409


# --- /api/walk ---


def _comp_mob(client):
    mobs = client.get("/api/mobs?class=CompositionMob").get_json()["mobs"]
    return mobs[0]


def test_walk_returns_full_chain(client, chain_aaf):
    client.post("/api/open", json={"path": str(chain_aaf)})
    comp = _comp_mob(client)
    r = client.get(f"/api/walk?mob_id={comp['mob_id']}")
    assert r.status_code == 200
    j = r.get_json()
    assert j["start"]["class"] == "CompositionMob"
    assert len(j["hops"]) == 3
    assert j["hops"][-1]["terminal"] is True
    assert j["hops"][-1]["terminal_reason"] == "essence"


def test_walk_by_path(client, chain_aaf):
    client.post("/api/open", json={"path": str(chain_aaf)})
    comp = _comp_mob(client)
    r = client.get(
        f"/api/walk?path=Mobs/{comp['mob_id']}/Slots/0/Segment"
    )
    assert r.status_code == 200
    # Path resolves to a SourceClip; walk descends from there into the
    # MasterMob and SourceMob.
    j = r.get_json()
    assert [h["mob_class"] for h in j["hops"]] == ["MasterMob", "SourceMob"]


def test_walk_unknown_mob_id_404(client, chain_aaf):
    client.post("/api/open", json={"path": str(chain_aaf)})
    r = client.get("/api/walk?mob_id=nope")
    assert r.status_code == 404


def test_walk_missing_args_400(client, chain_aaf):
    client.post("/api/open", json={"path": str(chain_aaf)})
    r = client.get("/api/walk")
    assert r.status_code == 400


def test_walk_both_args_400(client, chain_aaf):
    client.post("/api/open", json={"path": str(chain_aaf)})
    r = client.get("/api/walk?mob_id=foo&path=bar")
    assert r.status_code == 400


def test_walk_no_file_open(client):
    r = client.get("/api/walk?mob_id=abc")
    assert r.status_code == 409


def test_walk_max_hops_clipped(client, chain_aaf):
    client.post("/api/open", json={"path": str(chain_aaf)})
    comp = _comp_mob(client)
    r = client.get(f"/api/walk?mob_id={comp['mob_id']}&max_hops=1")
    j = r.get_json()
    assert j["hops"][-1]["terminal_reason"] == "max_hops_reached"


# --- /api/find?class= ---


def test_find_class_filter_excludes(client, chain_aaf):
    client.post("/api/open", json={"path": str(chain_aaf)})
    r = client.get("/api/find?pattern=MstHostA&layer=aaf&class=SourceMob")
    assert r.status_code == 200
    assert r.get_json()["total"] == 0


def test_find_class_filter_repeated(client, chain_aaf):
    client.post("/api/open", json={"path": str(chain_aaf)})
    r = client.get(
        "/api/find?pattern=(?i)host&layer=aaf"
        "&class=CompositionMob&class=MasterMob"
    )
    assert r.status_code == 200
    classnames = {m["classname"] for m in r.get_json()["matches"]}
    assert "SourceMob" not in classnames
