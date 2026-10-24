"""Server-side music bridge with isolated KuGou and NetEase credentials."""

import json
import os
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BRIDGE = ROOT / "music_bridge.mjs"


class MusicError(RuntimeError):
    pass


PLATFORMS = {"kugou", "netease"}


def normalize_cookie(value):
    if not isinstance(value, str):
        raise MusicError("Cookie 格式不正确")
    if "\r" in value or "\n" in value or len(value) > 16000:
        raise MusicError("请粘贴不含换行的完整 Cookie，长度不超过 16000 字符")
    return re.sub(r"^cookie:\s*", "", value.strip(), flags=re.IGNORECASE)


def detect_cookie_platform(cookie):
    keys = {key.lower() for key in re.findall(r"(?:^|;)\s*([^=;\s]+)\s*=", cookie)}
    netease = bool(keys & {"music_u", "music_a", "nmtid", "wnmcid", "_ntes_nuid", "_ntes_nnid"})
    kugou = bool(keys & {"kugooid", "kugoowebtoken", "kg_mid", "kg_dfid", "kg_dfid_collect"})
    if netease and kugou:
        raise MusicError("请一次只粘贴酷狗或网易云其中一个账号的 Cookie")
    if netease:
        return "netease"
    if kugou or "t" in keys:
        return "kugou"
    raise MusicError("没有识别出酷狗或网易云的 Cookie，请粘贴完整 Cookie")


def connection_settings(read_setting, platform=None):
    """Internal only: never return this dictionary from a public API."""
    def credential(provider):
        return str(read_setting("music_" + provider + "_cookie", "") or
                   os.environ.get("METING_" + provider.upper() + "_COOKIE", "")).strip()
    if platform is None:
        platform = str(read_setting("music_platform", "") or "").strip()
        if platform not in PLATFORMS:
            platform = "netease" if credential("netease") and not credential("kugou") else "kugou"
    if platform not in PLATFORMS:
        raise MusicError("不支持这个音乐来源")
    return {"platform": platform, "cookie": credential(platform)}


def split_song_id(song_id):
    value = str(song_id or "").strip()
    if not value:
        raise MusicError("缺少歌曲")
    if ":" not in value:
        return "kugou", value
    platform, raw_id = value.split(":", 1)
    if platform not in PLATFORMS or not raw_id or (platform == "netease" and not raw_id.isdigit()):
        raise MusicError("歌曲来源或编号不正确")
    return platform, raw_id


def _run(payload, timeout=70):
    try:
        completed = subprocess.run(
            [os.environ.get("NODE_BIN", "node"), str(BRIDGE)],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            cwd=ROOT,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MusicError("音乐服务暂时没有回应") from exc
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise MusicError("音乐服务返回了无法识别的数据") from exc
    if completed.returncode or not isinstance(data, dict) or not data.get("ok"):
        raise MusicError("音乐服务暂时不可用，请稍后重试或重新连接账号")
    return data


def _first_url(value):
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("url", "play_url", "playUrl"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate.replace("http://", "https://", 1)
        for candidate in value.values():
            found = _first_url(candidate)
            if found:
                return found
    if isinstance(value, list):
        for candidate in value:
            found = _first_url(candidate)
            if found:
                return found
    return ""


def search(query, cookie="", limit=12, platform="kugou"):
    query = str(query or "").strip()
    if not query:
        return []
    if platform not in PLATFORMS:
        raise MusicError("不支持这个音乐来源")
    raw = _run({"action": "search", "query": query, "limit": limit, "cookie": cookie, "platform": platform}).get("result")
    rows = raw if isinstance(raw, list) else []
    results = []
    for row in rows[:limit]:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        artists = row.get("artist") or []
        if isinstance(artists, str):
            artists = [artists]
        results.append({
            "id": ("netease:" if platform == "netease" else "") + str(row["id"]),
            "name": str(row.get("name") or "未知歌曲"),
            "artist": " / ".join(str(item) for item in artists if item) or "未知歌手",
            "album": str(row.get("album") or ""),
        })
    return results


def prepare(song_id, cookie=""):
    platform, raw_id = split_song_id(song_id)
    data = _run({"action": "prepare", "id": raw_id, "cookie": cookie, "platform": platform})
    lyric_data = data.get("lyric")
    if not isinstance(lyric_data, dict):
        lyric_data = {}
    return {
        "audio_url": _first_url(data.get("url")),
        "cover_url": _first_url(data.get("pic")),
        "lyric": str(lyric_data.get("lyric") or ""),
        "translated_lyric": str(lyric_data.get("tlyric") or ""),
    }


def lyrics(song_id, cookie=""):
    platform, raw_id = split_song_id(song_id)
    data = _run({"action": "lyrics", "id": raw_id, "cookie": cookie, "platform": platform})
    lyric_data = data.get("lyric")
    if not isinstance(lyric_data, dict):
        lyric_data = {}
    return {
        "lyric": str(lyric_data.get("lyric") or ""),
        "translated_lyric": str(lyric_data.get("tlyric") or ""),
    }
