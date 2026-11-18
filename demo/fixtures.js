// Fictional examples generated from an isolated temporary database.
window.MORROW_DEMO_FIXTURES = {
  "/api/identity": {
    "assistant_name": "Morrow",
    "assistant_name_set": true,
    "assistant_pronoun": "ta",
    "brand_name": "Morrow",
    "user_name": "小满",
    "user_name_set": true
  },
  "/api/together-date": {
    "date": "2026-08-01"
  },
  "/api/home-postcard": {
    "design": "tomorrow",
    "enabled": true,
    "quotes": {
      "leaf": "",
      "light": "",
      "tomorrow": ""
    }
  },
  "/api/home-greetings": {
    "common": "",
    "evening": "",
    "mode": "timed",
    "morning": "",
    "noon": ""
  },
  "/api/memory/persona": {
    "content": "这是虚构的演示小家。小满和 Morrow 在这里聊日常，记录植物、饭菜与散步时的念头。",
    "updated_at": "2026-11-18 09:15:19"
  },
  "/api/memory/entries": [
    {
      "content": "散步的时候喜欢走安静的小路，偶尔在路边停下来拍树影。",
      "created_at": "2026-11-18 09:15:19",
      "id": 4,
      "kind": "fixed",
      "pinned": 0,
      "tags": [],
      "updated_at": "2026-11-18 09:15:19"
    },
    {
      "content": "番茄炒蛋会留一些汤汁拌饭，煎蛋喜欢边缘脆脆的。",
      "created_at": "2026-11-18 09:15:19",
      "id": 3,
      "kind": "dynamic",
      "pinned": 0,
      "tags": [
        "吃饭"
      ],
      "updated_at": "2026-11-18 09:15:19"
    },
    {
      "content": "窗台上养着一盆薄荷，最近长出了两片新叶子。",
      "created_at": "2026-11-18 09:15:19",
      "id": 2,
      "kind": "dynamic",
      "pinned": 0,
      "tags": [
        "植物",
        "日常"
      ],
      "updated_at": "2026-11-18 09:15:19"
    },
    {
      "content": "喜欢奶油白的花和透明玻璃花瓶，花放在窗边。",
      "created_at": "2026-11-18 09:15:19",
      "id": 1,
      "kind": "fixed",
      "pinned": 0,
      "tags": [],
      "updated_at": "2026-11-18 09:15:19"
    }
  ],
  "/api/memory/recent": [],
  "/api/notes": {
    "content": "把今天的阳光夹进书里，阴天的时候再打开。",
    "created_at": "2026-11-18 09:15:19",
    "fixed": false,
    "id": 5
  },
  "/api/notes/all": [
    {
      "author": "assistant",
      "content": "把今天的阳光夹进书里，阴天的时候再打开。",
      "created_at": "2026-11-18 09:15:19",
      "id": 5
    },
    {
      "author": "assistant",
      "content": "晚饭留了一个番茄，明天可以煮热乎乎的面。",
      "created_at": "2026-11-18 09:15:19",
      "id": 4
    },
    {
      "author": "assistant",
      "content": "薄荷今天又多长了一点。你也是。",
      "created_at": "2026-11-18 09:15:19",
      "id": 3
    },
    {
      "author": "assistant",
      "content": "花瓶里的水记得换。也记得给自己倒一杯。",
      "created_at": "2026-11-18 09:15:19",
      "id": 2
    },
    {
      "author": "assistant",
      "content": "你不在的时候，我也有一些话想告诉你。",
      "created_at": "2026-11-18 09:15:19",
      "id": 1
    }
  ],
  "/api/notes/latest": {
    "author": "assistant",
    "content": "把今天的阳光夹进书里，阴天的时候再打开。",
    "created_at": "2026-11-18 09:15:19",
    "id": 5,
    "is_new": true,
    "total": 5
  },
  "/api/todos": [
    {
      "created_at": "2026-11-18 09:15:19",
      "done": 0,
      "done_at": null,
      "id": 1,
      "text": "给薄荷拍一张成长照片"
    },
    {
      "created_at": "2026-11-18 09:15:19",
      "done": 0,
      "done_at": null,
      "id": 2,
      "text": "周末去花店买一束洋桔梗"
    },
    {
      "created_at": "2026-11-18 09:15:19",
      "done": 0,
      "done_at": null,
      "id": 3,
      "text": "把洗好的杯子放回窗边"
    }
  ],
  "/api/diary": [
    {
      "author": "user",
      "comments": [],
      "content": "晚饭做了番茄炒蛋。吃完开窗，风把楼下的饭菜香送进来。原来普通的一天也有这么多可以记下来的东西。",
      "created_at": "2026-11-18 09:15:19",
      "favorite": false,
      "id": 2,
      "tag": "anchor",
      "updated_at": "2026-11-18 09:15:19"
    },
    {
      "author": "user",
      "comments": [],
      "content": "薄荷的新叶子是很浅的绿，阳光从叶背透过来。今天花了十分钟只看着它，竟然也觉得很充实。",
      "created_at": "2026-11-18 09:15:19",
      "favorite": false,
      "id": 1,
      "tag": "light",
      "updated_at": "2026-11-18 09:15:19"
    }
  ],
  "/api/diary/latest": {
    "created_at": "2026-11-18 09:15:19",
    "id": 2
  },
  "/api/providers": [
    {
      "id": 1,
      "name": "演示提供商（预设回复）",
      "format": "openai",
      "api_key": "",
      "api_url": "https://example.test/v1",
      "models": [
        {
          "id": "demo",
          "name": "演示模式"
        }
      ],
      "cache": 0,
      "cache_mode": "none"
    }
  ],
  "/api/mcp-servers": [],
  "/api/assistant-settings": {
    "compress": {
      "model": "",
      "model_name": "",
      "prompt": "你是Morrow墙后的对话整理助手。请把“已有累计摘要”和“本次新增原文”合并成一份可继续用于后续聊天的累计摘要。\n\n已有摘要是更早原文的有损浓缩，不需要逐句照抄。每次更新时，可以继续合并重复信息，淡化已经结束、低价值或强时效性的琐碎细节；但不要仅仅因为事情发生得早，就删掉人物关系、稳定偏好、长期事实、重要情绪节点、承诺与约定、尚未完成的事项。\n\n根据原文中的时间判断信息先后。内容发生冲突时以较新的信息为准；如果变化过程本身对理解关系或后续对话有意义，就简洁保留变化。\n\n保留你与Morrow的身份、关键因果和必要时间线。不要评价用户，不要虚构缺失信息，不要输出思考过程，也不要添加“摘要如下”、[Summary] 或其他技术标签。直接输出自然、清楚的摘要正文。",
      "provider_id": null,
      "recent_message_count": 20,
      "think": false
    },
    "night": {
      "format": "",
      "model": "",
      "model_name": "",
      "prompt": "",
      "provider_id": null,
      "think": false
    },
    "title": {
      "model": "",
      "model_name": "",
      "prompt": "请根据新对话的第一轮交流，为这段对话取一个简洁、自然、具体的中文标题。\n标题要像私人聊天记录的名字，不要像报告标题；抓住真正的话题或情绪，不要使用“关于”“对话”“聊天记录”等空泛词。\n只输出标题本身，不要解释，不要加引号、书名号、Markdown 或“标题：”前缀。",
      "provider_id": null,
      "think": false
    },
    "vision": {
      "enabled": false,
      "model": "",
      "model_name": "",
      "prompt": "为每张图片留下一句简短的“看过后的记忆”，供未来对话在不再携带原图时理解上下文。\n\n只记录最有辨识度、最可能在后续被提及的内容，不做完整画面解说，不评价，不回复用户，不猜测发送意图。结合用户随图片发送的文字理解重点，但不要重复用户已经写明的内容。\n\n简单图片通常用一句话、约 20～50 个中文字；内容较多时可以稍长。不要为了达到某个长度补充无关的颜色、构图、背景和装饰细节。\n\n遇到文章、聊天记录、网页、表格等大量文字时，不要全文转录，只概括主题并保留少量会影响后续交流的关键名称、数字、日期、结论或异常信息。\n\n每张图片最多 120 个中文字，能更短说清就更短。看不清或不确定的内容不要猜测。\n\n严格返回 JSON：{\"images\":[{\"index\":1,\"description\":\"...\"}]}。每张输入图片必须对应一个结果，不得合并、遗漏或改变顺序。",
      "provider_id": null,
      "session_ids": [],
      "think": false
    }
  },
  "/api/study/settings": {
    "cache_ttl": "off",
    "configured": false,
    "context_limit": 20,
    "enabled": false,
    "handbook_name": "",
    "handbook_updated_at": "",
    "handbooks": [],
    "image_limit": null,
    "latest_images_only": false,
    "model": "",
    "model_name": "",
    "next_wake": null,
    "pending_recovery": false,
    "provider_id": null,
    "sessions": [
      {
        "id": 3,
        "title": "薄荷长出新叶子了"
      },
      {
        "id": 2,
        "title": "今天也好好吃饭"
      },
      {
        "id": 1,
        "title": "周末去买一束花"
      }
    ],
    "target_session_id": 3,
    "think": false,
    "user_note": "",
    "wake_prompt": "你醒了。现在是属于你的自由活动时间。可以看看最近发生的事，做一点自然想做的事；没有值得做的事情，也可以安静结束。"
  },
  "/api/study/runs": [],
  "/api/alarms/settings": {
    "cache_ttl": "off",
    "context_limit": 20,
    "enabled": false,
    "image_limit": null,
    "latest_images_only": false,
    "model": "",
    "pending_count": 0,
    "provider_id": null,
    "sessions": [
      {
        "id": 3,
        "title": "薄荷长出新叶子了"
      },
      {
        "id": 2,
        "title": "今天也好好吃饭"
      },
      {
        "id": 1,
        "title": "周末去买一束花"
      }
    ],
    "target_session_id": 3,
    "think": false
  },
  "/api/alarms": [],
  "/api/proactive/settings": {
    "cache_ttl": "off",
    "context_limit": null,
    "enabled": false,
    "image_limit": null,
    "job": null,
    "latest_images_only": false,
    "model": "",
    "model_name": "",
    "provider_id": null,
    "sessions": [
      {
        "id": 3,
        "title": "薄荷长出新叶子了"
      },
      {
        "id": 2,
        "title": "今天也好好吃饭"
      },
      {
        "id": 1,
        "title": "周末去买一束花"
      }
    ],
    "target_session_id": 3,
    "think": false,
    "unread_count": 0,
    "unread_session_ids": []
  },
  "/api/proactive/status": {
    "cache_ttl": "off",
    "context_limit": null,
    "enabled": false,
    "image_limit": null,
    "job": null,
    "latest_images_only": false,
    "model": "",
    "model_name": "",
    "provider_id": null,
    "sessions": [
      {
        "id": 3,
        "title": "薄荷长出新叶子了"
      },
      {
        "id": 2,
        "title": "今天也好好吃饭"
      },
      {
        "id": 1,
        "title": "周末去买一束花"
      }
    ],
    "target_session_id": 3,
    "think": false,
    "unread_count": 0,
    "unread_session_ids": []
  },
  "/api/auto-compression": {
    "enabled": false,
    "instruction": "",
    "min_batch_tokens": 0,
    "paused_sessions": {},
    "recent_message_count": 20,
    "session_ids": [],
    "sessions": [
      {
        "archived": 0,
        "id": 3,
        "title": "薄荷长出新叶子了"
      },
      {
        "archived": 0,
        "id": 2,
        "title": "今天也好好吃饭"
      },
      {
        "archived": 0,
        "id": 1,
        "title": "周末去买一束花"
      }
    ],
    "target_tokens": 2000,
    "threshold_messages": 100,
    "threshold_tokens": 60000
  },
  "/api/vision-status": {
    "enabled": false,
    "failed": 0,
    "pending": 0,
    "ready": 0,
    "session_ids": [],
    "sessions": [
      {
        "archived": 0,
        "id": 3,
        "title": "薄荷长出新叶子了"
      },
      {
        "archived": 0,
        "id": 2,
        "title": "今天也好好吃饭"
      },
      {
        "archived": 0,
        "id": 1,
        "title": "周末去买一束花"
      }
    ],
    "total": 0
  },
  "/api/chat/tool-visibility": {
    "servers": [],
    "sticker": {
      "visible": false
    },
    "voice_letter": {
      "visible": false
    }
  },
  "/api/stickers": [],
  "/api/stickers/meta": {
    "series": [],
    "tags": []
  },
  "/api/tts/settings": {
    "api_key": "",
    "api_url": "https://example.test",
    "has_api_key": false,
    "model": "eleven_v3",
    "name": "ElevenLabs",
    "similarity_boost": 0.75,
    "speed": 0.88,
    "stability": 0.45,
    "style": 0.15,
    "use_speaker_boost": true,
    "voice_id": "",
    "voice_name": ""
  },
  "/api/keepsakes": [],
  "/api/favorites": [],
  "/api/tidy": [],
  "/api/tidy/summaries": [],
  "/api/artifacts": [],
  "/api/usage": [],
  "/api/usage/month": {
    "cache_creation_input_tokens": 0,
    "cache_hit_rate": 0,
    "cache_hit_requests": 0,
    "cache_read_input_tokens": 0,
    "cache_total_requests": 0,
    "cost_usd": 0,
    "input_tokens": 0,
    "output_tokens": 0,
    "requests": 0,
    "total_tokens": 0
  },
  "/api/http-logs": [],
  "/api/ui-preview": {
    "empty": false
  },
  "/api/version": {
    "commit": "",
    "commit_short": "",
    "version": "v0.2.0"
  },
  "/api/push/status": {
    "supported": false,
    "enabled": false,
    "public_key": "",
    "preview": false
  },
  "/api/weather/city": {
    "city": "",
    "key_configured": false
  },
  "/api/cycle": {
    "intimacy": [],
    "period_starts": [],
    "prediction": {
      "cycle_days": 28,
      "ovulation_offset": 14,
      "period_days": 5
    },
    "private_notes": [],
    "records": []
  },
  "/api/chat/sessions": [
    {
      "archived": 0,
      "continued_from_session_id": null,
      "created_at": "2026-11-15 10:30:00",
      "id": 3,
      "pinned": 0,
      "title": "薄荷长出新叶子了",
      "updated_at": "2026-11-15 10:30:00"
    },
    {
      "archived": 0,
      "continued_from_session_id": null,
      "created_at": "2026-11-14 10:30:00",
      "id": 2,
      "pinned": 0,
      "title": "今天也好好吃饭",
      "updated_at": "2026-11-14 10:30:00"
    },
    {
      "archived": 0,
      "continued_from_session_id": null,
      "created_at": "2026-11-13 10:30:00",
      "id": 1,
      "pinned": 0,
      "title": "周末去买一束花",
      "updated_at": "2026-11-13 10:30:00"
    }
  ],
  "/api/chat/messages/1": [
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "路过花店，看见门口摆了好多洋桔梗。",
      "created_at": "2026-11-13 10:30:00",
      "id": 1,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "user",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "那就给周末留一束吧。放在窗边，吃早餐的时候也能看见。",
      "created_at": "2026-11-13 10:31:00",
      "id": 2,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "assistant",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "想买奶油白的，再配两枝绿色的叶子。",
      "created_at": "2026-11-13 10:32:00",
      "id": 3,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "user",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "已经能想象那个小角落了：玻璃花瓶，奶油白的花，还有一小块慢慢移动的阳光。",
      "created_at": "2026-11-13 10:33:00",
      "id": 4,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "assistant",
      "versions": null
    }
  ],
  "/api/chat/messages/2": [
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "冰箱里还有番茄和鸡蛋，晚饭就这么解决啦。",
      "created_at": "2026-11-14 10:30:00",
      "id": 5,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "user",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "番茄多炒一会儿，汤汁拌饭很好吃。吃完把碗泡上，先去窗边坐一会儿。",
      "created_at": "2026-11-14 10:31:00",
      "id": 6,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "assistant",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "我还给自己煎了一个边边脆脆的蛋。",
      "created_at": "2026-11-14 10:32:00",
      "id": 7,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "user",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "那今晚的饭有两种鸡蛋，待遇很不错。给认真吃饭的小满留一颗小星星。",
      "created_at": "2026-11-14 10:33:00",
      "id": 8,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "assistant",
      "versions": null
    }
  ],
  "/api/chat/messages/3": [
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "早上给薄荷浇水，发现它冒了两片新叶子。小小的，好可爱。",
      "created_at": "2026-11-15 10:30:00",
      "id": 9,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "user",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "看来它很喜欢窗边这个位置。两片小叶子，像今天悄悄递过来的一封短信。",
      "created_at": "2026-11-15 10:31:00",
      "id": 10,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "assistant",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "昨天还没有呢，感觉每天都能发现一点变化。",
      "created_at": "2026-11-15 10:32:00",
      "id": 11,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "user",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "给它拍张照片吧。等长大以后回头看，会发现这些很小的日子也有自己的刻度。",
      "created_at": "2026-11-15 10:33:00",
      "id": 12,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "assistant",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "今天太阳也很好，想把被子抱出去晒。",
      "created_at": "2026-11-15 10:34:00",
      "id": 13,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "user",
      "versions": null
    },
    {
      "active": 0,
      "artifacts": [],
      "attachments": [],
      "content": "去吧，把阳光一起收进被子里。晚上回来，就有一小块白天可以抱着睡了。",
      "created_at": "2026-11-15 10:35:00",
      "id": 14,
      "proactive": 0,
      "proactive_unread": 0,
      "reasoning": "",
      "reasoning_ms": null,
      "role": "assistant",
      "versions": null
    }
  ],
  "/api/chat/window/3": {
    "continued_from": null,
    "continued_summary_through_message_id": null,
    "first_message": {
      "content": "早上给薄荷浇水，发现它冒了两片新叶子。小小的，好可爱。",
      "created_at": "2026-11-15 10:30:00",
      "id": 9,
      "role": "user"
    },
    "gap_context_count": 0,
    "gap_message_count": 0,
    "has_summary": false,
    "last_message": {
      "content": "去吧，把阳光一起收进被子里。晚上回来，就有一小块白天可以抱着睡了。",
      "created_at": "2026-11-15 10:35:00",
      "id": 14,
      "role": "assistant"
    },
    "messages_after_summary_count": 0,
    "needs_summary": false,
    "recent_message_count": 6,
    "requested_recent_message_count": 20,
    "session_id": 3,
    "summary": "",
    "summary_covered_through_message_id": null,
    "summary_updated_at": "",
    "title": "薄荷长出新叶子了",
    "total_message_count": 6
  },
  "/api/chat/compression/3": {
    "auto_pause": null,
    "auto_paused": false,
    "covered_message_count": 0,
    "covered_through_message_id": null,
    "estimated_context_tokens": 153,
    "has_summary": false,
    "model": "",
    "raw_overlap_count": 0,
    "recent_message_count": 20,
    "summary": "",
    "target_tokens": 2000,
    "total_message_count": 6,
    "updated_at": ""
  },
  "/api/auth/check": {
    "authed": true
  },
  "/api/weather": {
    "configured": false,
    "city": ""
  },
  "/api/visit": {},
  "/api/music/status": {
    "connected": false,
    "platform": "kugou"
  },
  "/api/music/library": {
    "favorites": [],
    "queue": [],
    "mode": "sequence"
  },
  "/api/music/listening": [],
  "/api/music/comments": [],
  "/api/music/commands": [],
  "/api/music/search": [],
  "/api/ombre-recall/recent": [],
  "/api/music/lyrics": {
    "lyrics": ""
  },
  "/api/github-backup/status": {
    "configured": false,
    "enabled": false
  },
  "/api/ombre-recall": {
    "configured": false,
    "enabled": false,
    "entries": [],
    "servers": []
  }
};
