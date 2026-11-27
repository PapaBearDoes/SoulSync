"""what the anime libraries start with, and the way back to an older build.

the anime folder used to be no library: an anime show searched and grabbed
with whatever tv library the order picked (the tv default, most often). now
it is the Anime library, so on the day it first has a folder it takes the
settings anime was using -- the tv default's profile, sources, indexers,
automatic search / upgrade switches and its markers / chapter picture
switches; never rules, never a client category (an anime download had
none). Anime 4K takes TV 4K's the same way. once
each: switched off and on again they keep what the admin set.

an older build reads the two anime rows as 4K look-alikes, so a show picked
for Anime by hand would file into the TV 4K folder there. ROLLBACK_SQL is the
cleanup, run with the app stopped, before starting the older build.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from flask import Flask

import core.video.libraries as libs
from database.video_database import VideoDatabase

ROLLBACK_SQL = """
DELETE FROM video_title_libraries WHERE library_id IN (SELECT id FROM video_libraries WHERE builtin IN ('anime','anime4k'));
UPDATE video_wishlist  SET video_library_id = NULL WHERE video_library_id IN (SELECT id FROM video_libraries WHERE builtin IN ('anime','anime4k'));
UPDATE video_wishlist  SET filed_library_id = NULL WHERE filed_library_id IN (SELECT id FROM video_libraries WHERE builtin IN ('anime','anime4k'));
UPDATE video_downloads SET video_library_id = NULL WHERE video_library_id IN (SELECT id FROM video_libraries WHERE builtin IN ('anime','anime4k'));
UPDATE shows           SET video_library_id = NULL WHERE video_library_id IN (SELECT id FROM video_libraries WHERE builtin IN ('anime','anime4k'));
DELETE FROM video_libraries WHERE builtin IN ('anime','anime4k');
DELETE FROM video_settings  WHERE key IN ('anime_library_settings_copied', 'anime4k_library_settings_copied');
"""

# every copied switch set the other way from a new library's, so a key the
# copy misses shows up
TV_OPTIONS = {"quality_profile_id": 0, "sources": ["usenet", "torrent"], "indexer_ids": [4, 7],
              "torrent_category": "tv", "usenet_category": "tv-sab", "auto_search": True, "upgrades": False,
              "chapter_thumbs": False, "markers": False, "markers_recaps": True, "markers_write": False}
COPIED = ("quality_profile_id", "sources", "indexer_ids", "auto_search", "upgrades",
          "chapter_thumbs", "markers", "markers_recaps", "markers_write")


@pytest.fixture(autouse=True)
def _no_tmdb(monkeypatch):
    monkeypatch.setattr(libs, "title_tags", lambda db, kind, tmdb_id: {"genres": [], "keywords": []})
    import core.video.anime_hint as ah

    def _never(_tmdb):
        raise AssertionError("TMDB must not be asked here")
    monkeypatch.setattr(ah, "default_suggest", _never)


def _reopen(path):
    """a restart: the database is set up again (the upgrade runs)"""
    import database.video_database as vdbmod
    vdbmod._initialized_paths.discard(str(Path(path).resolve()))
    return VideoDatabase(database_path=str(path))


def _show_libs(db):
    return {x["builtin"] or x["name"]: x for x in libs.list_libraries(db, "show")}


def _before_the_upgrade(path, *, anime_folder=True, tv_options=TV_OPTIONS):
    """an install from before the anime libraries: its folders and tv
    library settings, and none of the two anime rows"""
    db = VideoDatabase(database_path=str(path))
    db.set_setting("tv_path", "/media/tv")
    db.set_setting("anime_path", "/media/anime" if anime_folder else "")
    from core.video.quality_profile import save_named
    pid = save_named(db, None, "TV 1080p", {"library": "main"})["id"]
    libs.update_library(db, _show_libs(db)["main"]["id"], {"options": dict(tv_options, quality_profile_id=pid)})
    conn = sqlite3.connect(str(path))
    conn.execute("DELETE FROM video_libraries WHERE builtin IN ('anime', 'anime4k')")
    conn.execute("DELETE FROM video_settings WHERE key LIKE 'anime%_settings_copied'")
    conn.commit()
    conn.close()
    return db, pid


# ── the upgrade ──────────────────────────────────────────────────────────────

def test_the_upgrade_gives_anime_the_tv_default_settings(tmp_path):
    path = tmp_path / "v.db"
    _before_the_upgrade(path)
    db = _reopen(path)
    lib = _show_libs(db)
    tv, anime = lib["main"]["options"], lib["anime"]["options"]
    for key in COPIED:
        assert anime[key] == tv[key], key
    # never a category: an anime download had none
    assert anime["torrent_category"] == "" and anime["usenet_category"] == ""
    assert anime["rules"] == {"genres": [], "keywords": []}
    assert db.get_setting("anime_library_settings_copied") == "1"
    # Anime 4K has no folder yet: nothing copied, no marker
    assert db.get_setting("anime4k_library_settings_copied") in (None, "")


def test_an_anime_show_searches_and_grabs_the_same_way_after_the_upgrade(tmp_path):
    """the settings it used before (tv's) are the ones it uses after (Anime's copy)"""
    from core.automation.handlers import video_process_wishlist as vpw
    import api.video as videoapi
    path = tmp_path / "v.db"
    db, pid = _before_the_upgrade(path)
    db.set_show_type_hint(42, "anime", "user")
    db.add_episodes_to_wishlist(42, "Frieren", [{"season_number": 1, "episode_number": 1}])

    def effective(d):
        videoapi._video_db = d
        try:
            item = dict(next(r for r in d.episode_wishlist_to_download(due_only=False) if r["show_tmdb_id"] == 42))
            return (libs.default_profile_for(d, "show", tmdb_id=42), vpw.item_indexer_ids(dict(item), "episode"),
                    vpw.item_source_chain(dict(item), "episode", ["soulseek"]),
                    vpw.library_allows(dict(item), "episode", vpw._default_pick_library, vpw._default_profile_library),
                    vpw.library_allows(dict(item, owned=True), "episode", vpw._default_pick_library,
                                       vpw._default_profile_library),
                    libs.client_category(d, "/media/anime", "usenet"), libs.client_category(d, "/media/anime", "torrent"))
        finally:
            videoapi._video_db = None
    before = effective(db)
    after_db = _reopen(path)
    after = effective(after_db)
    assert before == after == (pid, [4, 7], ["usenet", "torrent"], True, False, None, None)
    assert libs.choose(after_db, "show", tmdb_id=42)["builtin"] == "anime"


def test_markers_and_chapter_pictures_stay_off_for_anime_after_the_upgrade(tmp_path):
    """tv had markers and chapter pictures off, so an anime file had them off:
    the markers job reads the library the file is filed under (none before,
    which is tv's; Anime after), chapter pictures the library the title is
    moved to by hand. Both still say off once the file is in Anime"""
    from core.player import chapter_thumbs
    from core.video.markers import service as markers
    path = tmp_path / "v.db"
    db, _pid = _before_the_upgrade(path)
    db.upsert_show_tree("plex", {"server_id": "p-42", "title": "Frieren", "tmdb_id": 42, "library": "Anime",
                                 "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    conn = sqlite3.connect(str(path))
    ep = conn.execute("SELECT e.id FROM episodes e JOIN shows s ON s.id=e.show_id WHERE s.tmdb_id=42").fetchone()[0]
    file_id = conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes) VALUES (?,?,1)",
                           (ep, "/media/anime/Frieren (2023)/Season 01/S01E01.mkv")).lastrowid
    conn.commit()
    conn.close()

    def switches(d):
        libs.attribute_files(d)
        markers.forget_libraries()
        opts = markers.options_for(d, markers.file_ctx(d, file_id, resolve=False))
        thumbs = any(c["file_id"] == file_id for c in chapter_thumbs.candidates(d))
        return opts["markers"], opts["markers_recaps"], opts["markers_write"], thumbs
    before = switches(db)
    db = _reopen(path)
    anime_id = _show_libs(db)["anime"]["id"]
    libs.assign_title(db, "show", 42, anime_id)
    after = switches(db)
    assert markers.file_ctx(db, file_id, resolve=False).library_id == anime_id
    assert before == after == (False, True, False, False)


def test_a_custom_default_library_is_what_anime_copies(tmp_path):
    path = tmp_path / "v.db"
    db, _pid = _before_the_upgrade(path)
    archive = libs.create_library(db, {"kind": "show", "name": "TV Archive", "path": "/media/tv-archive",
                                       "is_default": True, "indexer_ids": [11], "sources": ["torrent"],
                                       "torrent_category": "archive"})
    db = _reopen(path)
    anime = _show_libs(db)["anime"]["options"]
    assert anime["indexer_ids"] == [11] and anime["sources"] == ["torrent"]
    assert anime["torrent_category"] == ""
    # the source library is never changed
    assert _show_libs(db)["TV Archive"]["options"] == libs.get_library(db, archive["id"])["options"]
    assert _show_libs(db)["TV Archive"]["options"]["torrent_category"] == "archive"


def test_an_install_with_no_anime_folder_copies_nothing(tmp_path):
    path = tmp_path / "v.db"
    _before_the_upgrade(path, anime_folder=False)
    db = _reopen(path)
    assert _show_libs(db)["anime"]["options"]["indexer_ids"] == []
    assert db.get_setting("anime_library_settings_copied") in (None, "")


# ── later: the folder set from Settings ──────────────────────────────────────

@pytest.fixture()
def client(tmp_path, monkeypatch):
    import api.video as videoapi
    db = VideoDatabase(database_path=str(tmp_path / "v.db"))
    db.set_setting("tv_path", "/media/tv")
    videoapi._video_db = db
    app = Flask(__name__)
    app.register_blueprint(videoapi.create_video_blueprint(), url_prefix="/api/video")
    yield app.test_client(), db
    videoapi._video_db = None


def test_an_anime_folder_set_months_later_copies_tv_as_it_is_then(client):
    c, db = client
    tv = _show_libs(db)["main"]
    libs.update_library(db, tv["id"], {"indexer_ids": [1]})
    libs.update_library(db, tv["id"], {"indexer_ids": [2, 3]})      # what tv uses by the time
    assert c.post("/api/video/downloads/config", json={"anime_path": "/media/anime"}).status_code == 200
    assert _show_libs(db)["anime"]["options"]["indexer_ids"] == [2, 3]


def test_switched_off_and_on_again_anime_keeps_its_own_settings(client):
    c, db = client
    c.post("/api/video/downloads/config", json={"anime_path": "/media/anime"})
    anime_id = _show_libs(db)["anime"]["id"]
    libs.update_library(db, anime_id, {"indexer_ids": [9], "usenet_category": "anime"})
    libs.update_library(db, _show_libs(db)["main"]["id"], {"indexer_ids": [5]})
    c.post("/api/video/downloads/config", json={"anime_path": ""})
    assert _show_libs(db)["anime"]["usable"] is False
    c.post("/api/video/downloads/config", json={"anime_path": "/media/anime"})
    anime = _show_libs(db)["anime"]["options"]
    assert anime["indexer_ids"] == [9] and anime["usenet_category"] == "anime"


def test_anime_4k_copies_tv_4k_the_first_time_it_is_switched_on(client):
    c, db = client
    c.post("/api/video/downloads/config", json={"anime_path": "/media/anime"})
    libs.update_library(db, _show_libs(db)["4k"]["id"], {"indexer_ids": [8], "sources": ["usenet"]})
    db.set_setting("anime_4k_path", "/media/anime-4k")
    db.set_setting("anime_4k_enabled", "1")
    assert libs.copy_anime_settings(db) == ["anime4k"]
    a4k = _show_libs(db)["anime4k"]["options"]
    assert a4k["indexer_ids"] == [8] and a4k["sources"] == ["usenet"]
    assert libs.copy_anime_settings(db) == []                       # once


def test_anime_4k_waits_for_the_anime_folder_before_it_copies(client):
    """no anime folder, no Anime 4K: switched on with its own folder it copies
    nothing until the anime folder is set, then copies TV 4K as it is then"""
    c, db = client
    libs.update_library(db, _show_libs(db)["4k"]["id"], {"indexer_ids": [8]})
    c.post("/api/video/downloads/config", json={"anime_4k_enabled": True, "anime_4k_path": "/media/anime-4k"})
    assert _show_libs(db)["anime4k"]["usable"] is False
    assert db.get_setting(libs.ANIME_COPY_MARKERS["anime4k"]) is None
    libs.update_library(db, _show_libs(db)["4k"]["id"], {"indexer_ids": [9]})
    c.post("/api/video/downloads/config", json={"anime_path": "/media/anime"})
    assert _show_libs(db)["anime4k"]["options"]["indexer_ids"] == [9]


# ── the way back: ROLLBACK_SQL ───────────────────────────────────────────────

def test_the_rollback_cleanup_leaves_nothing_an_older_build_can_misread(tmp_path):
    path = tmp_path / "v.db"
    db, _pid = _before_the_upgrade(path)
    libs.create_library(db, {"kind": "show", "name": "TV Archive", "path": "/media/tv-archive",
                             "is_default": True, "indexer_ids": [11], "markers": False, "chapter_thumbs": False})
    db = _reopen(path)
    db.set_setting("tv_4k_enabled", "1")
    db.set_setting("tv_4k_path", "/media/tv-4k")
    lib = _show_libs(db)
    archive_before = json.dumps(lib["TV Archive"]["options"], sort_keys=True)
    anime_id = lib["anime"]["id"]
    # everything that can point at an anime library: a hand pick, a picked
    # wishlist row, a download, a show filed there
    libs.assign_title(db, "show", 77, anime_id)
    db.add_episodes_to_wishlist(77, "Frieren", [{"season_number": 1, "episode_number": 1}])
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE video_wishlist SET video_library_id=?, filed_library_id=? WHERE tmdb_id=77",
                 (anime_id, anime_id))
    conn.execute("INSERT INTO shows (server_source, server_id, title, tmdb_id, video_library_id) "
                 "VALUES ('plex', 's77', 'Frieren', 77, ?)", (anime_id,))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(video_downloads)")}
    if {"kind", "title", "status", "video_library_id"} <= cols:
        conn.execute("INSERT INTO video_downloads (kind, title, status, video_library_id) "
                     "VALUES ('episode', 'Frieren', 'completed', ?)", (anime_id,))
    conn.commit()
    # run as the sqlite3 tool would: foreign keys off, so nothing cascades
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.executescript(ROLLBACK_SQL)
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM video_libraries")]
    assert anime_id not in ids
    assert conn.execute("SELECT COUNT(*) FROM video_libraries WHERE builtin IN ('anime','anime4k')").fetchone()[0] == 0
    for table, col in (("video_title_libraries", "library_id"), ("video_wishlist", "video_library_id"),
                       ("video_wishlist", "filed_library_id"), ("video_downloads", "video_library_id"),
                       ("shows", "video_library_id"), ("movies", "video_library_id")):
        left = conn.execute("SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL AND %s NOT IN "
                            "(SELECT id FROM video_libraries)" % (table, col, col)).fetchone()[0]
        assert left == 0, (table, col)
    assert conn.execute("SELECT COUNT(*) FROM video_settings WHERE key LIKE 'anime%_settings_copied'").fetchone()[0] == 0
    conn.close()
    # starting this build again after the cleanup is an upgrade again
    db = _reopen(path)
    lib = _show_libs(db)
    assert json.dumps(lib["TV Archive"]["options"], sort_keys=True) == archive_before
    # upgraded again: the rows are back and the copy runs again
    assert lib["anime"]["options"]["indexer_ids"] == [11]
    assert lib["anime"]["options"]["markers"] is False and lib["anime"]["options"]["chapter_thumbs"] is False
    assert db.get_setting("anime_library_settings_copied") == "1"
