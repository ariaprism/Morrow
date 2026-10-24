"""Generate one quiet, human-readable title for a new Morrow conversation."""

import re

import requests

import chat_engine


DEFAULT_PROMPT = """请根据新对话的第一轮交流，为这段对话取一个简洁、自然、具体的中文标题。
标题要像私人聊天记录的名字，不要像报告标题；抓住真正的话题或情绪，不要使用“关于”“对话”“聊天记录”等空泛词。
只输出标题本身，不要解释，不要加引号、书名号、Markdown 或“标题：”前缀。"""


class TitleError(RuntimeError):
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


def _openai_title(provider, model, system, user, think):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "stream": False,
        # Some OpenAI-compatible reasoning routes think even when their UI
        # toggle is off. 160 tokens was close enough to the hidden reasoning
        # budget that DeepSeek could return HTTP 200 with an empty final
        # ``content``. The visible title is still cleaned to 60 characters.
        "max_tokens": 1200 if think else 800,
    }
    if think:
        payload["chat_template_kwargs"] = {"thinking": True}
    try:
        response = requests.post(
            provider["base_url"] + "/chat/completions",
            headers={
                "Authorization": "Bearer " + provider["api_key"],
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=chat_engine.REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise TitleError(f"连不上标题模型：{exc}") from exc
    if response.status_code != 200:
        raise TitleError(
            f"标题模型返回错误 {response.status_code}：{response.text[:240]}"
        )
    try:
        return response.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise TitleError("标题模型没有返回可用标题。") from exc


def _anthropic_title(provider, model, system, user, think):
    payload = {
        "model": model,
        "system": system,
        "messages": [{"role": "user", "content": user}],
        "stream": False,
        "max_tokens": 160,
    }
    if think:
        budget = max(1024, chat_engine.ANTHROPIC_THINK_BUDGET)
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
        payload["max_tokens"] = budget + 160
    try:
        response = requests.post(
            provider["base_url"] + "/messages",
            headers={
                "x-api-key": provider["api_key"],
                "anthropic-version": chat_engine.ANTHROPIC_VERSION,
                "content-type": "application/json",
            },
            json=payload,
            timeout=chat_engine.REQUEST_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise TitleError(f"连不上标题模型：{exc}") from exc
    if response.status_code != 200:
        raise TitleError(
            f"标题模型返回错误 {response.status_code}：{response.text[:240]}"
        )
    try:
        blocks = response.json().get("content") or []
        return "".join(
            block.get("text", "")
            for block in blocks
            if block.get("type") == "text"
        )
    except (TypeError, ValueError) as exc:
        raise TitleError("标题模型没有返回可用标题。") from exc


def _clean_title(value):
    text = str(value or "").strip()
    if not text:
        raise TitleError("标题模型返回了空标题。")
    line = next((item.strip() for item in text.splitlines() if item.strip()), "")
    line = re.sub(r"^#{1,6}\s*", "", line)
    line = re.sub(r"^(?:标题|title)\s*[:：]\s*", "", line, flags=re.IGNORECASE)
    line = line.strip(" \t`'\"“”‘’《》「」『』")
    line = re.sub(r"\s+", " ", line).strip()
    if not line:
        raise TitleError("标题模型返回了空标题。")
    return line[:60]


def generate(user_text, assistant_text, *, provider_id, model, think=False, prompt=""):
    provider = _provider(provider_id)
    if not provider.get("api_key"):
        raise TitleError("标题模型的供应商还没有填写 API Key。")
    model = (model or "").strip()
    if not model:
        raise TitleError("请先在“助手模型”里选择标题命名模型。")

    system = (prompt or "").strip() or DEFAULT_PROMPT
    user = (
        "【你的第一句话】\n"
        + ((user_text or "").strip()[:6000] or "（没有文字）")
        + "\n\n【Morrow的第一句回答】\n"
        + ((assistant_text or "").strip()[:8000] or "（没有文字）")
    )
    if provider.get("format") == "claude":
        raw = _anthropic_title(provider, model, system, user, think)
    else:
        raw = _openai_title(provider, model, system, user, think)
    return _clean_title(raw)
