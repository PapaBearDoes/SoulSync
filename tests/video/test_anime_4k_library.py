"""Anime 4K: the anime folder's 4K sibling, beside TV 4K.

Off until its switch is on AND a folder is set, like the other 4K folders. An
anime show on a 4K quality profile files there; with it off, anime keeps the
anime folder. Without an anime folder there is no anime library at all, so a
4K anime show goes where any 4K show goes (TV 4K).

Covers: the settings helpers, the pure routing, the drain fallback, the
default lookup, the folder settings round-trip, and the guard that every
place listing the TV 4K folder lists the Anime 4K one too (roots, the import
monitor, the drive map, health).
"""

from __future__ import annotations

import pytest
from flask import Flask

from core.video.download_pipeline import target_dir_for
from core.video.library_roots import four_k_path, library_roots, output_paths
from database.video_database import VideoDatabase


@pytest.fixture()
def db(tmp_path):
    vdb = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    vdb.set_setting("movies_path", "/media/movies")
    vdb.set_setting("tv_path", "/media/tv")
    vdb.set_setting("anime_path", "/media/anime")
    return vdb


def _on(db, *, tv_4k=None, anime_4k=None):
    if tv_4k is not None:
        db.set_setting("tv_4k_enabled", "1")
        db.set_setting("tv_4k_path", tv_4k)
    if anime_4k is not None:
        db.set_setting("anime_4k_enabled", "1")
        db.set_setting("anime_4k_path", anime_4k)


# ── settings helpers ──────────────────────────────────────────────────────────

def test_anime_4k_is_off_by_default(db):
    assert four_k_path(db, "anime") == ""
    assert output_paths(db)["anime_4k_path"] == ""


def test_anime_4k_needs_both_the_switch_and_a_path(db):
    db.set_setting("anime_4k_path", "/media/anime-4k")      # path, switch off
    assert four_k_path(db, "anime") == ""
    db.set_setting("anime_4k_enabled", "1")
    assert four_k_path(db, "anime") == "/media/anime-4k"
    db.set_setting("anime_4k_path", "   ")                  # switch on, no path
    assert four_k_path(db, "anime") == ""


def test_no_anime_folder_means_no_anime_4k(db):
    """the anime folder's 4K sibling: off while no anime folder is set,
    whatever its own switch and folder say"""
    from core.video.drive_map import _configured_roles
    from core.video.health import collect
    _on(db, anime_4k="/media/anime-4k")
    db.set_setting("anime_path", "")
    assert four_k_path(db, "anime") == ""
    assert output_paths(db)["anime_4k_path"] == ""
    assert "/media/anime-4k" not in library_roots(db, "episode")
    assert "/media/anime-4k" not in {p for p, _label in _configured_roles(db)}
    assert "4K anime library" not in {c["label"] for c in collect(db)["checks"]}
    db.set_setting("anime_path", "/media/anime")             # back on with the anime folder
    assert four_k_path(db, "anime") == "/media/anime-4k"


def test_anime_4k_is_a_tv_root_only_while_it_is_on(db):
    _on(db, anime_4k="/media/anime-4k")
    assert "/media/anime-4k" in library_roots(db, "episode")
    assert "/media/anime-4k" not in library_roots(db, "movie")
    db.set_setting("anime_4k_enabled", "0")
    assert "/media/anime-4k" not in library_roots(db, "episode")


# ── pure routing ──────────────────────────────────────────────────────────────

PATHS = {"movies_path": "/movies", "tv_path": "/tv", "anime_path": "/anime",
         "movies_4k_path": "/movies-4k", "tv_4k_path": "/tv-4k", "anime_4k_path": "/anime-4k"}


@pytest.mark.parametrize("kind,series_type,library,want", [
    ("show", "anime", "4k", "/anime-4k"),
    ("episode", "anime", "4K", "/anime-4k"),        # case-insensitive
    ("show", "anime", "main", "/anime"),
    ("show", "anime", None, "/anime"),
    ("show", "standard", "4k", "/tv-4k"),           # only anime goes to Anime 4K
    ("movie", None, "4k", "/movies-4k"),
])
def test_target_dir_for_routes_anime_4k_by_profile(kind, series_type, library, want):
    assert target_dir_for(kind, PATHS, series_type, library) == want


def test_anime_4k_off_keeps_anime_in_the_anime_folder():
    assert target_dir_for("show", dict(PATHS, anime_4k_path=""), "anime", "4k") == "/anime"


def test_anime_4k_without_an_anime_folder_is_unused():
    # no anime folder, no anime library: a 4K anime show is a 4K show
    assert target_dir_for("show", dict(PATHS, anime_path=""), "anime", "4k") == "/tv-4k"


# ── the drain, when the libraries can't be read ──────────────────────────────

def _fallback(item, *, anime_4k_root):
    from core.automation.handlers.video_process_wishlist import item_target_dir
    return item_target_dir(item, "episode", "/tv", "/anime", "/tv-4k",
                           lambda it: it.get("route_series_type"),
                           lambda it: it.get("lib") or "main",
                           anime_4k_root=anime_4k_root)


def test_fallback_files_4k_anime_in_anime_4k():
    anime = {"show_tmdb_id": 1, "route_series_type": "anime"}
    assert _fallback(dict(anime, lib="4k"), anime_4k_root="/anime-4k") == "/anime-4k"
    assert _fallback(dict(anime, lib="main"), anime_4k_root="/anime-4k") == "/anime"
    # off: anime keeps its folder, as it always did
    assert _fallback(dict(anime, lib="4k"), anime_4k_root="") == "/anime"
    # not anime: Anime 4K never takes it
    assert _fallback({"show_tmdb_id": 2, "lib": "4k"}, anime_4k_root="/anime-4k") == "/tv-4k"


def test_default_anime_4k_dir(db, monkeypatch):
    import api.video as videoapi
    from core.automation.handlers.video_process_wishlist import _default_anime_4k_dir
    monkeypatch.setattr(videoapi, "get_video_db", lambda: db)
    assert _default_anime_4k_dir("episode") == ""
    _on(db, anime_4k="/media/anime-4k")
    assert _default_anime_4k_dir("episode") == "/media/anime-4k"
    assert _default_anime_4k_dir("movie") == ""
    monkeypatch.setattr(videoapi, "get_video_db",
                        lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    assert _default_anime_4k_dir("episode") == ""


# ── the anime libraries file by the caller's folders, like the 4K one ───────

def test_the_anime_libraries_route_by_the_callers_folders(db, monkeypatch):
    import api.video as videoapi
    from core.automation.handlers.video_process_wishlist import (
        _default_pick_library, item_target_dir)
    from core.video.quality_profile import save_named
    monkeypatch.setattr(videoapi, "_video_db", db)
    _on(db, anime_4k="/media/anime-4k")
    uhd = save_named(db, None, "UHD", {"library": "4k"})["id"]
    db.set_show_type_hint(77, "anime", source="user")
    ep = {"show_tmdb_id": 77, "show_title": "S", "season_number": 1, "episode_number": 1,
          "route_series_type": "anime"}

    def route(item, anime_4k_root):
        return item_target_dir(item, "episode", "/x/tv", "/x/anime", "/x/tv-4k",
                               lambda it: it.get("route_series_type"),
                               lambda it: "4k" if it.get("quality_profile_id") == uhd else "main",
                               _default_pick_library, anime_4k_root=anime_4k_root)
    assert route(dict(ep), "/x/anime-4k") == "/x/anime"
    assert route(dict(ep, quality_profile_id=uhd), "/x/anime-4k") == "/x/anime-4k"
    # a caller with no Anime 4K folder of its own files it in its anime folder
    assert route(dict(ep, quality_profile_id=uhd), "") == "/x/anime"


# ── the drain and RSS sync, when the libraries can't be read ────────────────
# the only time their own anime folders decide (F10: a wishlist grab still
# files, by the folders, the way it did before libraries)

ANIME_4K = "/media/anime-4k"


@pytest.fixture()
def locked(db, monkeypatch):
    """Anime 4K on, a 4K Default profile, an anime show wished, and the
    library rows unreadable"""
    import sqlite3

    import api.video as videoapi
    import core.video.anime_hint as ah
    import core.video.libraries as libraries
    from core.video.quality_profile import load, save
    monkeypatch.setattr(videoapi, "_video_db", db)
    monkeypatch.setattr(ah, "default_suggest", lambda _t: False)
    _on(db, anime_4k=ANIME_4K)
    save(db, dict(load(db), library="4k"))
    db.set_show_type_hint(123542, "anime", source="user")
    db.add_episodes_to_wishlist(123542, "Link Click", [
        {"season_number": 4, "episode_number": 12, "air_date": "2026-10-22"}])

    def unreadable(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(libraries, "_rows", unreadable)
    return db


def test_the_drain_files_4k_anime_in_anime_4k_without_the_libraries(locked, monkeypatch):
    from core.automation.handlers import video_process_wishlist as vpw
    grabs = []
    vpw.auto_video_process_wishlist(
        {"_automation_id": "a", "max_concurrent": 1, "max_simultaneous_downloads": 5},
        _Deps(), media_type="episode",
        active_keys=lambda mt: set(), target_dir=lambda mt: "/media/tv",
        search=lambda it, mt: [{"filename": "f", "title": "r", "username": "u", "size_bytes": 1,
                                "quality_label": "WEBDL-2160p", "accepted": True, "score": 10}],
        enqueue=lambda item, best, cands, mt, root: grabs.append(root) or True)
    assert grabs == [ANIME_4K]


def test_rss_sync_files_4k_anime_in_anime_4k_without_the_libraries(locked, monkeypatch):
    import core.video.rss_sync as rss
    from core.automation.handlers import video_process_wishlist as vpw
    from core.video import download_events
    from core.video.download_config import save
    download_events._reset_for_tests()
    grabs = []
    monkeypatch.setattr(vpw, "_default_target_dir", lambda mt: "/media/tv")
    monkeypatch.setattr(vpw, "_default_active_keys", lambda mt: set())
    monkeypatch.setattr(vpw, "_default_enqueue",
                        lambda item, best, cands, mt, root: grabs.append(root) or True)
    save(locked, {"download_mode": "torrent"})
    hit = {"title": "Link Click S04E12 1080p WEB-DL", "size_bytes": 1_400_000_000,
           "seeders": 50, "peers": 5, "username": "indexer", "availability": 50,
           "filename": "Link Click S04E12 1080p WEB-DL", "files": [], "file_count": 0,
           "folder_size_bytes": 1_400_000_000, "download_url": "http://idx/dl/lc",
           "protocol": "torrent", "indexer_id": 1, "guid": "g-lc"}
    out = rss.rss_pass(fetch=lambda: [hit])
    assert out["grabbed"] == 1, out
    assert grabs == [ANIME_4K]


class _Deps:
    def update_progress(self, automation_id, **kw):
        pass


# ── the folder settings ───────────────────────────────────────────────────────

@pytest.fixture()
def client(db, monkeypatch):
    import api.video as videoapi
    monkeypatch.setattr(videoapi, "_video_db", db)
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    return app.test_client()


def test_folder_settings_round_trip_anime_4k(client):
    cfg = client.get("/api/video/downloads/config").get_json()
    assert cfg["anime_4k_enabled"] is False and cfg["anime_4k_path"] == ""
    client.post("/api/video/downloads/config",
                json={"anime_4k_enabled": True, "anime_4k_path": "  /data/anime-4k  "})
    cfg = client.get("/api/video/downloads/config").get_json()
    assert cfg["anime_4k_enabled"] is True and cfg["anime_4k_path"] == "/data/anime-4k"
    # a save without the keys leaves them alone
    client.post("/api/video/downloads/config", json={"tv_path": "/media/tv2"})
    cfg = client.get("/api/video/downloads/config").get_json()
    assert cfg["anime_4k_enabled"] is True and cfg["anime_4k_path"] == "/data/anime-4k"


# ── everywhere TV 4K is listed, Anime 4K is too ──────────────────────────────

def _roots_seen(db, monkeypatch, tmp_path):
    """the folders each surface lists, with TV 4K and Anime 4K both on"""
    import api.video as videoapi
    import core.video.download_monitor as mon
    from core.video.drive_map import _configured_roles
    from core.video.health import collect
    tv_4k, anime_4k = tmp_path / "tv-4k", tmp_path / "anime-4k"   # neither exists
    _on(db, tv_4k=str(tv_4k), anime_4k=str(anime_4k))
    monkeypatch.setattr(videoapi, "get_video_db", lambda: db)
    unreachable = {c["label"] for c in collect(db)["checks"] if c["status"] == "error"}
    return {
        "library_roots": set(library_roots(db, "episode")),
        "output_paths": set(output_paths(db).values()),
        "monitor": mon._library_roots(),
        "drive_map": {path for path, _label in _configured_roles(db)},
    }, unreachable, (str(tv_4k), str(anime_4k))


@pytest.mark.parametrize("surface", ["library_roots", "output_paths", "monitor", "drive_map"])
def test_every_surface_with_tv_4k_has_anime_4k(db, monkeypatch, tmp_path, surface):
    import os
    seen, _unreachable, (tv_4k, anime_4k) = _roots_seen(db, monkeypatch, tmp_path)
    got = {os.path.abspath(p) for p in seen[surface]} if surface == "monitor" else seen[surface]
    want = {os.path.abspath(tv_4k), os.path.abspath(anime_4k)} if surface == "monitor" else {tv_4k, anime_4k}
    assert want <= got


def test_health_checks_the_anime_4k_folder(db, monkeypatch, tmp_path):
    _seen, unreachable, _paths = _roots_seen(db, monkeypatch, tmp_path)
    assert {"4K TV library", "4K anime library"} <= unreachable


def test_stale_temp_reaper_watches_the_anime_4k_folder(db, tmp_path):
    import os
    import time

    import core.video.download_monitor as mon
    anime_4k = tmp_path / "anime-4k"
    anime_4k.mkdir()
    stale = anime_4k / "Ep.mkv.tmp.abcdef12"       # the importer's '.tmp.<8 hex>' name
    stale.write_bytes(b"x")
    old = time.time() - 3 * 3600
    os.utime(stale, (old, old))
    _on(db, anime_4k=str(anime_4k))
    assert mon._sweep_import_temps(db) >= 1
    assert not stale.exists()


def test_drive_map_names_the_anime_4k_folder(db):
    from core.video.drive_map import _configured_roles
    _on(db, anime_4k="/media/anime-4k")
    assert ("/media/anime-4k", "your 4K anime folder") in _configured_roles(db)
