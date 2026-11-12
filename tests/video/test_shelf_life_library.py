"""Shelf Life on the library page: an admin's cards carry their score or gate
and the job's threshold, and the library sorts lowest score first. members get
none of it, and their sort=shelf_life quietly falls back to title."""

from __future__ import annotations

import pytest

from database.video_database import VideoDatabase


def _movie(db, title, year=2000):
    return db.upsert_movie("plex", {"server_id": title, "title": title, "year": year})


def _score(db, results):
    db.shelf_life_save("movie", [{"item_id": i, "score": s, "gate": g, "signals": {}, "reasons": [], "size_bytes": 1}
                                 for i, s, g in results])


@pytest.fixture()
def library(tmp_path, monkeypatch):
    db = VideoDatabase(database_path=str(tmp_path / "lib.db"))
    ids = {t: _movie(db, t) for t in ("Alien", "Brazil", "Cube", "Dune")}
    # Alien 70, Brazil 12, Cube gated, Dune never scored
    _score(db, [(ids["Alien"], 70, None), (ids["Brazil"], 12, None), (ids["Cube"], None, "keep")])
    return db, ids


def _titles(result):
    return [it["title"] for it in result["items"]]


def test_the_library_sorts_lowest_shelf_life_first(library):
    db, _ = library
    r = db.query_library("movies", sort="shelf_life", server_source="plex")
    # scored lowest first, then gated, then never scored
    assert _titles(r) == ["Brazil", "Alien", "Cube", "Dune"]


def _client(db, monkeypatch, admin, enabled=True):
    from flask import Flask, g

    import api.video as videoapi
    import core.video.sources as sources
    import core.video.repair.worker as worker_mod
    videoapi._video_db = db
    worker_mod._worker = worker_mod.VideoRepairWorker(db)
    worker_mod._worker.set_job_config("shelf_life", enabled=enabled)
    monkeypatch.setattr(sources, "resolve_video_server", lambda: "plex")
    app = Flask(__name__)

    @app.before_request
    def _who():
        g.is_admin = admin
        g.profile_id = 1 if admin else 2

    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client(), worker_mod


@pytest.fixture()
def routes(library, monkeypatch):
    import api.video as videoapi
    made = []

    def _make(admin, enabled=True):
        c, wm = _client(library[0], monkeypatch, admin, enabled)
        made.append(wm)
        return c
    yield _make
    for wm in made:
        if wm._worker:
            wm._worker.stop()
        wm._worker = None
    videoapi._video_db = None


def test_an_admin_gets_each_cards_score_or_gate_and_the_threshold(library, routes):
    c = routes(True)
    body = c.get("/api/video/library?kind=movies&sort=shelf_life").get_json()
    assert _titles(body) == ["Brazil", "Alien", "Cube", "Dune"]
    by = {it["title"]: it.get("shelf_life") for it in body["items"]}
    assert by["Brazil"] == {"score": 12, "gate": None, "gate_text": None}
    assert by["Cube"]["gate"] == "keep" and by["Cube"]["gate_text"]
    assert by["Dune"] is None                     # never scored: no badge
    assert body["shelf_life_threshold"] == 30


def test_the_threshold_follows_the_jobs_setting(library, routes):
    c = routes(True)
    from core.video.repair.worker import get_video_repair_worker
    w = get_video_repair_worker()
    w.set_job_config("shelf_life", settings={"threshold": 45})
    assert c.get("/api/video/library?kind=movies").get_json()["shelf_life_threshold"] == 45


def test_an_admin_gets_nothing_until_the_job_has_scored_that_kind(tmp_path, monkeypatch, routes):
    # a fresh library (the job never ran) answers an admin exactly as it
    # answers anyone else: no threshold, so no Shelf Life sort, no badges
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "fresh.db"))
    _movie(db, "Alien")
    videoapi._video_db = db
    c, _ = _client(db, monkeypatch, True)
    body = c.get("/api/video/library?kind=movies").get_json()
    assert "shelf_life_threshold" not in body
    assert all("shelf_life" not in it for it in body["items"])
    # scored movies say nothing about shows
    _score(db, [(db.upsert_movie("plex", {"server_id": "x", "title": "Brazil"}), 12, None)])
    assert "shelf_life_threshold" in c.get("/api/video/library?kind=movies").get_json()
    assert "shelf_life_threshold" not in c.get("/api/video/library?kind=shows").get_json()


def test_the_job_switched_off_shows_nothing(library, routes):
    # stored scores stay, but nothing refreshes them, so nothing is shown:
    # no badges, no shields, no threshold and so no Shelf Life sort
    c = routes(True, enabled=False)
    body = c.get("/api/video/library?kind=movies").get_json()
    assert "shelf_life_threshold" not in body
    assert all("shelf_life" not in it for it in body["items"])
    from core.video.repair.worker import get_video_repair_worker
    get_video_repair_worker().set_job_config("shelf_life", enabled=True)
    body = c.get("/api/video/library?kind=movies").get_json()
    assert body["shelf_life_threshold"] == 30
    assert {it["title"]: it.get("shelf_life") for it in body["items"]}["Brazil"]["score"] == 12


def test_a_member_gets_no_shelf_life_and_no_shelf_life_sort(library, routes):
    c = routes(False)
    body = c.get("/api/video/library?kind=movies&sort=shelf_life").get_json()
    assert _titles(body) == ["Alien", "Brazil", "Cube", "Dune"]    # title order
    assert "shelf_life_threshold" not in body
    assert all("shelf_life" not in it for it in body["items"])
