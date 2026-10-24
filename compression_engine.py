"""Incremental conversation summaries kept behind the chat UI."""

import time

import requests

import chat_engine


DEFAULT_PROMPT = """你是Morrow墙后的对话整理助手。请把“已有累计摘要”和“本次新增原文”合并成一份可继续用于后续聊天的累计摘要。

已有摘要是更早原文的有损浓缩，不需要逐句照抄。每次更新时，可以继续合并重复信息，淡化已经结束、低价值或强时效性的琐碎细节；但不要仅仅因为事情发生得早，就删掉人物关系、稳定偏好、长期事实、重要情绪节点、承诺与约定、尚未完成的事项。

根据原文中的时间判断信息先后。内容发生冲突时以较新的信息为准；如果变化过程本身对理解关系或后续对话有意义，就简洁保留变化。

保留你与Morrow的身份、关键因果和必要时间线。不要评价用户，不要虚构缺失信息，不要输出思考过程，也不要添加“摘要如下”、[Summary] 或其他技术标签。直接输出自然、清楚的摘要正文。"""


class CompressionError(RuntimeError):
    def __init__(self, message, *, usage_items=None):
        super().__init__(message)
        self.usage_items = list(usage_items or [])


class SummaryResult(str):
    """String-compatible summary carrying wall-side billing metadata."""

    def __new__(cls, content, usage_items=None):
        value = super().__new__(cls, content)
        value.usage_items = list(usage_items or [])
        return value


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


def _output_budgets(target_tokens):
    # Some OpenAI-compatible reasoning routes spend max_tokens on hidden thought
    # even with the visible thinking toggle off. Leave enough room for the asked
    # summary, then retry once only when the provider explicitly reports a cap.
    first = max(4000, min(8000, int(target_tokens) * 2))
    second = min(16000, max(first + 2000, first * 2))
    return [first] if second == first else [first, second]


def _usage_item(provider, model, usage):
    return {
        "provider": {
            "id": provider.get("id"),
            "name": provider.get("name") or "",
            "format": provider.get("format") or "openai",
        },
        "format": provider.get("format") or "openai",
        "model": model,
        "usage": usage if isinstance(usage, dict) else {},
    }


def _log_request(provider, model, endpoint, payload, started, *, ok, status_code=None,
                 error_type="", error=""):
    chat_engine._log_http_request(
        provider, model, endpoint, ok, status_code=status_code,
        elapsed_ms=int((time.monotonic() - started) * 1000),
        error_type=error_type, error=error,
        req_bytes=chat_engine._payload_size(payload),
        msg_count=len(payload.get("messages") or []),
    )


def _openai_summary(provider, model, system, user, think, target_tokens):
    endpoint = provider["base_url"] + "/chat/completions"
    usage_items = []
    for attempt, budget in enumerate(_output_budgets(target_tokens)):
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "max_tokens": budget,
        }
        if think:
            payload["chat_template_kwargs"] = {"thinking": True}
        started = time.monotonic()
        try:
            response = requests.post(
                endpoint,
                headers={"Authorization": "Bearer " + provider["api_key"], "Content-Type": "application/json"},
                json=payload,
                timeout=chat_engine.REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            _log_request(provider, model, endpoint, payload, started, ok=False,
                         error_type=type(exc).__name__, error=str(exc))
            raise CompressionError(f"连不上压缩模型：{exc}", usage_items=usage_items) from exc
        if response.status_code != 200:
            detail = response.text[:240]
            _log_request(provider, model, endpoint, payload, started, ok=False,
                         status_code=response.status_code, error=detail)
            raise CompressionError(
                f"压缩模型返回错误 {response.status_code}：{detail}", usage_items=usage_items
            )
        try:
            body = response.json()
            choice = body["choices"][0]
            content = choice["message"]["content"]
            usage = body.get("usage") or {}
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            _log_request(provider, model, endpoint, payload, started, ok=False,
                         status_code=response.status_code, error_type="InvalidResponse",
                         error="压缩模型没有返回可用摘要")
            raise CompressionError("压缩模型没有返回可用摘要。", usage_items=usage_items) from exc
        if usage:
            usage_items.append(_usage_item(provider, model, usage))
        finish_reason = str(choice.get("finish_reason") or "").lower()
        truncated = finish_reason in {"length", "max_tokens", "max_output_tokens"}
        _log_request(
            provider, model, endpoint, payload, started, ok=not truncated,
            status_code=response.status_code,
            error_type="OutputTruncated" if truncated else "",
            error=(f"finish_reason={finish_reason}; max_tokens={budget}" if truncated else ""),
        )
        if truncated and attempt + 1 < len(_output_budgets(target_tokens)):
            continue
        if truncated:
            raise CompressionError(
                "压缩模型连续两次写到输出上限，Morrow 没有保存这份半截摘要。",
                usage_items=usage_items,
            )
        return SummaryResult((content or "").strip(), usage_items)


def _anthropic_summary(provider, model, system, user, think, target_tokens):
    endpoint = provider["base_url"] + "/messages"
    usage_items = []
    for attempt, output_budget in enumerate(_output_budgets(target_tokens)):
        payload = {
            "model": model,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "stream": False,
            "max_tokens": output_budget,
        }
        if think:
            budget = max(1024, chat_engine.ANTHROPIC_THINK_BUDGET)
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = budget + output_budget
        started = time.monotonic()
        try:
            response = requests.post(
                endpoint,
                headers={"x-api-key": provider["api_key"], "anthropic-version": chat_engine.ANTHROPIC_VERSION,
                         "content-type": "application/json"},
                json=payload,
                timeout=chat_engine.REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            _log_request(provider, model, endpoint, payload, started, ok=False,
                         error_type=type(exc).__name__, error=str(exc))
            raise CompressionError(f"连不上压缩模型：{exc}", usage_items=usage_items) from exc
        if response.status_code != 200:
            detail = response.text[:240]
            _log_request(provider, model, endpoint, payload, started, ok=False,
                         status_code=response.status_code, error=detail)
            raise CompressionError(
                f"压缩模型返回错误 {response.status_code}：{detail}", usage_items=usage_items
            )
        try:
            body = response.json()
            blocks = body.get("content") or []
            content = "".join(block.get("text", "") for block in blocks if block.get("type") == "text")
            usage = body.get("usage") or {}
        except (TypeError, ValueError) as exc:
            _log_request(provider, model, endpoint, payload, started, ok=False,
                         status_code=response.status_code, error_type="InvalidResponse",
                         error="压缩模型没有返回可用摘要")
            raise CompressionError("压缩模型没有返回可用摘要。", usage_items=usage_items) from exc
        if usage:
            usage_items.append(_usage_item(provider, model, usage))
        stop_reason = str(body.get("stop_reason") or "").lower()
        truncated = stop_reason in {"max_tokens", "length"}
        _log_request(
            provider, model, endpoint, payload, started, ok=not truncated,
            status_code=response.status_code,
            error_type="OutputTruncated" if truncated else "",
            error=(f"stop_reason={stop_reason}; max_tokens={payload['max_tokens']}" if truncated else ""),
        )
        if truncated and attempt + 1 < len(_output_budgets(target_tokens)):
            continue
        if truncated:
            raise CompressionError(
                "压缩模型连续两次写到输出上限，Morrow 没有保存这份半截摘要。",
                usage_items=usage_items,
            )
        return SummaryResult(content.strip(), usage_items)


def summarize(old_summary, messages, *, provider_id, model, think=False, prompt="",
              instruction="", target_tokens=2000):
    provider = _provider(provider_id)
    if not provider.get("api_key"):
        raise CompressionError("压缩模型的供应商还没有填写 API Key。")
    model = (model or "").strip()
    if not model:
        raise CompressionError("请先在“助手模型”里选择压缩模型。")

    transcript = []
    for message in messages:
        role = "你" if message["role"] == "user" else "Morrow"
        created_at = (message["created_at"] or "").strip() if "created_at" in message.keys() else ""
        stamp = f"{created_at} · " if created_at else ""
        transcript.append(f"[{stamp}{role} · 消息 {message['id']}]\n{message['content'] or ''}")
    parts = ["【已有累计摘要】\n" + (old_summary.strip() if old_summary else "（这是第一次整理，没有旧摘要）"),
             "【本次新增原文】\n" + "\n\n".join(transcript)]
    if instruction.strip():
        parts.append("【本次附加说明】\n" + instruction.strip())
    system = (prompt or "").strip() or DEFAULT_PROMPT
    user = "\n\n".join(parts) + f"\n\n请将新摘要控制在约 {target_tokens} Token，直接输出新摘要正文。"
    if provider.get("format") == "claude":
        summary = _anthropic_summary(provider, model, system, user, think, target_tokens)
    else:
        summary = _openai_summary(provider, model, system, user, think, target_tokens)
    if not summary:
        raise CompressionError(
            "压缩模型返回了空摘要。",
            usage_items=getattr(summary, "usage_items", []),
        )
    return summary
