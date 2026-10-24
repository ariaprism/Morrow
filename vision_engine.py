"""Quiet image-memory generation for Morrow chat attachments."""

from __future__ import annotations

import base64
import json
import os
import re

import requests

import chat_engine
from media_utils import detect_image_mime


DEFAULT_PROMPT = """为每张图片留下一句简短的“看过后的记忆”，供未来对话在不再携带原图时理解上下文。

只记录最有辨识度、最可能在后续被提及的内容，不做完整画面解说，不评价，不回复用户，不猜测发送意图。结合用户随图片发送的文字理解重点，但不要重复用户已经写明的内容。

简单图片通常用一句话、约 20～50 个中文字；内容较多时可以稍长。不要为了达到某个长度补充无关的颜色、构图、背景和装饰细节。

遇到文章、聊天记录、网页、表格等大量文字时，不要全文转录，只概括主题并保留少量会影响后续交流的关键名称、数字、日期、结论或异常信息。

每张图片最多 120 个中文字，能更短说清就更短。看不清或不确定的内容不要猜测。

严格返回 JSON：{"images":[{"index":1,"description":"..."}]}。每张输入图片必须对应一个结果，不得合并、遗漏或改变顺序。"""


class VisionError(RuntimeError):
    pass


def _provider(provider_id):
    provider = chat_engine.get_provider(provider_id)
    if provider:
        return provider
    return {
        "base_url": chat_engine.LLM_BASE_URL.rstrip("/"),
        "api_key": chat_engine.LLM_API_KEY,
        "format": "openai",
        "id": None,
        "name": "env",
    }


def _image_blob(attachment):
    selected = attachment.get("tool_copy") if isinstance(attachment.get("tool_copy"), dict) else attachment
    path = os.path.basename(selected.get("path") or "")
    full = os.path.join(chat_engine.UPLOAD_DIR, path)
    if not path or not os.path.isfile(full):
        selected = attachment
        path = os.path.basename(selected.get("path") or "")
        full = os.path.join(chat_engine.UPLOAD_DIR, path)
    if not path or not os.path.isfile(full):
        raise VisionError("图片文件已经不在了。")
    try:
        with open(full, "rb") as source:
            blob = source.read()
    except OSError as exc:
        raise VisionError("图片文件暂时读不了。") from exc
    mime = detect_image_mime(blob) or selected.get("mime") or attachment.get("mime") or "image/jpeg"
    return mime, base64.b64encode(blob).decode("ascii")


def _request_text(user_text, count):
    context = (user_text or "").strip()[:4000] or "（用户没有附带文字）"
    return (
        f"这条用户消息附有 {count} 张图片。请按图片顺序分别留下文字底片。\n\n"
        f"【用户随图片发送的文字】\n{context}"
    )


def _openai(provider, model, system, user_text, attachments, think):
    content = [{"type": "text", "text": _request_text(user_text, len(attachments))}]
    for attachment in attachments:
        mime, data = _image_blob(attachment)
        content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}})
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "stream": False,
        "max_tokens": 1800 if think else 1200,
    }
    if think:
        payload["chat_template_kwargs"] = {"thinking": True}
    try:
        response = requests.post(
            provider["base_url"] + "/chat/completions",
            headers={"Authorization": "Bearer " + provider["api_key"], "Content-Type": "application/json"},
            json=payload,
            timeout=chat_engine.REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise VisionError(f"连不上识图模型：{exc}") from exc
    if response.status_code != 200:
        raise VisionError(f"识图模型返回错误 {response.status_code}：{response.text[:240]}")
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise VisionError("识图模型没有返回可用描述。") from exc


def _anthropic(provider, model, system, user_text, attachments, think):
    content = [{"type": "text", "text": _request_text(user_text, len(attachments))}]
    for attachment in attachments:
        mime, data = _image_blob(attachment)
        content.append({"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}})
    visible_tokens = 1200
    payload = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": content}],
        "stream": False,
        "max_tokens": visible_tokens,
    }
    if think:
        budget = max(1024, chat_engine.ANTHROPIC_THINK_BUDGET)
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        payload["max_tokens"] = budget + visible_tokens
    try:
        response = requests.post(
            provider["base_url"] + "/messages",
            headers={"x-api-key": provider["api_key"], "anthropic-version": chat_engine.ANTHROPIC_VERSION,
                     "content-type": "application/json"},
            json=payload,
            timeout=chat_engine.REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise VisionError(f"连不上识图模型：{exc}") from exc
    if response.status_code != 200:
        raise VisionError(f"识图模型返回错误 {response.status_code}：{response.text[:240]}")
    try:
        blocks = response.json().get("content") or []
        return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
    except (TypeError, ValueError) as exc:
        raise VisionError("识图模型没有返回可用描述。") from exc


def _clean_description(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        raise VisionError("识图模型漏掉了一张图片。")
    return text[:120]


def _parse(raw, count):
    text = str(raw or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        try:
            data = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
        except (TypeError, ValueError) as exc:
            raise VisionError("识图模型返回的格式没有接稳。") from exc
    items = data.get("images") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise VisionError("识图模型返回的格式没有接稳。")
    by_index = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= count and index not in by_index:
            by_index[index] = _clean_description(item.get("description"))
    if len(by_index) != count:
        raise VisionError("识图模型没有逐张留下完整描述。")
    return [by_index[index] for index in range(1, count + 1)]


def describe(attachments, user_text, *, provider_id, model, think=False, prompt=""):
    attachments = [item for item in attachments if isinstance(item, dict) and item.get("type") == "image"]
    if not attachments:
        return []
    provider = _provider(provider_id)
    if not provider.get("api_key"):
        raise VisionError("识图模型的供应商还没有填写 API Key。")
    model = (model or "").strip()
    if not model:
        raise VisionError("请先在“助手模型”里选择识图模型。")
    system = (prompt or "").strip() or DEFAULT_PROMPT
    if provider.get("format") == "claude":
        raw = _anthropic(provider, model, system, user_text, attachments, think)
    else:
        raw = _openai(provider, model, system, user_text, attachments, think)
    return _parse(raw, len(attachments))
