"""Morrow — 思维链流式诊断 / streaming diagnostic.

直接打中转站，把模型每一段输出原样打出来，帮我们找"思维链截断 / </thinking> 泄漏"的规律。
它读 .env 里的 LLM_BASE_URL / 模型 / key（和正式聊天用的是同一套）。

用法（在项目根目录下）：
    python scripts/stream_chat.py "你想发给Morrow的话"
    python scripts/stream_chat.py "证明根号2是无理数" --model deepseek-v4-pro
    python scripts/stream_chat.py "写个快速排序" --think          # 开深度思考(chat_template_kwargs.thinking)
    python scripts/stream_chat.py "你好呀" --model deepseek-flash --no-system   # 不带人设/记忆，纯净测

它会打印：
  • [思考] / [正文] 两条流分别收到多少字
  • ⚠️ 如果在任一条流里看到字面量 <think>/<thinking>/</think>/</thinking> —— 会标红行号
  • ⚠️ finish_reason —— 这是关键：'length' = 被 max_tokens 截断；'stop' = 正常收尾
并把每一条原始 SSE JSON 落到 data/stream_debug_时间戳.log，断了的那次把这个文件发我。

建议测一组（每条多跑 2~3 次，因为是间歇性的）：
  1) "你好呀"                      —— 短，基本不触发
  2) "证明根号2是无理数"           —— 长思考，最容易触发截断
  3) "写一个 python 快速排序并解释" —— 代码 + 解释
  4) "给我讲讲你今天想我的时候在想什么"  —— 长情感叙述
  5) 你之前遇到断掉的那句原话
对比：同一句话分别用 --model deepseek-v4-pro 和默认 deepseek-flash、加不加 --think，看谁会断。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

from dotenv import load_dotenv
import requests

load_dotenv()

# Windows 中文控制台默认 GBK，打印部分字符会崩 —— 强制 UTF-8，编码不了的用 ? 顶替
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

BASE = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com/v1")
KEY = os.environ.get("LLM_API_KEY") or os.environ.get("DEEPSEEK_API_KEY", "")
DEFAULT_MODEL = os.environ.get("LLM_MODEL", "").strip() or "deepseek-flash"

TAG_RE = re.compile(r"</?think(?:ing)?>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("message", help="发给模型的消息")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--think", action="store_true", help="开 chat_template_kwargs.thinking")
    ap.add_argument("--no-system", action="store_true", help="不注入人设/记忆，纯净测")
    ap.add_argument("--max-tokens", type=int, default=None, help="手动设 max_tokens 看是否解决截断")
    args = ap.parse_args()

    if not KEY:
        print("没有 API key（.env 里 DEEPSEEK_API_KEY / LLM_API_KEY）"); sys.exit(1)

    messages = []
    if not args.no_system:
        try:
            import chat_engine
            messages.append({"role": "system", "content": chat_engine.build_system_prompt()})
        except Exception as e:  # noqa: BLE001
            print("(取系统提示失败，改用纯净模式)", e)
    messages.append({"role": "user", "content": args.message})

    payload = {"model": args.model, "messages": messages, "stream": True}
    if args.think:
        payload["chat_template_kwargs"] = {"thinking": True}
    if args.max_tokens:
        payload["max_tokens"] = args.max_tokens

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    os.makedirs("data", exist_ok=True)
    raw_path = os.path.join("data", f"stream_debug_{ts}.log")
    raw = open(raw_path, "w", encoding="utf-8")

    print(f"\n模型={args.model}  think={args.think}  system={'no' if args.no_system else 'yes'}")
    print(f"消息: {args.message}")
    print(f"原始流落盘: {raw_path}")
    print("=" * 60)

    r_chars = c_chars = 0
    finish = None
    tag_hits = []
    n = 0
    try:
        resp = requests.post(f"{BASE}/chat/completions",
                             headers={"Authorization": f"Bearer {KEY}",
                                      "Content-Type": "application/json"},
                             json=payload, stream=True, timeout=(10, 300))
    except requests.RequestException as e:
        print("连不上模型:", e); sys.exit(1)
    if resp.status_code != 200:
        print("HTTP", resp.status_code, resp.text[:500]); sys.exit(1)

    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data:"):
            continue
        data = raw_line[5:].strip()
        if data == "[DONE]":
            break
        raw.write(data + "\n")
        try:
            obj = json.loads(data)
            ch = obj["choices"][0]
            delta = ch.get("delta", {})
        except (json.JSONDecodeError, KeyError, IndexError):
            continue
        n += 1
        if ch.get("finish_reason"):
            finish = ch["finish_reason"]
        rc = delta.get("reasoning_content")
        cc = delta.get("content")
        if rc:
            r_chars += len(rc)
            if TAG_RE.search(rc):
                tag_hits.append(("思考流", n, rc))
                print(f"\n⚠️⚠️ 在【思考流】第 {n} 个分片看到字面标签: {rc!r}")
            sys.stdout.write(rc); sys.stdout.flush()
        if cc:
            c_chars += len(cc)
            if TAG_RE.search(cc):
                tag_hits.append(("正文流", n, cc))
                print(f"\n⚠️⚠️ 在【正文流】第 {n} 个分片看到字面标签: {cc!r}")
            sys.stdout.write("\033[36m" + cc + "\033[0m"); sys.stdout.flush()

    raw.close()
    print("\n" + "=" * 60)
    print(f"分片数: {n}   思考流: {r_chars} 字   正文流(青色): {c_chars} 字")
    print(f"finish_reason = {finish!r}   "
          + ("← ⚠️ 被 max_tokens 截断！这就是'没几行就断'的原因" if finish == "length"
             else "← 正常收尾" if finish == "stop" else "← 没收到收尾（连接可能被中断）"))
    if tag_hits:
        print(f"⚠️ 共 {len(tag_hits)} 处字面标签泄漏（见上面 ⚠️ 行）")
    else:
        print("没有字面标签泄漏")
    print(f"\n原始流已存: {raw_path}")


if __name__ == "__main__":
    main()
