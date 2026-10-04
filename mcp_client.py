"""Minimal MCP HTTP client: Streamable HTTP first, legacy SSE fallback."""

import json
from urllib.parse import urljoin, urlparse

import requests


PROTOCOL_VERSION = "2025-03-26"
CLIENT_INFO = {"name": "nook", "version": "1.0"}


class MCPError(RuntimeError):
    pass


class MCPTransportMismatch(MCPError):
    """The configured URL looks like a legacy HTTP+SSE endpoint."""


def _rpc(method, params=None, request_id=1):
    body = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        body["id"] = request_id
    if params is not None:
        body["params"] = params
    return body


def _json_from_response(resp, request_id=None):
    ctype = (resp.headers.get("content-type") or "").lower()
    if "text/event-stream" not in ctype:
        try:
            data = resp.json()
        except ValueError as exc:
            raise MCPError("MCP 返回的不是 JSON") from exc
        if isinstance(data, dict) and data.get("error"):
            raise MCPError(data["error"].get("message") or "MCP 请求失败")
        return data
    for raw in _sse_lines(resp):
        if not raw or not raw.startswith("data:"):
            continue
        try:
            data = json.loads(raw[5:].strip())
        except ValueError:
            continue
        if request_id is None or data.get("id") == request_id:
            if data.get("error"):
                raise MCPError(data["error"].get("message") or "MCP 请求失败")
            return data
    raise MCPError("MCP SSE 没有返回结果")


def _sse_lines(resp):
    """按 HTTP 原始换行拆 SSE，再解 UTF-8；避免 requests 用 latin-1 拆坏中文 JSON。"""
    for raw in resp.iter_lines(decode_unicode=False):
        if isinstance(raw, bytes):
            yield raw.decode("utf-8", errors="replace")
        else:
            yield raw


def _initialize_payload(request_id=1):
    return _rpc("initialize", {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {},
        "clientInfo": CLIENT_INFO,
    }, request_id)


def _request_headers(access_token="", **headers):
    out = dict(headers)
    if access_token:
        out["Authorization"] = "Bearer " + access_token
    return out


def _same_origin(first, second):
    a, b = urlparse(first), urlparse(second)
    return (a.scheme.lower(), a.hostname, a.port) == (b.scheme.lower(), b.hostname, b.port)


def _streamable_call(url, method, params=None, timeout=20, access_token=""):
    session = requests.Session()
    headers = _request_headers(
        access_token,
        Accept="application/json, text/event-stream",
        **{"Content-Type": "application/json"},
    )
    init = session.post(url, json=_initialize_payload(), headers=headers, timeout=timeout, stream=True)
    if init.status_code >= 400:
        message = f"HTTP {init.status_code}: {init.text[:160]}"
        if init.status_code in {400, 404, 405}:
            raise MCPTransportMismatch(message)
        raise MCPError(message)
    init_data = _json_from_response(init, 1)
    sid = init.headers.get("Mcp-Session-Id")
    if sid:
        headers["Mcp-Session-Id"] = sid
    session.post(url, json=_rpc("notifications/initialized", request_id=None), headers=headers, timeout=timeout)
    resp = session.post(url, json=_rpc(method, params, 2), headers=headers, timeout=timeout, stream=True)
    if resp.status_code >= 400:
        raise MCPError(f"HTTP {resp.status_code}: {resp.text[:160]}")
    data = _json_from_response(resp, 2)
    return data.get("result") or {}, "http"


def _streamable_probe(url, timeout=20, access_token=""):
    session = requests.Session()
    headers = _request_headers(
        access_token,
        Accept="application/json, text/event-stream",
        **{"Content-Type": "application/json"},
    )
    sid = ""
    try:
        init = session.post(
            url, json=_initialize_payload(), headers=headers, timeout=timeout, stream=True
        )
        if init.status_code >= 400:
            message = f"HTTP {init.status_code}: {init.text[:160]}"
            if init.status_code in {400, 404, 405}:
                raise MCPTransportMismatch(message)
            raise MCPError(message)
        _json_from_response(init, 1)
        sid = init.headers.get("Mcp-Session-Id") or ""
        if sid:
            headers["Mcp-Session-Id"] = sid
        ready = session.post(
            url,
            json=_rpc("notifications/initialized", request_id=None),
            headers=headers,
            timeout=timeout,
        )
        if ready.status_code >= 400:
            raise MCPError(f"HTTP {ready.status_code}: {ready.text[:160]}")
        return "http"
    finally:
        if sid:
            try:
                session.delete(url, headers=headers, timeout=timeout)
            except requests.RequestException:
                pass
        session.close()


def _read_legacy_event(lines, wanted_id=None):
    event = "message"
    for raw in lines:
        if not raw:
            event = "message"
            continue
        if raw.startswith("event:"):
            event = raw[6:].strip()
        elif raw.startswith("data:"):
            value = raw[5:].strip()
            if event == "endpoint":
                return "endpoint", value
            try:
                data = json.loads(value)
            except ValueError:
                continue
            if wanted_id is None or data.get("id") == wanted_id:
                if data.get("error"):
                    raise MCPError(data["error"].get("message") or "MCP 请求失败")
                return "message", data
    raise MCPError("MCP SSE 连接提前关闭")


def _legacy_sse_call(url, method, params=None, timeout=20, access_token=""):
    session = requests.Session()
    stream_headers = _request_headers(access_token, Accept="text/event-stream")
    stream = session.get(url, headers=stream_headers, timeout=timeout, stream=True)
    if stream.status_code >= 400:
        raise MCPError(f"SSE HTTP {stream.status_code}: {stream.text[:160]}")
    lines = _sse_lines(stream)
    kind, endpoint = _read_legacy_event(lines)
    if kind != "endpoint":
        raise MCPError("SSE 没有提供消息端点")
    post_url = urljoin(url, endpoint)
    if access_token and not _same_origin(url, post_url):
        raise MCPError("SSE 消息端点跨域，已停止转发访问密钥")
    headers = _request_headers(access_token, **{"Content-Type": "application/json"})
    resp = session.post(post_url, json=_initialize_payload(), headers=headers, timeout=timeout)
    if resp.status_code >= 400:
        raise MCPError(f"SSE 初始化失败：HTTP {resp.status_code}")
    _, init_data = _read_legacy_event(lines, 1)
    session.post(post_url, json=_rpc("notifications/initialized", request_id=None), headers=headers, timeout=timeout)
    resp = session.post(post_url, json=_rpc(method, params, 2), headers=headers, timeout=timeout)
    if resp.status_code >= 400:
        raise MCPError(f"SSE 请求失败：HTTP {resp.status_code}")
    _, data = _read_legacy_event(lines, 2)
    stream.close()
    return data.get("result") or {}, "sse"


def call(url, method, params=None, timeout=20, access_token=""):
    url = (url or "").strip()
    access_token = (access_token or "").strip()
    if not url.startswith(("http://", "https://")):
        raise MCPError("服务器 URL 必须以 http:// 或 https:// 开头")
    try:
        return _streamable_call(url, method, params, timeout, access_token)
    except MCPTransportMismatch as first:
        try:
            return _legacy_sse_call(url, method, params, timeout, access_token)
        except (MCPError, requests.RequestException) as second:
            raise MCPError(f"自动连接失败：{first}；SSE：{second}") from second


def list_tools(url, timeout=20, access_token=""):
    result, transport = call(url, "tools/list", {}, timeout, access_token)
    tools = result.get("tools") if isinstance(result, dict) else []
    return tools or [], transport


def probe(url, timeout=20, access_token=""):
    """Verify MCP initialization without reading or changing tool configuration."""
    url = (url or "").strip()
    access_token = (access_token or "").strip()
    if not url.startswith(("http://", "https://")):
        raise MCPError("服务器 URL 必须以 http:// 或 https:// 开头")
    try:
        return _streamable_probe(url, timeout, access_token)
    except MCPTransportMismatch as first:
        try:
            _result, transport = _legacy_sse_call(url, "ping", {}, timeout, access_token)
            return transport
        except (MCPError, requests.RequestException) as second:
            raise MCPError(f"自动连接失败：{first}；SSE：{second}") from second


def call_tool(url, name, arguments=None, timeout=30, access_token=""):
    result, transport = call(
        url, "tools/call", {"name": name, "arguments": arguments or {}}, timeout, access_token
    )
    if result.get("isError"):
        text = _content_text(result.get("content")) or "MCP 工具调用失败"
        raise MCPError(text)
    return _content_text(result.get("content")) or json.dumps(result, ensure_ascii=False), transport


def read_resource(url, uri, timeout=30, access_token=""):
    """Read one MCP text resource without exposing it as a model-callable tool."""
    result, transport = call(
        url, "resources/read", {"uri": str(uri or "")}, timeout, access_token
    )
    text = _resource_text(result.get("contents"))
    if not text:
        raise MCPError("MCP 资源没有返回文本内容")
    return text, transport


def _content_text(content):
    parts = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text") or ""))
    return "\n".join(x for x in parts if x).strip()


def _resource_text(contents):
    parts = []
    for item in contents or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts).strip()
