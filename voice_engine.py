"""Voice-letter parsing and model-facing guidance for Morrow."""

from __future__ import annotations

import re


TOOL_NAME = "send_voice_letter"
TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "给你寄一封真正会被她听到的声音信。只在此刻用声音比文字更贴近时使用，不要每轮都用。"
            "script 用英文写，是一段完整、自然的口语，通常约 40～100 words，说完想说的就好，长一点"
            "短一点都行；标点是语言的呼吸。允许省略号，长短、停顿、吞音和突然断掉。在局部情绪变化"
            "处才放一个英文音频标签。可优先用 [quietly] [whispers] "
            "[playfully] [flustered] [nervously] [hesitant] [tired] [rushed] [light chuckle] "
            "[laughs] [sighs] [slows down]，也可使用简短、明确、可听见的自然英文短语。写的是声音，"
            "只写她能听到的东西。它和其他工具一样，可以在调用前后继续说普通文字；文字用于自然衔接，"
            "声音信里说过的不用再重复。朗读稿用英文。传 subtitle_zh：按实际朗读句逐句提供自然中文翻译；"
            "一条英文句子对应数组中的一条中文。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "script": {"type": "string", "description": "带少量内联音频标签的完整英文朗读稿"},
                "subtitle_zh": {
                    "type": "array",
                    "description": "与英文朗读句逐句对应的自然中文字幕",
                    "items": {"type": "string"},
                    "maxItems": 80,
                },
            },
            "required": ["script", "subtitle_zh"],
            "additionalProperties": False,
        },
    },
}

_TAG_RE = re.compile(r"\[([^\]\r\n]{1,80})\]")
_SPACE_RE = re.compile(r"[ \t]{2,}")
_SENTENCE_RE = re.compile(r"[^。！？!?；;….—–\n]+(?:[。！？!?；;….—–]+[”’\"']?|\n|$)")

_TONE_MAP = {
    "quietly": "声音很轻", "softly": "声音很轻", "whispers": "近乎耳语",
    "whispering": "近乎耳语", "playfully": "带点调皮", "flustered": "有些慌张",
    "nervously": "有些紧张", "nervous": "有些紧张", "hesitant": "有些迟疑",
    "hesitates": "有些迟疑", "tired": "有些困倦", "rushed": "说得有点急",
    "slows down": "慢慢说着", "sad": "有些难过", "sadly": "有些难过",
    "happy": "听起来很开心", "happily": "听起来很开心", "excited": "有些雀跃",
    "warmly": "语气很温柔", "gently": "语气很温柔", "comforting": "轻声安慰着",
}
_NONVERBAL_MAP = {
    "light chuckle": "轻轻笑了一下", "chuckles": "轻轻笑了一下", "laughs": "笑出了声",
    "laughing": "笑着说", "sighs": "轻轻叹了口气", "sigh": "轻轻叹了口气",
}
_MECHANICAL = {"pause", "short pause", "long pause", "continues", "continue", "emphasis", "stressed"}


def parse_script(script):
    raw = str(script or "").strip()
    tags = [match.group(1).strip() for match in _TAG_RE.finditer(raw)]
    clean = _TAG_RE.sub("", raw)
    clean = _SPACE_RE.sub(" ", clean)
    clean = re.sub(r" *\n *", "\n", clean).strip()
    tones = []
    for tag in tags:
        key = tag.lower().strip()
        label = _TONE_MAP.get(key) or _NONVERBAL_MAP.get(key)
        if label and label not in tones:
            tones.append(label)
        elif key in _MECHANICAL:
            continue
    return {"script": raw, "text": clean, "tags": tags, "tones": tones[:5]}


def split_sentences(text):
    return [part.strip() for part in _SENTENCE_RE.findall(str(text or ""))
            if part.strip() and not re.fullmatch(r"[—–\s]+", part)]


def sentence_timings_from_alignment(script, alignment):
    """Turn ElevenLabs character alignment into compact visible-sentence starts."""
    if not isinstance(alignment, dict):
        return []
    characters = alignment.get("characters")
    starts = alignment.get("character_start_times_seconds")
    ends = alignment.get("character_end_times_seconds")
    if isinstance(characters, str):
        characters = list(characters)
    if not isinstance(characters, list) or not isinstance(starts, list) or not isinstance(ends, list):
        return []
    if not characters or len(characters) != len(starts) or len(characters) != len(ends):
        return []

    aligned_text = "".join(str(item) for item in characters)
    raw_script = str(script or "").strip()
    visible_chars = []
    alignment_indexes = []
    if aligned_text == raw_script:
        inside_tag = False
        for index, character in enumerate(aligned_text):
            if character == "[":
                inside_tag = True
                continue
            if inside_tag:
                if character == "]":
                    inside_tag = False
                continue
            visible_chars.append(character)
            alignment_indexes.append(index)
        if inside_tag:
            return []
        visible_text = "".join(visible_chars)
    else:
        # Some voices omit non-spoken audio tags from the returned character list.
        # Both forms are lossless enough for sentence boundaries, so accept them.
        tagless_script = _TAG_RE.sub("", raw_script)
        clean_script = parse_script(raw_script)["text"]
        if aligned_text not in (tagless_script, clean_script):
            return []
        visible_text = aligned_text
        alignment_indexes = list(range(len(characters)))
    timings = []
    for match in _SENTENCE_RE.finditer(visible_text):
        piece = match.group(0)
        left = len(piece) - len(piece.lstrip())
        right = len(piece.rstrip())
        clean = piece.strip()
        if not clean or re.fullmatch(r"[—–\s]+", clean):
            continue
        first = match.start() + left
        last = match.start() + right - 1
        try:
            start = float(starts[alignment_indexes[first]])
            end = float(ends[alignment_indexes[last]])
        except (IndexError, TypeError, ValueError):
            return []
        if start < 0 or end < start:
            return []
        timings.append({"text": clean, "start": round(start, 3), "end": round(end, 3)})

    expected = split_sentences(parse_script(script)["text"])
    if len(timings) != len(expected):
        return []
    return timings


def clean_subtitles(raw, text):
    if not isinstance(raw, list):
        return []
    subtitles = [str(item or "").strip()[:500] for item in raw]
    if not subtitles or any(not item for item in subtitles):
        return []
    return subtitles if len(subtitles) == len(split_sentences(text)) else []


def history_record(script, text="", tags=None):
    """Build the compact, role-neutral voice boundary shown to the model."""
    script = str(script or "").strip()
    if script:
        return f"【声音信 · 内容：{script}】"
    text = str(text or "").strip()
    tag_text = ", ".join(
        str(item).strip() for item in (tags or [])
        if str(item).strip() and str(item).strip().lower() not in _MECHANICAL
    )
    prefix = f"【声音信 · 语气：{tag_text} · 内容：" if tag_text else "【声音信 · 内容："
    return prefix + text + "】"
