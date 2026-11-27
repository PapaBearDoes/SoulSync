"""where a tv grab lands and which library's settings it uses, every case.

the golden routing table: each scenario runs through every grab path with
nothing patched but the network --

* get / manual search (api/video/downloads ``_target_for`` + ``_library_for``)
* the wishlist drain and rss sync: a real wishlist row read back through
  ``episode_wishlist_to_download`` (so the routing type and quality profile
  are the ones the query works out), filed by ``item_target_dir``
* wishlist search now (``grab_target_dir`` on that same row) and repair
  grabs (``grab_target_dir`` on the item ``repair/grab.grab_episode`` builds)

and asserts the folder and the library whose settings apply (default
quality profile, indexers, sources, category, automatic search) for each.
a show is anime three ways: by a stored hint (a show in no library), filed
in a plex library ticked anime, filed in a jellyfin library ticked anime.

before the anime library an anime show's FILE went to the anime folder
while its SETTINGS came from whatever library the order picked with anime
ignored (the tv default, a custom default, tv 4k, a rule library): the rows
in ``WAS_SETTINGS_FROM_TV_SIDE``. now both come from the Anime library (or
Anime 4K), and nothing else moves.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import api.video as videoapi
from api.video import downloads as dl
from core.automation.handlers import video_process_wishlist as vpw
from core.video import libraries
from core.video.quality_profile import save_named
from database.video_database import VideoDatabase

TV, TV_4K, ANIME, ANIME_4K = "/media/tv", "/media/tv-4k", "/media/anime", "/media/anime-4k"
KIDS_TV, ARCHIVE = "/media/kids-tv", "/media/tv-archive"

GENRES: dict = {}   # tmdb id -> genres tmdb would give


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """tmdb is never asked: genres come from GENRES, and a show in no
    library is anime only by a hint a test stores"""
    import core.video.anime_hint as ah
    import core.video.enrichment.engine as engine

    def _never(_tmdb):
        raise AssertionError("TMDB must not be asked here")
    monkeypatch.setattr(ah, "default_suggest", _never)

    class _Engine:
        def title_identity(self, kind, tmdb):
            return {"genres": list(GENRES.get(int(tmdb), [])), "keywords": []}
    monkeypatch.setattr(engine, "get_video_enrichment_engine", lambda: _Engine())
    GENRES.clear()
    yield
    videoapi._video_db = None


def _world(tmp_path, *, anime_folder, tv_4k=False, kids_rule=False, archive_default=False, anime_4k=False):
    """an install: tv, the anime folder (or not), tv 4k on (or off), anime 4k
    on (or off), a kids tv library with a genre rule, a tv archive library
    made the default. both servers list the same two show libraries, anime
    ticked as anime."""
    db = VideoDatabase(database_path=str(tmp_path / "video_library.db"))
    db.set_setting("movies_path", "/media/movies")
    db.set_setting("tv_path", TV)
    db.set_setting("anime_path", ANIME if anime_folder else "")
    db.set_setting("tv_4k_path", TV_4K)
    db.set_setting("tv_4k_enabled", "1" if tv_4k else "0")
    db.set_setting("anime_4k_path", ANIME_4K)
    db.set_setting("anime_4k_enabled", "1" if anime_4k else "0")
    for server in ("plex", "jellyfin"):
        db.set_library_selection(server, "Movies", "TV Shows", tv_more=["Anime"], anime=["Anime"])
    ids = {"uhd": save_named(db, None, "UHD", {"library": "4k"})["id"]}
    if kids_rule:
        ids["kids"] = libraries.create_library(db, {"kind": "show", "name": "Kids TV", "path": KIDS_TV,
                                                    "rules": {"genres": ["Kids"]}})["id"]
    if archive_default:
        ids["archive"] = libraries.create_library(db, {"kind": "show", "name": "TV Archive", "path": ARCHIVE,
                                                       "is_default": True})["id"]
    builtin = {x["builtin"]: x["id"] for x in libraries.list_libraries(db, "show") if x["builtin"]}
    ids.update(tv=builtin["main"], anime=builtin["anime"], anime4k=builtin["anime4k"])
    videoapi._video_db = db
    return db, ids


def _show(db, ids, tmdb, *, anime, mark, profile_4k=False, genres=(), assign=None):
    """a show the way each marking gives it: ``hint`` (in no library, typed
    by a stored hint), ``plex`` / ``jellyfin`` (filed by that server's scan in
    its Anime library, or its TV Shows library when not anime)"""
    GENRES[tmdb] = list(genres)
    if mark == "hint":
        db.set_show_type_hint(tmdb, "anime" if anime else "standard", "user")
    else:
        db.upsert_show_tree(mark, {"server_id": "%s-%s" % (mark, tmdb), "title": "Show %s" % tmdb,
                                   "tmdb_id": tmdb, "genres": list(genres),
                                   "library": "Anime" if anime else "TV Shows",
                                   "seasons": [{"season_number": 1, "episodes": [
                                       {"episode_number": 1, "title": "E1"}]}]})
    if profile_4k:
        db.set_title_profile_choice("show", tmdb, ids["uhd"])
    if assign:
        libraries.assign_title(db, "show", tmdb, ids[assign])


def _routes(db, tmdb, *, pick=None):
    """{path: (folder, library name, why)} for every grab path"""
    body = {"media_source": "tmdb", "media_id": tmdb, "tmdb_id": tmdb, "title": "Show %s" % tmdb}
    if pick:
        body["video_library_id"] = pick
    lib = dl._library_for(db, "show", body)
    out = {"get": (dl._target_for(db, "show", body), lib and lib["name"], lib and lib["why"])}
    if pick:
        return out   # a pick is made in the get / manual search window only
    db.add_episodes_to_wishlist(tmdb, "Show %s" % tmdb, [{"season_number": 1, "episode_number": 1}])
    row = next(r for r in db.episode_wishlist_to_download(due_only=False) if r["show_tmdb_id"] == tmdb)
    item = dict(row)
    pick_lib = vpw._default_pick_library(dict(item), "episode", lambda: vpw._default_profile_library(item))
    folder = vpw.item_target_dir(dict(item), "episode", TV, db.get_setting("anime_path") or "",
                                 vpw._default_four_k_dir("episode"), vpw._default_route_type,
                                 vpw._default_profile_library, vpw._default_pick_library,
                                 anime_4k_root=vpw._default_anime_4k_dir("episode"))
    out["drain"] = (folder, pick_lib and pick_lib["name"], pick_lib and pick_lib["why"])
    # wishlist search now: the same row, through grab_target_dir
    out["search"] = (vpw.grab_target_dir(dict(item), "episode", TV), out["drain"][1], out["drain"][2])
    # a repair grab: the item core/video/repair/grab.grab_episode builds
    from core.video.repair.grab import _title_profile_id
    repair = {"show_tmdb_id": tmdb, "show_title": "Show %s" % tmdb, "season_number": 1, "episode_number": 1,
              "episode_title": None, "quality_profile_id": _title_profile_id("show", tmdb_id=tmdb)}
    pick_lib = vpw._default_pick_library(dict(repair), "episode", lambda: vpw._default_profile_library(repair))
    out["repair"] = (vpw.grab_target_dir(dict(repair), "episode", TV), pick_lib and pick_lib["name"],
                     pick_lib and pick_lib["why"])
    return out


# ── the table ────────────────────────────────────────────────────────────────
# (row, world, show, expected (folder, library, why)) -- row numbers are the
# vault's Anime Library Routing Table. "world": anime folder, tv 4k, kids rule,
# archive default. "show": anime, 4k profile, genres, assigned to, picked.

W = dict  # readability

# before the anime library these rows filed in the anime folder but took
# their profile, indexers, sources, category and automatic search from
# (6, 8) TV, (7, 9) TV Archive, (10) TV 4K, (13) Kids TV
WAS_SETTINGS_FROM_TV_SIDE = {6, 7, 8, 9, 10, 13}

ROWS = [
    # with an anime folder
    (1, W(anime_folder=True), W(anime=False), (TV, "TV", "default")),
    (2, W(anime_folder=True, archive_default=True), W(anime=False), (ARCHIVE, "TV Archive", "default")),
    (3, W(anime_folder=True), W(anime=False, profile_4k=True), (TV, "TV", "default")),
    (4, W(anime_folder=True, archive_default=True), W(anime=False, profile_4k=True), (ARCHIVE, "TV Archive", "default")),
    (5, W(anime_folder=True, tv_4k=True), W(anime=False, profile_4k=True), (TV_4K, "TV 4K", "profile")),
    (6, W(anime_folder=True), W(anime=True), (ANIME, "Anime", "anime")),
    (7, W(anime_folder=True, archive_default=True), W(anime=True), (ANIME, "Anime", "anime")),
    (8, W(anime_folder=True), W(anime=True, profile_4k=True), (ANIME, "Anime", "anime")),
    (9, W(anime_folder=True, archive_default=True), W(anime=True, profile_4k=True), (ANIME, "Anime", "anime")),
    (10, W(anime_folder=True, tv_4k=True), W(anime=True, profile_4k=True), (ANIME, "Anime", "anime")),
    (11, W(anime_folder=True), W(anime=True, assign="tv"), (TV, "TV", "assigned")),
    (12, W(anime_folder=True, kids_rule=True), W(anime=False, genres=["Kids"]), (KIDS_TV, "Kids TV", "rule: genre Kids")),
    (13, W(anime_folder=True, kids_rule=True), W(anime=True, genres=["Kids"]), (ANIME, "Anime", "anime")),
    (14, W(anime_folder=True, kids_rule=True), W(anime=True, pick="kids"), (KIDS_TV, "Kids TV", "picked")),
    (15, W(anime_folder=True, kids_rule=True), W(anime=True, assign="kids"), (KIDS_TV, "Kids TV", "assigned")),
    # without an anime folder: anime is filed like any show
    (16, W(anime_folder=False), W(anime=False), (TV, "TV", "default")),
    (17, W(anime_folder=False, archive_default=True), W(anime=False), (ARCHIVE, "TV Archive", "default")),
    (18, W(anime_folder=False), W(anime=False, profile_4k=True), (TV, "TV", "default")),
    (19, W(anime_folder=False, archive_default=True), W(anime=False, profile_4k=True), (ARCHIVE, "TV Archive", "default")),
    (20, W(anime_folder=False, tv_4k=True), W(anime=False, profile_4k=True), (TV_4K, "TV 4K", "profile")),
    (21, W(anime_folder=False), W(anime=True), (TV, "TV", "default")),
    (22, W(anime_folder=False, archive_default=True), W(anime=True), (ARCHIVE, "TV Archive", "default")),
    (23, W(anime_folder=False), W(anime=True, profile_4k=True), (TV, "TV", "default")),
    (24, W(anime_folder=False, archive_default=True), W(anime=True, profile_4k=True), (ARCHIVE, "TV Archive", "default")),
    (25, W(anime_folder=False, tv_4k=True), W(anime=True, profile_4k=True), (TV_4K, "TV 4K", "profile")),
    (26, W(anime_folder=False), W(anime=True, assign="tv"), (TV, "TV", "assigned")),
    (27, W(anime_folder=False, kids_rule=True), W(anime=False, genres=["Kids"]), (KIDS_TV, "Kids TV", "rule: genre Kids")),
    (28, W(anime_folder=False, kids_rule=True), W(anime=True, genres=["Kids"]), (KIDS_TV, "Kids TV", "rule: genre Kids")),
    (29, W(anime_folder=False, kids_rule=True), W(anime=True, pick="kids"), (KIDS_TV, "Kids TV", "picked")),
    (30, W(anime_folder=False, kids_rule=True), W(anime=True, assign="kids"), (KIDS_TV, "Kids TV", "assigned")),
    # anime 4k switched on: only an anime show on a 4k profile moves
    (31, W(anime_folder=True, anime_4k=True), W(anime=True), (ANIME, "Anime", "anime")),
    (32, W(anime_folder=True, anime_4k=True), W(anime=True, profile_4k=True), (ANIME_4K, "Anime 4K", "anime")),
    (33, W(anime_folder=True, anime_4k=True), W(anime=False, profile_4k=True), (TV, "TV", "default")),
    (33.1, W(anime_folder=True, anime_4k=True, tv_4k=True), W(anime=False, profile_4k=True), (TV_4K, "TV 4K", "profile")),
    (34, W(anime_folder=True), W(anime=False, assign="anime"), (ANIME, "Anime", "assigned")),
    (34.1, W(anime_folder=True), W(anime=False, pick="anime"), (ANIME, "Anime", "picked")),
    (35, W(anime_folder=True, anime_4k=True), W(anime=False, assign="anime4k"), (ANIME_4K, "Anime 4K", "assigned")),
    (35.1, W(anime_folder=True, anime_4k=True), W(anime=True, pick="anime4k"), (ANIME_4K, "Anime 4K", "picked")),
    # anime 4k on without an anime folder: no anime library, anime files as before (L3)
    (35.2, W(anime_folder=False, anime_4k=True, tv_4k=True), W(anime=True, profile_4k=True), (TV_4K, "TV 4K", "profile")),
]


@pytest.mark.parametrize("mark", ["hint", "plex", "jellyfin"])
@pytest.mark.parametrize("row,world,show,want", ROWS, ids=["row%04.1f" % r[0] for r in ROWS])
def test_every_path_files_and_reads_settings_like_the_table(tmp_path, mark, row, world, show, want):
    db, ids = _world(tmp_path, **world)
    show = dict(show)
    pick = show.pop("pick", None)
    tmdb = 500 + int(row * 10)
    _show(db, ids, tmdb, mark=mark, **show)
    got = _routes(db, tmdb, pick=ids[pick] if pick else None)
    for path, outcome in got.items():
        assert outcome == want, "row %s, %s, %s path: %r != %r" % (row, mark, path, outcome, want)


# ── the settings an anime show uses (F3) ──────────────────────────────────────

@pytest.mark.parametrize("mark", ["hint", "plex", "jellyfin"])
def test_an_anime_show_takes_every_library_setting_from_the_anime_library(tmp_path, mark):
    """files in the anime folder and searches and grabs with the Anime
    library's settings; tv's settings no longer reach it"""
    db, ids = _world(tmp_path, anime_folder=True)
    tv_profile = save_named(db, None, "TV 1080p", {"library": "main"})["id"]
    anime_profile = save_named(db, None, "Anime 1080p", {"library": "main"})["id"]
    libraries.update_library(db, ids["tv"], {"options": {
        "quality_profile_id": tv_profile, "sources": ["usenet"], "indexer_ids": [7],
        "torrent_category": "tv", "usenet_category": "tv", "auto_search": False, "upgrades": False}})
    libraries.update_library(db, ids["anime"], {"options": {
        "quality_profile_id": anime_profile, "sources": ["torrent"], "indexer_ids": [9],
        "torrent_category": "anime", "usenet_category": "anime"}})
    _show(db, ids, 700, anime=True, mark=mark)
    assert _routes(db, 700)["drain"] == (ANIME, "Anime", "anime")
    assert libraries.default_profile_for(db, "show", tmdb_id=700) == anime_profile
    item = dict(next(r for r in db.episode_wishlist_to_download(due_only=False) if r["show_tmdb_id"] == 700))
    assert vpw.item_indexer_ids(dict(item), "episode") == [9]
    assert vpw.item_source_chain(dict(item), "episode", ["torrent", "usenet"]) == ["torrent"]
    assert vpw.library_allows(dict(item), "episode", vpw._default_pick_library,
                              vpw._default_profile_library) is True
    # the category is read from the folder: the anime folder is the Anime library's
    assert libraries.client_category(db, ANIME, "usenet") == "anime"
    assert libraries.client_category(db, TV, "usenet") == "tv"
    # a show of another type keeps tv's
    _show(db, ids, 701, anime=False, mark=mark)
    assert libraries.default_profile_for(db, "show", tmdb_id=701) == tv_profile


def test_an_anime_show_in_the_anime_folder_is_in_the_anime_library_after_a_scan(tmp_path):
    """attribute_files stamps a show with the library its files sit in: a tv
    show gets tv, an anime show in the anime folder gets Anime"""
    db, ids = _world(tmp_path, anime_folder=True)
    for tmdb, lib, folder in ((209867, "Anime", ANIME + "/Frieren (2023)"), (95396, "TV Shows", TV + "/Severance (2022)")):
        _show(db, ids, tmdb, anime=lib == "Anime", mark="plex")
        conn = db._get_connection()
        try:
            ep = conn.execute("SELECT e.id FROM episodes e JOIN shows s ON s.id=e.show_id WHERE s.tmdb_id=?",
                              (tmdb,)).fetchone()[0]
            conn.execute("INSERT INTO media_files (episode_id, relative_path, size_bytes) VALUES (?,?,1)",
                         (ep, folder + "/Season 01/S01E01.mkv"))
            conn.commit()
        finally:
            conn.close()
    libraries.attribute_files(db)
    conn = db._get_connection()
    try:
        got = dict(conn.execute("SELECT tmdb_id, video_library_id FROM shows").fetchall())
    finally:
        conn.close()
    assert got == {209867: ids["anime"], 95396: ids["tv"]}


# ── how the type is known (F4, L2's three cases) ─────────────────────────────

def test_a_never_seen_anime_show_takes_the_anime_library_on_its_first_grab(tmp_path, monkeypatch):
    """the type is worked out (tmdb asked once) before the library is chosen,
    so the search's settings and the grab's folder agree from the first grab"""
    import core.video.anime_hint as ah
    db, ids = _world(tmp_path, anime_folder=True)
    asked = []
    monkeypatch.setattr(ah, "default_suggest", lambda t: asked.append(t) or True)
    got = _routes(db, 800)
    assert got["get"] == (ANIME, "Anime", "anime")
    assert got["drain"] == got["repair"] == (ANIME, "Anime", "anime")
    assert asked == [800]


def test_a_library_show_with_no_tmdb_id_is_judged_by_its_own_row(tmp_path):
    db, ids = _world(tmp_path, anime_folder=True)
    db.upsert_show_tree("plex", {"server_id": "p-x", "title": "Obscure OVA", "library": "Anime",
                                 "seasons": [{"season_number": 1, "episodes": [{"episode_number": 1}]}]})
    conn = db._get_connection()
    try:
        row_id = conn.execute("SELECT id FROM shows WHERE server_id='p-x'").fetchone()[0]
    finally:
        conn.close()
    body = {"media_source": "plex", "media_id": row_id, "title": "Obscure OVA"}
    assert dl._target_for(db, "show", body) == ANIME
    lib = dl._library_for(db, "show", body)
    assert (lib["name"], lib["why"]) == ("Anime", "anime")


def test_a_get_naming_the_show_by_tmdb_id_alone_files_where_its_library_says(tmp_path):
    """a body with tmdb_id but no media_id: the library still knows the stored
    type, and the folder follows the library (L6), never TV under Anime's settings"""
    db, ids = _world(tmp_path, anime_folder=True)
    db.set_show_type_hint(801, "anime", source="user")
    body = {"tmdb_id": 801, "title": "Show 801"}
    lib = dl._library_for(db, "show", body)
    assert (lib["name"], lib["why"]) == ("Anime", "anime")
    assert dl._target_for(db, "show", body) == ANIME


# ── a show re-keyed to the other server (F9) ─────────────────────────────────

@pytest.mark.parametrize("jf_anime,want", [("Anime", ANIME), ("Japanese Animation", TV)])
def test_a_show_rekeyed_to_jellyfin_stays_anime_only_by_the_same_library_name(tmp_path, jf_anime, want):
    """rekey_library moves the row to jellyfin and keeps its library_name:
    a jellyfin anime library with another name no longer reads it as anime"""
    from core.video import server_links
    db, ids = _world(tmp_path, anime_folder=True)
    db.set_library_selection("jellyfin", "Movies", "TV Shows", tv_more=[jf_anime], anime=[jf_anime])
    _show(db, ids, 900, anime=True, mark="plex")
    conn = db._get_connection()
    try:
        show_id = conn.execute("SELECT id FROM shows WHERE tmdb_id=900").fetchone()[0]
        conn.execute("INSERT INTO video_server_links (kind, item_id, server_source, server_id) "
                     "VALUES ('show', ?, 'jellyfin', 'jf-900')", (show_id,))
        conn.commit()
    finally:
        conn.close()
    assert server_links.rekey_library(db, "plex", "jellyfin")["shows"] == 1
    got = _routes(db, 900)
    assert {o[0] for o in got.values()} == {want}


# ── when the libraries can't be read (F10) ───────────────────────────────────

@pytest.mark.parametrize("anime_folder,tv_4k,anime,profile_4k,wishlist_folder", [
    (True, False, True, False, ANIME), (True, True, True, True, ANIME),
    (True, True, False, True, TV_4K), (False, True, True, True, TV_4K), (True, False, False, False, TV),
])
def test_unreadable_libraries_fall_back_on_the_wishlist_side_and_fail_a_get(
        tmp_path, monkeypatch, anime_folder, tv_4k, anime, profile_4k, wishlist_folder):
    db, ids = _world(tmp_path, anime_folder=anime_folder, tv_4k=tv_4k)
    _show(db, ids, 950, anime=anime, mark="plex", profile_4k=profile_4k)
    from core.video.repair.grab import _title_profile_id
    bare = {"show_tmdb_id": 950, "show_title": "Show 950", "season_number": 1, "episode_number": 1,
            "quality_profile_id": _title_profile_id("show", tmdb_id=950)}   # as a repair grab builds it

    def locked(*_a, **_k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(libraries, "_rows", locked)
    assert vpw.grab_target_dir(dict(bare), "episode", TV) == wishlist_folder
    body = {"media_source": "tmdb", "media_id": 950, "tmdb_id": 950, "title": "Show 950"}
    with pytest.raises(sqlite3.OperationalError):
        dl._target_for(db, "show", body)


def test_the_table_rows_are_numbered_once():
    nums = [r[0] for r in ROWS]
    assert nums == sorted(set(nums)) and WAS_SETTINGS_FROM_TV_SIDE <= set(nums)
    assert json.dumps(ROWS)   # plain data, easy to diff when a row changes


# ── an anime library has no rules and is never the default (L4, L5) ───────────

def test_rules_on_an_anime_library_are_never_kept_or_used(tmp_path):
    db, ids = _world(tmp_path, anime_folder=True)
    lib = libraries.update_library(db, ids["anime"], {"rules": {"genres": ["Animation"]}, "indexer_ids": [3]})
    assert lib["rules"] == {"genres": [], "keywords": []} and lib["options"]["indexer_ids"] == [3]
    # a rule slipped into the row by hand still sends no standard show there
    conn = db._get_connection()
    try:
        conn.execute("UPDATE video_libraries SET options=? WHERE id=?",
                     (json.dumps({"rules": {"genres": ["Animation"]}}), ids["anime"]))
        conn.commit()
    finally:
        conn.close()
    _show(db, ids, 1000, anime=False, mark="plex", genres=["Animation"])
    assert _routes(db, 1000)["get"] == (TV, "TV", "default")


def test_an_anime_library_can_never_be_the_default(tmp_path):
    db, ids = _world(tmp_path, anime_folder=True, anime_4k=True)
    for key in ("anime", "anime4k"):
        with pytest.raises(ValueError, match="anime shows only"):
            libraries.set_default(db, ids[key])
    # one made default in the row by hand is still passed over for a new show
    conn = db._get_connection()
    try:
        conn.execute("UPDATE video_libraries SET is_default = (id = ?) WHERE kind='show'", (ids["anime"],))
        conn.commit()
    finally:
        conn.close()
    _show(db, ids, 1001, anime=False, mark="plex")
    assert _routes(db, 1001)["get"] == (TV, "TV", "default")


def test_no_anime_folder_means_no_anime_library(tmp_path):
    db, ids = _world(tmp_path, anime_folder=False, anime_4k=True)
    libs = {x["builtin"]: x for x in libraries.list_libraries(db, "show") if x["builtin"]}
    assert libs["anime"]["usable"] is False and libs["anime"]["path"] == ""
    assert [x["builtin"] for x in libraries.list_libraries(db, "show")][:4] == ["main", "4k", "anime", "anime4k"]
    # no anime folder, no Anime 4K either: its own switch and folder don't make it usable
    assert libs["anime4k"]["usable"] is False and libs["anime4k"]["path"] == ANIME_4K
    # so even a hand pick can't file a show there
    _show(db, ids, 960, anime=True, mark="hint", profile_4k=True)
    got = _routes(db, 960, pick=ids["anime4k"])["get"]
    assert got[1] != "Anime 4K" and got[0] != ANIME_4K
    libraries.assign_title(db, "show", 960, ids["anime4k"])
    assert all(r[1] != "Anime 4K" and r[0] != ANIME_4K for r in _routes(db, 960).values())
