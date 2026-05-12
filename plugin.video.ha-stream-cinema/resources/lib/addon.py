import json
import sys
from urllib.parse import parse_qsl, quote, urlencode, urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
HANDLE = None
BASE_URL = None


def run(argv):
    global HANDLE, BASE_URL
    BASE_URL = argv[0]
    HANDLE = int(argv[1])
    params = dict(parse_qsl(argv[2][1:]))
    action = params.get("action", "root")

    try:
        {
            "root": show_root,
            "catalog": show_catalog,
            "search": search_catalog,
            "media": show_media,
            "season": show_season,
            "episode": show_episode,
            "streams": show_streams,
            "play": play_stream,
            "settings": open_settings,
        }.get(action, show_root)(params)
    except ApiError as exc:
        notify("HA Stream Cinema", str(exc))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
    except Exception as exc:
        xbmc.log("[%s] Unexpected error: %s" % (ADDON_ID, exc), xbmc.LOGERROR)
        notify("HA Stream Cinema", "Neocekavana chyba doplnku.")
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)


def show_root(params=None):
    add_folder("Filmy", plugin_url(action="catalog", media_type="movie"))
    add_folder("Serialy", plugin_url(action="catalog", media_type="tvshow"))
    add_folder("Hledat ve sbirce", plugin_url(action="search"))
    add_folder("Nastaveni", plugin_url(action="settings"))
    end_directory("videos")


def show_catalog(params):
    media_type = params.get("media_type", "all")
    query = params.get("q", "")
    data = api_get("/api/catalog", media_type=media_type, q=query).get("data", [])

    for media in data:
        add_media_item(media)

    if not data:
        add_folder("Zadna ulozena media", plugin_url())

    end_directory("videos")


def search_catalog(params=None):
    query = xbmcgui.Dialog().input("Hledat ve sbirce", type=xbmcgui.INPUT_ALPHANUM)
    if not query:
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)
        return
    show_catalog({"media_type": "all", "q": query})


def show_media(params):
    media = api_get("/api/media/%s" % quote(params["media_id"], safe=""))
    if media.get("type") == "tvshow" and media.get("seasons"):
        for season in media.get("seasons") or []:
            season_no = season.get("season")
            add_folder(
                "Serie %s" % season_no,
                plugin_url(action="season", media_id=media["_id"], season=season_no),
                art=art_for_media(media),
                info=info_for_media(media),
            )
    else:
        add_stream_items(media, media.get("streams") or [])
    end_directory("tvshows" if media.get("type") == "tvshow" else "movies")


def show_season(params):
    media = api_get("/api/media/%s" % quote(params["media_id"], safe=""))
    season_no = as_int(params.get("season"))
    selected = None
    for season in media.get("seasons") or []:
        if as_int(season.get("season")) == season_no:
            selected = season
            break

    if not selected:
        raise ApiError("Serie nebyla nalezena.")

    for episode in selected.get("episodes") or []:
        episode_no = episode.get("episode")
        add_folder(
            "Dil %s" % episode_no,
            plugin_url(
                action="episode",
                media_id=media["_id"],
                season=season_no,
                episode=episode_no,
            ),
            art=art_for_media(media),
            info=info_for_media(media, episode=episode_no, season=season_no),
        )
    end_directory("episodes")


def show_episode(params):
    media = api_get("/api/media/%s" % quote(params["media_id"], safe=""))
    season_no = as_int(params.get("season"))
    episode_no = as_int(params.get("episode"))
    streams = []
    for stream in media.get("streams") or []:
        if as_int(stream.get("season")) == season_no and as_int(stream.get("episode")) == episode_no:
            streams.append(stream)
    add_stream_items(media, streams, season=season_no, episode=episode_no)
    end_directory("episodes")


def show_streams(params):
    media = api_get("/api/media/%s" % quote(params["media_id"], safe=""))
    add_stream_items(media, media.get("streams") or [])
    end_directory("movies")


def play_stream(params):
    ident = params.get("ident", "")
    title = params.get("title") or ident
    source_url = params.get("source_url") or ""
    if not ident:
        raise ApiError("Stream nema identifikator.")

    response = api_get("/api/file_link/%s" % quote(ident, safe=""))
    link = response.get("link")
    if not link:
        raise ApiError("Provider nevratil prehravaci link.")

    stream_url = absolute_api_url(link)
    if source_url and link.startswith("api/stream_proxy/"):
        stream_url += ("&" if "?" in stream_url else "?") + urlencode({"url": source_url})

    item = xbmcgui.ListItem(label=title, path=stream_url)
    item.setProperty("IsPlayable", "true")
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def open_settings(params=None):
    ADDON.openSettings()
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def add_media_item(media):
    media_type = media.get("type") or "movie"
    title = media_title(media)
    stream_count = media.get("stream_count")
    label = title
    if stream_count:
        label = "%s (%s streamu)" % (title, stream_count)

    action = "media" if media_type == "tvshow" else "streams"
    add_folder(
        label,
        plugin_url(action=action, media_id=media["_id"]),
        art=art_for_media(media),
        info=info_for_media(media),
    )


def add_stream_items(media, streams, season=None, episode=None):
    active_streams = [s for s in streams if s.get("status") != "pending_delete"]
    for stream in sorted(active_streams, key=stream_sort_key, reverse=True):
        ident = stream_ident(stream)
        if not ident:
            continue
        label = stream_label(stream)
        item = xbmcgui.ListItem(label=label)
        item.setProperty("IsPlayable", "true")
        item.setInfo("video", info_for_media(media, season=season, episode=episode, stream=stream))
        item.setArt(art_for_media(media))
        xbmcplugin.addDirectoryItem(
            HANDLE,
            plugin_url(
                action="play",
                ident=ident,
                title=stream.get("filename") or media_title(media),
                source_url=stream.get("stream_url") or "",
            ),
            item,
            isFolder=False,
        )

    if not active_streams:
        add_folder("Zadne aktivni streamy", plugin_url())


def add_folder(label, url, art=None, info=None):
    item = xbmcgui.ListItem(label=label)
    if info:
        item.setInfo("video", info)
    if art:
        item.setArt(art)
    xbmcplugin.addDirectoryItem(HANDLE, url, item, isFolder=True)


def end_directory(content):
    xbmcplugin.setContent(HANDLE, content)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def plugin_url(**params):
    if not params:
        return BASE_URL
    return BASE_URL + "?" + urlencode(params)


def api_get(path, **params):
    api_url = absolute_api_url(path)
    if params:
        clean_params = {k: v for k, v in params.items() if v not in (None, "")}
        if clean_params:
            api_url += ("&" if "?" in api_url else "?") + urlencode(clean_params)

    request = Request(api_url, headers={"Accept": "application/json", "User-Agent": ADDON_ID})
    try:
        with urlopen(request, timeout=20) as response:
            charset = response.headers.get_content_charset() or "utf-8"
            payload = response.read().decode(charset)
            return json.loads(payload) if payload else {}
    except HTTPError as exc:
        raise ApiError("API vratilo HTTP %s." % exc.code)
    except URLError as exc:
        raise ApiError("Nelze se pripojit k API: %s" % exc.reason)
    except ValueError:
        raise ApiError("API nevratilo platny JSON.")


def absolute_api_url(path):
    base = configured_api_url()
    if path.startswith("http://") or path.startswith("https://"):
        return path
    return urljoin(base + "/", path.lstrip("/"))


def configured_api_url():
    value = (ADDON.getSetting("api_url") or "").strip().rstrip("/")
    if not value:
        raise ApiError("V nastaveni doplnku vypln URL HA Stream Cinema API.")
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    return value


def media_title(media):
    title = media.get("title") or media.get("original_title") or media.get("_id") or "Bez nazvu"
    year = media.get("year")
    return "%s (%s)" % (title, year) if year else title


def info_for_media(media, season=None, episode=None, stream=None):
    media_type = "episode" if episode is not None else ("tvshow" if media.get("type") == "tvshow" else "movie")
    info = {
        "title": media.get("title") or media.get("original_title") or "",
        "originaltitle": media.get("original_title") or media.get("title") or "",
        "plot": media.get("plot") or "",
        "year": media.get("year") or 0,
        "rating": rating_for_kodi(media.get("rating")),
        "genre": ", ".join(media.get("genres") or []),
        "mediatype": media_type,
    }
    if season is not None:
        info["season"] = int(season)
    if episode is not None:
        info["episode"] = int(episode)
        info["TVShowTitle"] = media.get("title") or media.get("original_title") or ""
    if stream and stream.get("duration"):
        info["duration"] = int(stream.get("duration") or 0)
    return info


def art_for_media(media):
    poster = media.get("poster") or ""
    fanart = media.get("fanart") or poster
    art = {}
    if poster:
        art["poster"] = poster
        art["thumb"] = poster
        art["icon"] = poster
    if fanart:
        art["fanart"] = fanart
    return art


def stream_ident(stream):
    ident = stream.get("ident") or ""
    provider = stream.get("provider") or ""
    if ":" in ident:
        return ident
    if provider and ident:
        return "%s:%s" % (provider, ident)
    return ident


def stream_label(stream):
    parts = [stream.get("filename") or stream_ident(stream)]
    badges = []
    if stream.get("provider"):
        badges.append(stream.get("provider"))
    if stream.get("format"):
        badges.append(stream.get("format"))
    if stream.get("width") or stream.get("height"):
        badges.append("%sx%s" % (stream.get("width") or "-", stream.get("height") or "-"))
    size = format_size(stream.get("size"))
    if size:
        badges.append(size)
    if badges:
        parts.append("[%s]" % " | ".join(badges))
    return " ".join(parts)


def stream_sort_key(stream):
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    size = int(stream.get("size") or 0)
    return (height * width, size)


def rating_for_kodi(value):
    try:
        rating = float(value or 0)
    except (TypeError, ValueError):
        return 0
    return rating / 10.0 if rating > 10 else rating


def format_size(value):
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    units = ["B", "KB", "MB", "GB", "TB"]
    unit = 0
    while size >= 1024 and unit < len(units) - 1:
        size /= 1024.0
        unit += 1
    return "%.1f %s" % (size, units[unit])


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def notify(title, message):
    xbmcgui.Dialog().notification(title, message, xbmcgui.NOTIFICATION_ERROR, 5000)


class ApiError(Exception):
    pass
