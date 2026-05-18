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
ACTION_PLAY = "Shlédnout"
ACTION_ASK = "Zeptat se"
DOWNLOADS_FILE = "downloads.json"
PROGRESS_FILE = "progress.json"
WATCHED_RESERVE_SECONDS = 600
WATCHED_RATIO = 0.9


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
            "genres": show_genres,
            "genre": show_genre_catalog,
            "search": search_catalog,
            "media": show_media,
            "season": show_season,
            "episode": show_episode,
            "streams": show_streams,
            "play": play_stream,
            "download": download_stream,
            "downloads": show_downloads,
            "cancel_download": cancel_download,
            "mark_watched": mark_watched,
            "mark_unwatched": mark_unwatched,
            "settings": open_settings,
        }.get(action, show_root)(params)
    except DownloadCancelled as exc:
        if str(exc):
            notify("HA Stream Cinema", str(exc), xbmcgui.NOTIFICATION_INFO)
        end_plugin_action()
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
    add_folder("Zanry", plugin_url(action="genres"))
    add_folder("Prave stahovane soubory", plugin_url(action="downloads"))
    add_folder("Hledat ve sbirce", plugin_url(action="search"))
    add_action_item("Nastaveni", plugin_url(action="settings"))
    end_directory("videos")


def show_catalog(params):
    media_type = params.get("media_type", "all")
    query = params.get("q", "")
    genre = params.get("genre", "")
    data = api_get("catalog", media_type=media_type, q=query).get("data", [])
    if genre:
        data = [media for media in data if genre in (media.get("genres") or [])]

    for media in data:
        add_media_item(media)

    if not data:
        add_folder("Zadna ulozena media", plugin_url())

    end_directory("videos")


def show_genres(params=None):
    data = api_get("catalog").get("data", [])
    genres = sorted({genre for media in data for genre in (media.get("genres") or [])}, key=lambda value: value.lower())
    for genre in genres:
        count = sum(1 for media in data if genre in (media.get("genres") or []))
        add_folder("%s (%s)" % (genre, count), plugin_url(action="genre", genre=genre))
    if not genres:
        add_folder("Zadne zanry", plugin_url())
    end_directory("videos")


def show_genre_catalog(params):
    genre = params.get("genre", "")
    if not genre:
        raise ApiError("Zanr nebyl zadan.")
    show_catalog({"media_type": "all", "genre": genre})


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
        end_directory("tvshows")
    else:
        choose_and_handle_stream(media, media.get("streams") or [], key=playback_key(media))
        end_plugin_action()


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
        episode_key = playback_key(media, season_no, episode_no)
        info = info_for_media(media, episode=episode_no, season=season_no)
        apply_playback_info(info, episode_key)
        add_action_item(
            "Dil %s" % episode_no,
            plugin_url(
                action="episode",
                media_id=media["_id"],
                season=season_no,
                episode=episode_no,
            ),
            art=art_for_media(media),
            info=info,
            properties=playback_properties(episode_key),
            context_menu=watch_context_menu(episode_key),
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
    choose_and_handle_stream(media, streams, season=season_no, episode=episode_no, key=playback_key(media, season_no, episode_no))
    end_plugin_action()


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
    download_id = params.get("download_id") or make_download_id(ident, title)
    if not ident:
        raise ApiError("Stream nema identifikator.")
    stream_url = resolve_stream_url(ident, source_url)
    download_url(stream_url, title, download_id)
    end_plugin_action()


def show_downloads(params=None):
    downloads = active_downloads()
    for item in sorted(downloads, key=lambda value: value.get("started") or 0, reverse=True):
        label = "%s - %s%%" % (item.get("filename") or item.get("title") or "Stahovani", item.get("percent") or 0)
        list_item = xbmcgui.ListItem(label=label)
        list_item.setLabel2(download_details(item))
        xbmcplugin.addDirectoryItem(
            HANDLE,
            plugin_url(action="cancel_download", download_id=item.get("id") or ""),
            list_item,
            isFolder=False,
        )
    if not downloads:
        add_folder("Nic se prave nestahuje", plugin_url())
    end_directory("files")


def cancel_download(params):
    download_id = params.get("download_id") or ""
    downloads = read_downloads()
    item = downloads.get(download_id)
    if not item or item.get("status") not in ("downloading", "starting"):
        notify("HA Stream Cinema", "Stahovani uz neni aktivni.", xbmcgui.NOTIFICATION_INFO)
        end_plugin_action()
        return
    if xbmcgui.Dialog().yesno("Zrusit stahovani", item.get("filename") or item.get("title") or download_id):
        item["cancel"] = True
        item["status"] = "canceling"
        downloads[download_id] = item
        write_downloads(downloads)
        notify("HA Stream Cinema", "Stahovani bude zruseno.", xbmcgui.NOTIFICATION_INFO)
    end_plugin_action()


def open_settings(params=None):
    ADDON.openSettings()
    end_plugin_action()


def add_media_item(media):
    media_type = media.get("type") or "movie"
    title = media_title(media)
    stream_count = media.get("stream_count")
    label = title
    if stream_count:
        label = "%s (%s streamu)" % (title, stream_count)

    action = "media"
    info = info_for_media(media)
    kwargs = {"art": art_for_media(media), "info": info}
    if media_type == "tvshow":
        add_folder(label, plugin_url(action=action, media_id=media["_id"]), **kwargs)
    else:
        key = playback_key(media)
        apply_playback_info(info, key)
        kwargs["properties"] = playback_properties(key)
        kwargs["context_menu"] = watch_context_menu(key)
        add_action_item(label, plugin_url(action=action, media_id=media["_id"]), **kwargs)


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


def choose_and_handle_stream(media, streams, season=None, episode=None, key=None):
    key = key or playback_key(media, season, episode)
    action = selected_action()
    if action == ACTION_ASK:
        action = ask_action()
    if not action:
        return None

    resume_time = 0
    if action == ACTION_PLAY:
        resume_time = choose_resume_time(key)
        if resume_time is None:
            return None

    stream = select_stream(media, streams)
    if not stream:
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
        play_with_tracking(stream_url, item, key, resume_time, stream.get("duration"))
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
    if value == "Přehrát":
        return ACTION_PLAY
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


def choose_resume_time(key):
    state = playback_state(key)
    resume_time = int(state.get("resume") or 0)
    total = int(state.get("total") or 0)
    if resume_time <= 60 or state.get("watched"):
        return 0
    label = "Pokracovat od %s" % format_time(resume_time)
    index = xbmcgui.Dialog().select("Pokracovat v prehravani", [label, "Od zacatku"])
    if index < 0:
        return None
    return resume_time if index == 0 else 0


def play_with_tracking(stream_url, item, key, resume_time=0, duration=None):
    player = xbmc.Player()
    monitor = xbmc.Monitor()
    player.play(stream_url, item)

    started = False
    for _ in range(60):
        if monitor.abortRequested():
            return
        if player.isPlayingVideo():
            started = True
            break
        if monitor.waitForAbort(0.25):
            return

    if not started:
        return

    if resume_time:
        try:
            player.seekTime(float(resume_time))
        except Exception as exc:
            xbmc.log("[%s] Cannot seek to resume point: %s" % (ADDON_ID, exc), xbmc.LOGWARNING)

    last_time = float(resume_time or 0)
    total_time = float(duration or 0)
    while not monitor.abortRequested() and player.isPlayingVideo():
        try:
            last_time = player.getTime()
            total_time = player.getTotalTime() or total_time
        except RuntimeError:
            break
        if monitor.waitForAbort(1):
            break

    save_playback_position(key, last_time, total_time)


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
    download_id = make_download_id(ident, title)
    register_download(download_id, title, ident)
    xbmc.executebuiltin(
        "RunPlugin(%s)" % plugin_url(
            action="download",
            ident=ident,
            title=title,
            source_url=source_url,
            download_id=download_id,
        )
    )
    notify("HA Stream Cinema", "Stahovani spusteno na pozadi.", xbmcgui.NOTIFICATION_INFO)


def download_url(stream_url, title, download_id):
    folder = xbmcaddon.Addon().getSetting("download_folder") or ""
    if not folder:
        raise ApiError("V nastaveni doplnku vyber slozku pro stahovani.")
    if not xbmcvfs.exists(folder):
        raise ApiError("Slozka pro stahovani neexistuje.")

    filename = safe_filename(title)
    target = unique_target(folder, filename)
    update_download_state(
        download_id,
        title=title,
        filename=filename,
        target=target,
        status="starting",
        percent=0,
        written=0,
        total=0,
        started=time.time(),
    )
    progress = xbmcgui.DialogProgressBG()
    progress.create("HA Stream Cinema", "Pripravuji stahovani: %s" % filename)
    written = 0
    total = 0
    started = time.time()
    out_file = None
    completed = False
    canceled = False
    last_progress_percent = None
    try:
        request = Request(stream_url, headers={"User-Agent": ADDON_ID})
        with urlopen(request, timeout=20) as response:
            total = int(response.headers.get("content-length") or 0)
            out_file = xbmcvfs.File(target, "wb")
            update_download_state(download_id, status="downloading", total=total)
            while True:
                if should_cancel_download(download_id):
                    raise DownloadCancelled("Stahovani zruseno.")
                chunk = response.read(1024 * 512)
                if not chunk:
                    break
                out_file.write(chunk)
                written += len(chunk)
                percent = download_percent(written, total)
                update_download_state(download_id, percent=percent, written=written, total=total, status="downloading")
                if should_report_progress(percent, last_progress_percent):
                    update_download_progress(progress, filename, written, total, started)
                    notify_download_progress(filename, percent, written, total)
                    last_progress_percent = percent
        completed = True
        update_download_state(download_id, percent=100, written=written, total=total, status="completed", completed=time.time())
    except HTTPError as exc:
        update_download_state(download_id, status="error", error="HTTP %s" % exc.code)
        raise ApiError("Stazeni vratilo HTTP %s." % exc.code)
    except URLError as exc:
        update_download_state(download_id, status="error", error=str(exc.reason))
        raise ApiError("Nelze stahnout stream: %s" % exc.reason)
    except DownloadCancelled:
        canceled = True
        update_download_state(download_id, status="canceled", completed=time.time())
        raise
    finally:
        if out_file:
            out_file.close()
        progress.close()
        if not completed and target and xbmcvfs.exists(target):
            xbmcvfs.delete(target)
        if completed or canceled:
            remove_download(download_id)
    notify("HA Stream Cinema", "Stazeno: %s" % filename, xbmcgui.NOTIFICATION_INFO)


def download_percent(written, total):
    return int(written * 100 / total) if total else 0


def update_download_progress(progress, filename, written, total, started):
    percent = download_percent(written, total)
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


def notify_download_progress(filename, percent, written, total):
    step = download_notify_step()
    if not step:
        return
    detail = "%s%%" % percent
    if total:
        detail = "%s - %s / %s" % (detail, format_size(written), format_size(total))
    notify("HA Stream Cinema", "%s: %s" % (filename, detail), xbmcgui.NOTIFICATION_INFO, 2500)


def should_report_progress(percent, last_percent):
    step = download_notify_step()
    if not step or percent <= 0:
        return False
    if last_percent is None:
        return percent == 100 or percent >= step
    return percent == 100 or (percent // step) > (last_percent // step)


def download_notify_step():
    value = xbmcaddon.Addon().getSetting("download_notify_step") or "20%"
    if value == "Bez notifikace":
        return 0
    if value.isdigit():
        options = [0, 1, 5, 10, 20, 25, 50, 100]
        try:
            return options[int(value)]
        except (TypeError, ValueError, IndexError):
            return 20
    try:
        return int(value.rstrip("%"))
    except (TypeError, ValueError):
        return 20


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


def make_download_id(ident, title):
    seed = "%s-%s-%s" % (ident, title, int(time.time() * 1000))
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", seed)[-120:]


def active_downloads():
    downloads = read_downloads()
    active = []
    changed = False
    for download_id, item in list(downloads.items()):
        if item.get("status") in ("starting", "downloading", "canceling"):
            active.append(item)
        elif (time.time() - float(item.get("completed") or 0)) > 300:
            downloads.pop(download_id, None)
            changed = True
    if changed:
        write_downloads(downloads)
    return active


def register_download(download_id, title, ident):
    update_download_state(
        download_id,
        id=download_id,
        title=title,
        filename=safe_filename(title),
        ident=ident,
        status="starting",
        percent=0,
        written=0,
        total=0,
        cancel=False,
        started=time.time(),
    )


def update_download_state(download_id, **changes):
    if not download_id:
        return
    downloads = read_downloads()
    item = downloads.get(download_id) or {"id": download_id, "started": time.time()}
    item.update(changes)
    downloads[download_id] = item
    write_downloads(downloads)


def remove_download(download_id):
    downloads = read_downloads()
    if download_id in downloads:
        downloads.pop(download_id, None)
        write_downloads(downloads)


def should_cancel_download(download_id):
    return bool(read_downloads().get(download_id, {}).get("cancel"))


def download_details(item):
    parts = []
    status = item.get("status") or "downloading"
    if status == "canceling":
        parts.append("rusim")
    else:
        parts.append(status)
    written = format_size(item.get("written"))
    total = format_size(item.get("total"))
    if written and total:
        parts.append("%s / %s" % (written, total))
    elif written:
        parts.append(written)
    target = item.get("target")
    if target:
        parts.append(target)
    return " | ".join(parts)


def read_downloads():
    path = downloads_path()
    if not xbmcvfs.exists(path):
        return {}
    handle = None
    try:
        handle = xbmcvfs.File(path, "r")
        payload = handle.read()
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload or "{}")
    except Exception as exc:
        xbmc.log("[%s] Cannot read downloads state: %s" % (ADDON_ID, exc), xbmc.LOGWARNING)
        return {}
    finally:
        if handle:
            handle.close()


def write_downloads(downloads):
    profile_dir()
    handle = None
    try:
        handle = xbmcvfs.File(downloads_path(), "w")
        handle.write(json.dumps(downloads, ensure_ascii=False))
    except Exception as exc:
        xbmc.log("[%s] Cannot write downloads state: %s" % (ADDON_ID, exc), xbmc.LOGWARNING)
    finally:
        if handle:
            handle.close()


def downloads_path():
    return join_kodi_path(profile_dir(), DOWNLOADS_FILE)


def profile_dir():
    path = xbmcvfs.translatePath(ADDON.getAddonInfo("profile"))
    if not xbmcvfs.exists(path):
        xbmcvfs.mkdirs(path)
    return path


def mark_watched(params):
    key = params.get("key") or ""
    if key:
        set_watched(key)
        notify("HA Stream Cinema", "Oznaceno jako zhlednute.", xbmcgui.NOTIFICATION_INFO)
    end_plugin_action()


def mark_unwatched(params):
    key = params.get("key") or ""
    if key:
        clear_playback_state(key)
        notify("HA Stream Cinema", "Oznaceno jako nezhlednute.", xbmcgui.NOTIFICATION_INFO)
    end_plugin_action()


def watch_context_menu(key):
    if not key:
        return []
    state = playback_state(key)
    if state.get("watched"):
        return [("Oznacit jako nezhlednute", "RunPlugin(%s)" % plugin_url(action="mark_unwatched", key=key))]
    return [("Oznacit jako zhlednute", "RunPlugin(%s)" % plugin_url(action="mark_watched", key=key))]


def playback_key(media, season=None, episode=None):
    media_id = media.get("_id") or media.get("id") or media_title(media)
    if season is not None and episode is not None:
        return "episode:%s:%s:%s" % (media_id, season, episode)
    return "media:%s" % media_id


def playback_state(key):
    return read_progress().get(key, {}) if key else {}


def save_playback_position(key, position, total):
    if not key:
        return
    position = int(position or 0)
    total = int(total or 0)
    if is_watched_position(position, total):
        set_watched(key, total=total)
    elif position > 60:
        update_progress_state(key, watched=False, resume=position, total=total, updated=time.time())


def is_watched_position(position, total):
    if position <= 0:
        return False
    if total > 0:
        return (total - position) <= WATCHED_RESERVE_SECONDS or (float(position) / float(total)) >= WATCHED_RATIO
    return False


def set_watched(key, total=0):
    update_progress_state(key, watched=True, resume=0, total=int(total or playback_state(key).get("total") or 0), updated=time.time())


def clear_playback_state(key):
    progress = read_progress()
    if key in progress:
        progress.pop(key, None)
        write_progress(progress)


def update_progress_state(key, **changes):
    progress = read_progress()
    item = progress.get(key) or {}
    item.update(changes)
    progress[key] = item
    write_progress(progress)


def apply_playback_info(info, key):
    state = playback_state(key)
    if state.get("watched"):
        info["playcount"] = 1
    else:
        info["playcount"] = 0


def playback_properties(key):
    state = playback_state(key)
    if state.get("watched"):
        return {}
    resume = int(state.get("resume") or 0)
    total = int(state.get("total") or 0)
    if resume > 60:
        properties = {"ResumeTime": str(resume)}
        if total:
            properties["TotalTime"] = str(total)
        return properties
    return {}


def read_progress():
    path = progress_path()
    if not xbmcvfs.exists(path):
        return {}
    handle = None
    try:
        handle = xbmcvfs.File(path, "r")
        payload = handle.read()
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        return json.loads(payload or "{}")
    except Exception as exc:
        xbmc.log("[%s] Cannot read playback state: %s" % (ADDON_ID, exc), xbmc.LOGWARNING)
        return {}
    finally:
        if handle:
            handle.close()


def write_progress(progress):
    profile_dir()
    handle = None
    try:
        handle = xbmcvfs.File(progress_path(), "w")
        handle.write(json.dumps(progress, ensure_ascii=False))
    except Exception as exc:
        xbmc.log("[%s] Cannot write playback state: %s" % (ADDON_ID, exc), xbmc.LOGWARNING)
    finally:
        if handle:
            handle.close()


def progress_path():
    return join_kodi_path(profile_dir(), PROGRESS_FILE)


def add_folder(label, url, art=None, info=None, context_menu=None, properties=None):
    item = xbmcgui.ListItem(label=label)
    if info:
        item.setInfo("video", info)
    if art:
        item.setArt(art)
    if properties:
        for key, value in properties.items():
            item.setProperty(key, value)
    if context_menu:
        item.addContextMenuItems(context_menu)
    xbmcplugin.addDirectoryItem(HANDLE, url, item, isFolder=True)


def add_action_item(label, url, art=None, info=None, context_menu=None, properties=None):
    item = xbmcgui.ListItem(label=label)
    if info:
        item.setInfo("video", info)
    if art:
        item.setArt(art)
    if properties:
        for key, value in properties.items():
            item.setProperty(key, value)
    if context_menu:
        item.addContextMenuItems(context_menu)
    xbmcplugin.addDirectoryItem(HANDLE, url, item, isFolder=False)


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


def format_time(seconds):
    seconds = int(seconds or 0)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


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


class DownloadCancelled(Exception):
    pass
