"""Video library listing endpoint.

GET /api/video/library -> {"movies": [...], "shows": [...]}
Reads what the last scan mirrored from the media server into video.db.
"""

from __future__ import annotations

from flask import g, jsonify, request

from utils.logging_config import get_logger

logger = get_logger("video_api.library")


def _capped_library(db, cap, server):
    """the library page for a kids profile: same filters and sort, over-cap
    and unrated titles left out, paged after the filter so counts and pages
    stay honest. walks the filtered set in big pages; only restricted
    profiles ever come through here."""
    from core.video.watch_scope import current_watcher

    from .kids import filter_library_items
    kind = request.args.get("kind", "movies")
    try:
        page = max(1, int(request.args.get("page", 1) or 1))
        limit = max(1, min(500, int(request.args.get("limit", 75) or 75)))
    except (TypeError, ValueError):
        page, limit = 1, 75
    kept, p = [], 1
    while True:
        chunk = db.query_library(
            kind, search=request.args.get("search") or None,
            letter=request.args.get("letter") or None,
            sort=request.args.get("sort", "title"),
            status=request.args.get("status", "all"),
            genre=request.args.get("genre") or None,
            resolution=request.args.get("resolution") or None,
            page=p, limit=500, server_source=server, include_size=False,
            watcher=current_watcher().rows,
            video_library=request.args.get("video_library") or None)
        kept.extend(filter_library_items(db, chunk.get("items") or [], cap, kind))
        if not (chunk.get("pagination") or {}).get("has_next"):
            break
        p += 1
    total = len(kept)
    total_pages = max(1, (total + limit - 1) // limit)
    return {"items": kept[(page - 1) * limit: page * limit],
            "total_size_bytes": sum(int(it.get("size_bytes") or 0) for it in kept),
            "pagination": {"page": page, "total_pages": total_pages, "total_count": total,
                           "has_prev": page > 1, "has_next": page < total_pages}}


def _profile() -> int:
    """The requesting user's profile for per-profile watchlist reads."""
    try:
        from core.profile_context import get_current_profile_id
        return int(get_current_profile_id() or 1)
    except Exception:
        return 1


def _is_admin() -> bool:
    return bool(getattr(g, "is_admin", getattr(g, "profile_id", 1) == 1))


def _attach_shelf_life(db, kind: str, result: dict) -> None:
    """an admin's library page shows each card's Shelf Life: its score, or the
    gate protecting it. members never get it -- the reasons can name who
    watched what. a card the job never scored gets nothing, and while the job
    is off or has stored nothing for this kind the page gets nothing at all
    (no threshold, so no Shelf Life sort either): a score nobody refreshes
    would read as a live verdict."""
    from core.video import shelf_life
    sl_kind = "show" if kind in ("shows", "show") else "movie"
    items = result.get("items") or []
    try:
        from core.video.repair.worker import get_video_repair_worker
        cfg = get_video_repair_worker().job_config("shelf_life")
        if not cfg.get("enabled") or not db.shelf_life_scored(sl_kind):
            return
        scores = db.shelf_life_get(sl_kind, [it.get("id") for it in items if it.get("id") is not None])
        threshold = (cfg.get("settings") or {}).get("threshold")
    except Exception:   # noqa: BLE001 - a badge never breaks the library page
        logger.debug("shelf life badges skipped", exc_info=True)
        return
    for it in items:
        s = scores.get(it.get("id"))
        if s:
            it["shelf_life"] = {"score": s["score"], "gate": s["gate"],
                                "gate_text": shelf_life.GATE_TEXT.get(s["gate"]) if s["gate"] else None}
    try:
        result["shelf_life_threshold"] = int(threshold if threshold is not None else shelf_life.DEFAULT_THRESHOLD)
    except (TypeError, ValueError):
        result["shelf_life_threshold"] = shelf_life.DEFAULT_THRESHOLD


def register_routes(bp):
    @bp.route("/library", methods=["GET"])
    def video_library():
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            # Channels tab: followed YouTube channels (no media server involved —
            # ownership comes from the permanent download history).
            if request.args.get("kind") == "channels":
                return jsonify(get_video_db().query_channel_library(
                    search=request.args.get("search") or None,
                    letter=request.args.get("letter") or None,
                    sort=request.args.get("sort", "title"),
                    page=request.args.get("page", 1),
                    limit=request.args.get("limit", 75),
                    profile_id=_profile(),
                ))
            from core.video.watch_scope import current_watcher

            from .kids import video_cap
            cap = video_cap()
            if cap is not None:
                return jsonify(_capped_library(get_video_db(), cap, resolve_video_server()))
            sort = request.args.get("sort", "title")
            admin = _is_admin()
            if sort == "shelf_life" and not admin:
                sort = "title"        # Shelf Life is admin-only, its sort too
            result = get_video_db().query_library(
                request.args.get("kind", "movies"),
                search=request.args.get("search") or None,
                letter=request.args.get("letter") or None,
                sort=sort,
                status=request.args.get("status", "all"),
                genre=request.args.get("genre") or None,
                resolution=request.args.get("resolution") or None,
                page=request.args.get("page", 1),
                limit=request.args.get("limit", 75),
                server_source=resolve_video_server(),
                watcher=current_watcher().rows,
                # the video library its files sit in (#2029)
                video_library=request.args.get("video_library") or None,
            )
            if admin:
                _attach_shelf_life(get_video_db(), request.args.get("kind", "movies"), result)
            return jsonify(result)
        except Exception:
            logger.exception("Failed to query video library")
            return jsonify({"error": "Failed to load video library"}), 500

    @bp.route("/library/video-libraries", methods=["GET"])
    def video_library_names():
        """The video libraries (#2029) for the library, wanted and activity
        filters: [{id, name, kind}] of the ones switched on with a folder.
        Names only, so any profile with video access can read it (the full
        config is the admin-only /libraries/folders)."""
        from . import get_video_db
        from core.video.libraries import list_libraries
        try:
            return jsonify({"libraries": [{"id": x["id"], "name": x["name"], "kind": x["kind"]}
                                          for x in list_libraries(get_video_db()) if x["usable"]]})
        except Exception:
            logger.exception("Failed to list video library names")
            return jsonify({"libraries": []})

    @bp.route("/library/resolutions", methods=["GET"])
    def video_library_resolutions():
        """File resolutions in use in the movie library — the library page's
        resolution filter dropdown (movies tab only)."""
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            return jsonify({"resolutions": get_video_db().library_resolutions(
                server_source=resolve_video_server())})
        except Exception:
            logger.exception("Failed to list library resolutions")
            return jsonify({"resolutions": []})

    @bp.route("/library/episode", methods=["GET"])
    def video_library_episode():
        """Is a specific episode in the library?
        ?tmdb_id=<show_tmdb_id>&season=<n>&episode=<n>
        Returns {in_library: bool}."""
        from . import get_video_db
        try:
            tmdb_id = request.args.get("tmdb_id", type=int)
            season = request.args.get("season", type=int)
            episode = request.args.get("episode", type=int)
            if not tmdb_id or season is None or episode is None:
                return jsonify({"error": "tmdb_id, season, and episode are required."}), 400
            in_lib = get_video_db().episode_in_library(tmdb_id, season, episode)
            return jsonify({"success": True, "in_library": in_lib})
        except Exception:
            logger.exception("Failed to check episode library status")
            return jsonify({"success": False, "error": "Failed to check episode."}), 500

    @bp.route("/library/genres", methods=["GET"])
    def video_library_genres():
        """Genre names in use for the given kind — the library filter dropdown.
        Read-only and ungated (unlike the collections field-suggestions endpoint)."""
        from . import get_video_db
        from core.video.sources import resolve_video_server
        try:
            return jsonify({"genres": get_video_db().library_genres(
                request.args.get("kind", "movies"), server_source=resolve_video_server())})
        except Exception:
            logger.exception("Failed to list library genres")
            return jsonify({"genres": []})
