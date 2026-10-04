"""Small server-side bridge to Meting-Agent's KuGou provider."""

import json
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
BRIDGE = ROOT / "music_bridge.mjs"


class MusicError(RuntimeError):
    pass


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
    if completed.returncode or not data.get("ok"):
        raise MusicError(str(data.get("error") or "音乐服务暂时不可用"))
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


def search(query, cookie="", limit=12):
    query = str(query or "").strip()
    if not query:
        return []
    raw = _run({"action": "search", "query": query, "limit": limit, "cookie": cookie}).get("result")
    rows = raw if isinstance(raw, list) else []
    results = []
    for row in rows[:limit]:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        artists = row.get("artist") or []
        if isinstance(artists, str):
            artists = [artists]
        results.append({
            "id": str(row["id"]),
            "name": str(row.get("name") or "未知歌曲"),
            "artist": " / ".join(str(item) for item in artists if item) or "未知歌手",
            "album": str(row.get("album") or ""),
        })
    return results


def prepare(song_id, cookie=""):
    data = _run({"action": "prepare", "id": str(song_id or ""), "cookie": cookie})
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
    data = _run({"action": "lyrics", "id": str(song_id or ""), "cookie": cookie})
    lyric_data = data.get("lyric")
    if not isinstance(lyric_data, dict):
        lyric_data = {}
    return {
        "lyric": str(lyric_data.get("lyric") or ""),
        "translated_lyric": str(lyric_data.get("tlyric") or ""),
    }
