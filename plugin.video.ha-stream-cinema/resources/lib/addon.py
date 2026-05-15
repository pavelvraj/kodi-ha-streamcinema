import json
import os
import re
import ssl
import sys
import time
import traceback
from urllib.parse import parse_qsl, quote, urlencode, urljoin
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

import xbmc
import xbmcaddon
import xbmcgui
import xbmcplugin
import xbmcvfs


ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo("id")
HANDLE = None
BASE_URL = None
ACTION_DOWNLOAD = "Stáhnout"
ACTION_PLAY = "Přehrát"
ACTION_ASK = "Zeptat se"


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
            "download": download_stream,
            "settings": open_settings,
        }.get(action, show_root)(params)
    except ApiError as exc:
        notify("HA Stream Cinema", str(exc))
        xbmcplugin.endOfDirectory(HANDLE, succeeded=False, cacheToDisc=False)
    except Exception as exc:
        xbmc.log("[%s] Unexpected error: %s" % (ADDON_ID, exc), xbmc.LOGERROR)
        xbmc.log("[%s] %s" % (ADDON_ID, traceback.format_exc()), xbmc.LOGERROR)
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
    data = api_get("catalog", media_type=media_type, q=query).get("data", [])

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
    media = api_get("media/%s" % quote(params["media_id"], safe=""))
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
        choose_and_handle_stream(media, media.get("streams") or [])
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def show_season(params):
    media = api_get("media/%s" % quote(params["media_id"], safe=""))
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
    media = api_get("media/%s" % quote(params["media_id"], safe=""))
    season_no = as_int(params.get("season"))
    episode_no = as_int(params.get("episode"))
    streams = []
    for stream in media.get("streams") or []:
        if as_int(stream.get("season")) == season_no and as_int(stream.get("episode")) == episode_no:
            streams.append(stream)
    choose_and_handle_stream(media, streams, season=season_no, episode=episode_no)
    xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def show_streams(params):
    media = api_get("media/%s" % quote(params["media_id"], safe=""))
    add_stream_items(media, media.get("streams") or [])
    end_directory("movies")


def play_stream(params):
    ident = params.get("ident", "")
    title = params.get("title") or ident
    source_url = params.get("source_url") or ""
    if not ident:
        raise ApiError("Stream nema identifikator.")

    stream_url = resolve_stream_url(ident, source_url)

    item = xbmcgui.ListItem(label=title, path=stream_url)
    item.setProperty("IsPlayable", "true")
    xbmcplugin.setResolvedUrl(HANDLE, True, item)


def download_stream(params):
    ident = params.get("ident", "")
    title = params.get("title") or ident
    source_url = params.get("source_url") or ""
    if not ident:
        raise ApiError("Stream nema identifikator.")
    stream_url = resolve_stream_url(ident, source_url)
    download_url(stream_url, title)
    end_plugin_action()


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

    action = "media"
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
        item.addContextMenuItems([
            (
                "Stahnout",
                "RunPlugin(%s)" % plugin_url(
                    action="download",
                    ident=ident,
                    title=stream.get("filename") or media_title(media),
                    source_url=stream.get("stream_url") or "",
                ),
            )
        ])
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


def choose_and_handle_stream(media, streams, season=None, episode=None):
    stream = select_stream(media, streams)
    if not stream:
        return None

    action = selected_action()
    if action == ACTION_ASK:
        action = ask_action()
    if not action:
        return None

    ident = stream_ident(stream)
    title = stream.get("filename") or media_title(media)
    source_url = stream.get("stream_url") or ""
    if action == ACTION_DOWNLOAD:
        queue_download(ident, title, source_url)
        return ACTION_DOWNLOAD
    else:
        stream_url = resolve_stream_url(ident, source_url)
        item = xbmcgui.ListItem(label=title, path=stream_url)
        item.setProperty("IsPlayable", "true")
        item.setInfo("video", info_for_media(media, season=season, episode=episode, stream=stream))
        item.setArt(art_for_media(media))
        xbmc.Player().play(stream_url, item)
        return ACTION_PLAY


def select_stream(media, streams):
    active_streams = [s for s in streams if s.get("status") != "pending_delete" and stream_ident(s)]
    active_streams = sorted(active_streams, key=stream_sort_key, reverse=True)
    if not active_streams:
        notify("HA Stream Cinema", "Zadne aktivni streamy.")
        return None
    items = [stream_dialog_item(s) for s in active_streams]
    try:
        index = xbmcgui.Dialog().select(media_title(media), items, useDetails=True)
    except TypeError:
        index = xbmcgui.Dialog().select(media_title(media), [stream_dialog_label(s) for s in active_streams])
    if index < 0:
        return None
    return active_streams[index]


def selected_action():
    value = xbmcaddon.Addon().getSetting("default_action") or ACTION_ASK
    if value in (ACTION_DOWNLOAD, ACTION_PLAY, ACTION_ASK):
        return value
    try:
        return [ACTION_DOWNLOAD, ACTION_PLAY, ACTION_ASK][int(value)]
    except (TypeError, ValueError, IndexError):
        return ACTION_ASK


def ask_action():
    options = [ACTION_DOWNLOAD, ACTION_PLAY]
    index = xbmcgui.Dialog().select("Akce se streamem", options)
    if index < 0:
        return None
    return options[index]


def resolve_stream_url(ident, source_url=""):
    response = api_get("file_link/%s" % quote(ident, safe=""))
    link = response.get("link")
    if not link:
        raise ApiError("Provider nevratil prehravaci link.")
    stream_url = absolute_api_url(link)
    if source_url and link.lstrip("/").startswith("api/stream_proxy/"):
        stream_url += ("&" if "?" in stream_url else "?") + urlencode({"url": source_url})
    return stream_url


def queue_download(ident, title, source_url=""):
    xbmc.executebuiltin(
        "RunPlugin(%s)" % plugin_url(
            action="download",
            ident=ident,
            title=title,
            source_url=source_url,
        )
    )
    notify("HA Stream Cinema", "Stahovani spusteno na pozadi.", xbmcgui.NOTIFICATION_INFO)


def download_url(stream_url, title):
    folder = xbmcaddon.Addon().getSetting("download_folder") or ""
    if not folder:
        raise ApiError("V nastaveni doplnku vyber slozku pro stahovani.")
    if not xbmcvfs.exists(folder):
        raise ApiError("Slozka pro stahovani neexistuje.")

    filename = safe_filename(title)
    target = unique_target(folder, filename)
    progress = xbmcgui.DialogProgressBG()
    progress.create("HA Stream Cinema", "Pripravuji stahovani: %s" % filename)
    written = 0
    total = 0
    started = time.time()
    out_file = None
    completed = False
    try:
        request = Request(stream_url, headers={"User-Agent": ADDON_ID})
        with urlopen(request, timeout=20) as response:
            total = int(response.headers.get("content-length") or 0)
            out_file = xbmcvfs.File(target, "wb")
            while True:
                if is_progress_canceled(progress):
                    raise ApiError("Stahovani zruseno.")
                chunk = response.read(1024 * 512)
                if not chunk:
                    break
                out_file.write(chunk)
                written += len(chunk)
                update_download_progress(progress, filename, written, total, started)
        completed = True
    except HTTPError as exc:
        raise ApiError("Stazeni vratilo HTTP %s." % exc.code)
    except URLError as exc:
        raise ApiError("Nelze stahnout stream: %s" % exc.reason)
    finally:
        if out_file:
            out_file.close()
        progress.close()
        if not completed and target and xbmcvfs.exists(target):
            xbmcvfs.delete(target)
    notify("HA Stream Cinema", "Stazeno: %s" % filename, xbmcgui.NOTIFICATION_INFO)


def update_download_progress(progress, filename, written, total, started):
    percent = int(written * 100 / total) if total else 0
    elapsed = max(time.time() - started, 0.1)
    speed = written / elapsed
    if total:
        message = "%s\n%s / %s, %s/s" % (filename, format_size(written), format_size(total), format_size(speed))
    else:
        message = "%s\n%s, %s/s" % (filename, format_size(written), format_size(speed))
    try:
        progress.update(percent, "HA Stream Cinema", message)
    except TypeError:
        progress.update(percent, message)


def is_progress_canceled(progress):
    return hasattr(progress, "iscanceled") and progress.iscanceled()


def end_plugin_action():
    if HANDLE >= 0:
        xbmcplugin.endOfDirectory(HANDLE, cacheToDisc=False)


def safe_filename(title):
    name = re.sub(r"[\\/:*?\"<>|]+", "_", title or "stream").strip(" ._")
    if not name:
        name = "stream"
    if not re.search(r"\.(avi|mkv|mp4|mpg|mpeg|ts|m4v)$", name, re.I):
        name += ".mkv"
    return name


def join_kodi_path(folder, filename):
    separator = "" if folder.endswith(("/", "\\")) else "/"
    return folder + separator + filename


def unique_target(folder, filename):
    target = join_kodi_path(folder, filename)
    if not xbmcvfs.exists(target):
        return target
    dot = filename.rfind(".")
    base = filename[:dot] if dot > 0 else filename
    ext = filename[dot:] if dot > 0 else ""
    for index in range(1, 1000):
        candidate = join_kodi_path(folder, "%s (%s)%s" % (base, index, ext))
        if not xbmcvfs.exists(candidate):
            return candidate
    return target


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

    try:
        return fetch_json(api_url)
    except HTTPError as exc:
        raise ApiError("API vratilo HTTP %s." % exc.code)
    except URLError as exc:
        fallback_url = http_fallback_url(api_url, exc)
        if fallback_url:
            try:
                return fetch_json(fallback_url)
            except HTTPError as fallback_exc:
                raise ApiError("API vratilo HTTP %s." % fallback_exc.code)
            except URLError:
                pass
        raise ApiError("Nelze se pripojit k API: %s" % exc.reason)
    except ValueError:
        raise ApiError("API nevratilo platny JSON.")


def fetch_json(api_url):
    xbmc.log("[%s] API GET %s" % (ADDON_ID, api_url), xbmc.LOGDEBUG)
    request = Request(api_url, headers={"Accept": "application/json", "User-Agent": ADDON_ID})
    with urlopen(request, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        payload = response.read().decode(charset)
        return json.loads(payload) if payload else {}


def absolute_api_url(path):
    if path.startswith("http://") or path.startswith("https://"):
        return path
    clean_path = path.lstrip("/")
    if clean_path == "api":
        clean_path = ""
    elif clean_path.startswith("api/"):
        clean_path = clean_path[4:]
    return urljoin(configured_api_url() + "/", clean_path)


def configured_api_url():
    value = (ADDON.getSetting("api_url") or "").strip().rstrip("/")
    if not value:
        raise ApiError("V nastaveni doplnku vypln URL HA Stream Cinema API.")
    if not value.startswith(("http://", "https://")):
        value = "http://" + value
    if not value.rstrip("/").endswith("/api"):
        value = value.rstrip("/") + "/api"
    return value


def http_fallback_url(api_url, exc):
    reason = getattr(exc, "reason", None)
    if api_url.startswith("https://") and is_wrong_ssl_version(reason):
        fallback = "http://" + api_url[len("https://"):]
        xbmc.log("[%s] HTTPS failed with WRONG_VERSION_NUMBER, retrying %s" % (ADDON_ID, fallback), xbmc.LOGWARNING)
        return fallback
    return None


def is_wrong_ssl_version(reason):
    if isinstance(reason, ssl.SSLError):
        return "WRONG_VERSION_NUMBER" in str(reason)
    return "WRONG_VERSION_NUMBER" in str(reason or "")


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
    duration = format_duration(stream.get("duration"))
    if duration:
        badges.append(duration)
    audio = languages_label(stream.get("audio"), "audio")
    if audio:
        badges.append(audio)
    subtitles = languages_label(stream.get("subtitles"), "sub")
    if subtitles:
        badges.append(subtitles)
    if badges:
        parts.append("[%s]" % " | ".join(badges))
    return " ".join(parts)


def stream_dialog_item(stream):
    item = xbmcgui.ListItem(label=stream.get("filename") or stream_ident(stream))
    item.setLabel2(stream_dialog_details(stream))
    icon = stream_format_icon(stream)
    if icon:
        item.setArt({"icon": icon, "thumb": icon})
    return item


def stream_dialog_label(stream):
    return "%s\n%s" % (stream.get("filename") or stream_ident(stream), stream_dialog_details(stream))


def stream_dialog_details(stream):
    parts = []
    fmt = stream_format(stream)
    if fmt:
        parts.append(color_text(fmt, format_color(fmt)))
    resolution = resolution_label(stream)
    if resolution:
        parts.append(resolution)
    size = format_size(stream.get("size"))
    if size:
        parts.append(size)
    duration = format_duration(stream.get("duration"))
    if duration:
        parts.append(duration)
    if stream.get("provider"):
        parts.append(color_text(stream.get("provider"), "FFB8C7D9"))
    audio = languages_label(stream.get("audio"), "audio")
    if audio:
        parts.append(color_text(audio, "FF66D9EF"))
    subtitles = languages_label(stream.get("subtitles"), "sub")
    if subtitles:
        parts.append(color_text(subtitles, "FFA6E22E"))
    return "  ".join(parts)


def stream_format(stream):
    value = (stream.get("format") or "").strip()
    if not value:
        filename = stream.get("filename") or ""
        _, ext = os.path.splitext(filename)
        value = ext.lstrip(".")
    return value.upper()


def stream_format_icon(stream):
    fmt = stream_format(stream).lower()
    if not fmt:
        fmt = "file"
    known = {"avi", "mkv", "mp4", "webm", "m4v", "mov", "ts", "mpg", "mpeg"}
    filename = "format_%s.png" % (fmt if fmt in known else "file")
    return addon_asset("resources/media/%s" % filename)


def addon_asset(path):
    return join_kodi_path(ADDON.getAddonInfo("path"), path)


def resolution_label(stream):
    try:
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (TypeError, ValueError):
        width = 0
        height = 0
    if not width and not height:
        return ""
    if height >= 2160 or width >= 3840:
        return color_text("4K", "FFFFD866")
    if height >= 1080:
        return color_text("1080p", "FF66D9EF")
    if height >= 720:
        return color_text("720p", "FFA6E22E")
    if height:
        return color_text("%sp" % height, "FFFFA94D")
    return color_text("%sx%s" % (width or "-", height or "-"), "FFFFA94D")


def color_text(value, color):
    return "[COLOR %s]%s[/COLOR]" % (color, value)


def format_color(fmt):
    return {
        "MKV": "FF9B5DE5",
        "MP4": "FF00BBF9",
        "AVI": "FFFFA94D",
        "WEBM": "FF00F5D4",
        "M4V": "FF66D9EF",
        "MOV": "FFFFD166",
        "TS": "FFFF5C5C",
        "MPG": "FFFF5C8A",
        "MPEG": "FFFF5C8A",
    }.get((fmt or "").upper(), "FFB8C7D9")


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


def format_duration(value):
    try:
        seconds = int(float(value or 0))
    except (TypeError, ValueError):
        return ""
    if seconds <= 0:
        return ""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours:
        return "%d:%02d h" % (hours, minutes)
    return "%d min" % minutes


def languages_label(value, prefix):
    if not value:
        return ""
    if isinstance(value, str):
        values = [value]
    else:
        values = value
    languages = []
    for item in values:
        if isinstance(item, dict):
            language = item.get("language") or item.get("lang") or item.get("name") or ""
        else:
            language = str(item)
        language = short_language(language)
        if language and language not in languages:
            languages.append(language)
    if not languages:
        return ""
    return "%s %s" % (prefix, "/".join(languages[:4]))


def short_language(value):
    clean = str(value or "").strip().lower()
    if not clean:
        return ""
    mapping = {
        "cze": "CZ",
        "ces": "CZ",
        "cs": "CZ",
        "cz": "CZ",
        "slo": "SK",
        "slk": "SK",
        "sk": "SK",
        "eng": "EN",
        "en": "EN",
    }
    return mapping.get(clean, clean[:3].upper())


def as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def notify(title, message, icon=xbmcgui.NOTIFICATION_ERROR, time_ms=5000):
    xbmcgui.Dialog().notification(title, message, icon, time_ms)


class ApiError(Exception):
    pass
