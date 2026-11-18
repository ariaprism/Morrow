/* The demo runs on its own origin. No real backend or model is called. */
(() => {
  "use strict";
  const STORAGE_KEY = "morrow-demo-state-v1";
  const PREFS_KEY = "morrow-demo-owned-prefs-v1";
  const clone = value => JSON.parse(JSON.stringify(value));
  const fixtures = clone(window.MORROW_DEMO_FIXTURES);
  const nativeFetch = window.fetch.bind(window);
  const stamp = () => new Date().toLocaleString("sv-SE");
  const json = (value, status = 200) => new Response(JSON.stringify(value), {
    status, headers: {"Content-Type": "application/json; charset=utf-8"}
  });
  const fail = (message = "演示模式下，此功能需要自行部署完整版。", status = 409) => json({error: message}, status);
  const collections = {
    "/api/chat/sessions": "sessions", "/api/memory/entries": "memories",
    "/api/todos": "todos", "/api/diary": "diary", "/api/favorites": "favorites"
  };
  function fresh() {
    const data = {schema: 1, messages: {}, values: {}, next: 1000, seen: []};
    for (const [path, key] of Object.entries(collections)) data[key] = clone(fixtures[path] || []);
    data.notes = clone(fixtures["/api/notes/all"]);
    for (const session of data.sessions) data.messages[session.id] = clone(fixtures["/api/chat/messages/" + session.id] || []);
    return data;
  }
  let state = fresh();
  let ephemeral = false;
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "null");
    if (saved?.schema === 1 && Array.isArray(saved.sessions) && saved.messages && saved.values) {
      for (const key of ["sessions", "memories", "todos", "diary", "favorites", "notes", "seen"]) {
        if (!Array.isArray(saved[key])) throw new Error("Invalid demo data");
      }
      if (!Number.isSafeInteger(saved.next) || saved.next < 1000) throw new Error("Invalid demo IDs");
      state = saved;
    }
  } catch { /* A corrupt or unavailable browser store starts a fresh demo. */ }
  function notify(message) {
    if (typeof window.toast === "function") window.toast(message);
    else console.info(message);
  }
  function save() {
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }
    catch {
      if (!ephemeral) notify("浏览器存储不可用或已满；本次修改仅保留到页面关闭。");
      ephemeral = true;
    }
  }
  const next = () => state.next++;
  const clean = (value, limit = 10000) => String(value ?? "").slice(0, limit);
  const found = (rows, id) => rows.find(item => Number(item.id) === Number(id));
  const write = value => { save(); return json(value); };
  const mutableValues = new Set([
    "/api/identity", "/api/together-date", "/api/home-postcard", "/api/home-greetings", "/api/memory/persona"
  ]);
  const integrationFields = /(?:api.?key|token|cookie|secret|password|authorization)/i;
  function containsSecret(value) {
    if (!value || typeof value !== "object") return false;
    return Object.entries(value).some(([key, item]) => integrationFields.test(key) || (typeof item === "object" && containsSecret(item)));
  }
  function demoReply(message) {
    if (/薄荷|浇水|植物|叶子|养花/.test(message)) return "给窗边的小植物留一点时间，也给自己留一点。可以拍张照片，把今天的新叶子记下来。\n\n*这是一条预设演示回复，未调用模型。*";
    if (/吃|饭|番茄|面|鸡蛋/.test(message)) return "那就先好好吃一顿饭吧。把喜欢的碗拿出来，给今天留一小段热乎乎的时间。\n\n*这是一条预设演示回复，未调用模型。*";
    if (/花|周末|散步|阳光/.test(message)) return "把这个小小的计划记下来吧。散步、买花、坐在阳光里，普通的一天也可以有值得期待的事情。\n\n*这是一条预设演示回复，未调用模型。*";
    return "欢迎来 Morrow 的演示小家坐坐。你可以翻翻聊天、写一页日记，或在记忆里留下一件小事。这里的修改会留在你自己的浏览器里。\n\n*这是一条预设演示回复，未调用模型。*";
  }
  function streamReply(body, regenerate = false) {
    let session = found(state.sessions, body.session_id);
    if (!session) {
      session = {id: next(), title: clean(body.message || "新的聊天", 24), archived: 0, pinned: 0, created_at: stamp(), updated_at: stamp()};
      state.sessions.unshift(session);
      state.messages[session.id] = [];
    }
    const messages = state.messages[session.id] || (state.messages[session.id] = []);
    let user = [...messages].reverse().find(item => item.role === "user");
    if (!regenerate) {
      user = {id: next(), role: "user", content: clean(body.message), attachments: [], artifacts: [], created_at: stamp(), active: 0, versions: null};
      messages.push(user);
    }
    let assistant = regenerate && body.message_id ? found(messages, body.message_id) : null;
    const content = demoReply(user?.content || "");
    if (assistant) {
      const versions = assistant.versions || [{c: assistant.content, r: "", a: [], model: "demo", at: assistant.created_at}];
      versions.push({c: content, r: "", a: [], model: "demo", at: stamp()});
      Object.assign(assistant, {versions, active: versions.length - 1, content});
    } else {
      assistant = {id: next(), role: "assistant", content, reasoning: "", attachments: [], artifacts: [], created_at: stamp(), active: 0, versions: null};
      messages.push(assistant);
    }
    session.updated_at = stamp();
    save();
    const events = [{type: "session", session_id: session.id, user_message_id: user?.id, title_pending: false},
      {type: "delta", content}, {type: "done", message_id: assistant.id, model: "demo", at: assistant.created_at, attachments: [], artifacts: []}];
    return new Response(events.map(event => "data: " + JSON.stringify(event) + "\n\n").join(""), {headers: {"Content-Type": "text/event-stream"}});
  }
  function calendar(rows, month) {
    const counts = {};
    rows.forEach(item => { const date = item.created_at.slice(0, 10); if (date.startsWith(month || "")) counts[date] = (counts[date] || 0) + 1; });
    return Object.entries(counts).map(([date, count]) => ({date, count}));
  }
  function route(url, method, body) {
    const p = url.pathname, q = url.searchParams, read = method === "GET" || method === "HEAD";
    if (!read && containsSecret(body)) return fail("演示站不接收或保存 key、Cookie、Token 等凭据。");
    if (p === "/api/chat/send" && method === "POST") return streamReply(body);
    if (p === "/api/chat/regenerate" && method === "POST") return streamReply(body, true);
    if (["/api/activity", "/api/push/read", "/api/notes/seen", "/api/logout"].includes(p) && method === "POST") {
      if (p === "/api/notes/seen") state.seen.push(Number(body.note_id));
      return write({ok: true});
    }
    if (mutableValues.has(p)) {
      if (read) return json(state.values[p] ?? fixtures[p]);
      if (method === "PUT") {
        if (p === "/api/identity") {
          const current = state.values[p] || fixtures[p];
          state.values[p] = {...current, user_name: clean(body.user_name, 40) || "小满", assistant_name: clean(body.assistant_name, 40) || "Morrow", assistant_pronoun: ["他", "她", "ta"].includes(body.assistant_pronoun) ? body.assistant_pronoun : "ta", user_name_set: true, assistant_name_set: true};
        } else if (p === "/api/together-date") {
          if (!/^\d{4}-\d{2}-\d{2}$/.test(body.date || "")) return fail("请填写有效日期。", 400);
          state.values[p] = {date: body.date};
        } else if (p === "/api/memory/persona") state.values[p] = {content: clean(body.content)};
        else if (p === "/api/home-greetings") state.values[p] = {mode: body.mode === "random" ? "random" : "timed", ...Object.fromEntries(["morning", "noon", "evening", "common"].map(key => [key, clean(body[key], 2000)]))};
        else state.values[p] = {enabled: !!body.enabled, design: ["light", "leaf", "tomorrow"].includes(body.design) ? body.design : "tomorrow", quotes: Object.fromEntries(["light", "leaf", "tomorrow"].map(key => [key, clean(body.quotes?.[key], 200)]))};
        return write(state.values[p]);
      }
    }
    let match = p.match(/^\/api\/chat\/messages\/(\d+)$/);
    if (match && read) return json(state.messages[match[1]] || []);
    match = p.match(/^\/api\/chat\/(window|compression)\/(\d+)$/);
    if (match && read) {
      const sample = clone(fixtures["/api/chat/" + match[1] + "/3"] || {});
      return json({...sample, session_id: Number(match[2]), continued_from: null, has_summary: false});
    }
    match = p.match(/^\/api\/chat\/sessions\/(\d+)\/generate-title$/);
    if (match && method === "POST") return json({title: found(state.sessions, match[1])?.title || "新的聊天"});
    match = p.match(/^\/api\/chat\/message\/(\d+)(\/version)?$/);
    if (match) {
      const rows = Object.values(state.messages).find(items => found(items, match[1]));
      const message = rows && found(rows, match[1]);
      if (!message) return fail("消息不存在。", 404);
      if (method === "DELETE" && !match[2]) { rows.splice(rows.indexOf(message), 1); return write({ok: true}); }
      if (method === "PUT" && !match[2]) { message.content = clean(body.content); return write(message); }
      if (match[2] && method === "PUT" && message.versions?.[body.index]) {
        message.active = body.index; message.content = message.versions[body.index].c;
        return write({ok: true});
      }
    }
    if (p === "/api/notes" && method === "PUT") {
      const note = {id: next(), content: clean(body.content), author: "user", created_at: stamp()};
      state.notes.unshift(note); return write(note);
    }
    if (["/api/notes", "/api/notes/latest", "/api/notes/all", "/api/notes/calendar", "/api/diary/calendar", "/api/diary/latest"].includes(p) && read) {
      const notes = p.startsWith("/api/notes"); const rows = notes ? state.notes : state.diary;
      if (p.endsWith("/calendar")) return json(calendar(rows, q.get("month")));
      if (p.endsWith("/all")) return json(rows.filter(item => !q.get("date") || item.created_at.startsWith(q.get("date"))));
      return json({...rows[0] || {id: null, content: ""}, total: rows.length, fixed: false, is_new: rows[0] ? !state.seen.includes(rows[0].id) : false});
    }
    match = p.match(/^\/api\/notes\/(\d+)$/);
    if (match && method === "DELETE") { state.notes = state.notes.filter(item => item.id !== Number(match[1])); return write({ok: true}); }
    match = p.match(/^\/api\/diary\/(\d+)\/favorite$/);
    if (match && method === "PUT") {
      const entry = found(state.diary, match[1]); if (!entry) return fail("日记不存在。", 404);
      entry.favorite = !!body.favorite; return write(entry);
    }
    match = p.match(/^\/api\/diary\/(\d+)\/comments$/);
    if (match && method === "POST") {
      const entry = found(state.diary, match[1]); if (!entry) return fail("日记不存在。", 404);
      const comment = {id: next(), entry_id: entry.id, author: "user", content: clean(body.content), created_at: stamp()};
      entry.comments.push(comment); return write(comment);
    }
    match = p.match(/^\/api\/diary\/comments\/(\d+)$/);
    if (match && method === "DELETE") { for (const entry of state.diary) entry.comments = entry.comments.filter(item => item.id !== Number(match[1])); return write({ok: true}); }
    for (const [path, key] of Object.entries(collections)) {
      const suffix = p.slice(path.length);
      if (p !== path && !(p.startsWith(path + "/") && /^\/\d+$/.test(suffix))) continue;
      const id = suffix ? Number(suffix.slice(1)) : null;
      const rows = state[key];
      if (read) {
        if (id !== null) return found(rows, id) ? json(found(rows, id)) : fail("记录不存在。", 404);
        let result = rows;
        if (key === "sessions") result = rows.filter(item => !!item.archived === (q.get("archived") === "1")).sort((a, b) => b.pinned - a.pinned || b.updated_at.localeCompare(a.updated_at));
        if (key === "memories" && q.get("kind")) result = result.filter(item => item.kind === q.get("kind"));
        if (key === "diary") result = result.filter(item => (!q.get("date") || item.created_at.startsWith(q.get("date"))) && (!q.get("q") || item.content.includes(q.get("q"))) && (!q.get("tag") || (q.get("tag") === "favorite" ? item.favorite : item.tag === q.get("tag"))));
        return json(result);
      }
      if (method === "DELETE" && id !== null) {
        state[key] = rows.filter(item => item.id !== id);
        if (key === "sessions") {
          delete state.messages[id]; state.favorites = state.favorites.filter(item => item.session_id !== id);
        }
        return write({ok: true});
      }
      if (method !== "POST" && method !== "PUT") return fail();
      let item = id !== null ? found(rows, id) : null;
      if (id !== null && !item) return fail("记录不存在。", 404);
      if (!item) { item = {id: next(), created_at: stamp(), updated_at: stamp()}; rows.unshift(item); }
      if (key === "sessions") {
        Object.assign(item, {title: clean(body.title ?? item.title ?? "新的聊天", 100), archived: Number(!!(body.archived ?? item.archived)), pinned: Number(!!(body.pinned ?? item.pinned)), updated_at: stamp()});
        state.messages[item.id] ||= [];
      } else if (key === "memories") Object.assign(item, {content: clean(body.content ?? item.content), pinned: Number(!!(body.pinned ?? item.pinned)), kind: (body.kind ?? item.kind) === "dynamic" ? "dynamic" : "fixed", tags: Array.isArray(body.tags) ? body.tags.map(tag => clean(tag, 40)).slice(0, 20) : item.tags || [], updated_at: stamp()});
      else if (key === "todos") Object.assign(item, {text: clean(body.text ?? item.text, 1000), done: Number(!!(body.done ?? item.done)), done_at: body.done ? stamp() : null});
      else if (key === "diary") Object.assign(item, {author: item.author || "user", content: clean(body.content ?? item.content), tag: ["light", "dive", "anchor"].includes(body.tag) ? body.tag : item.tag || "light", favorite: item.favorite || false, comments: item.comments || [], updated_at: stamp()});
      else if (key === "favorites") Object.assign(item, {title: clean(body.title, 60), preview: clean(body.content, 300), session_id: Number(body.session_id), message_id: Number(body.message_id) || null});
      return write(item);
    }
    if (read && Object.hasOwn(fixtures, p)) return json(fixtures[p]);
    return fail();
  }
  window.fetch = async (input, options = {}) => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request ? request.url : String(input), location.href);
    const method = (options.method || request?.method || "GET").toUpperCase();
    if (options.signal?.aborted || request?.signal.aborted) throw new DOMException("Aborted", "AbortError");
    if (url.origin !== location.origin) return fail("演示版不连接外部服务。");
    if (!url.pathname.startsWith("/api/")) {
      if (method !== "GET" && method !== "HEAD") return fail();
      return nativeFetch(input, options);
    }
    let body = {};
    try {
      const raw = options.body ?? (request && method !== "GET" && method !== "HEAD" ? await request.clone().text() : "{}");
      if (typeof raw !== "string") return fail("演示站不上传文件。");
      body = JSON.parse(raw || "{}");
      if (!body || typeof body !== "object" || Array.isArray(body)) return fail("无效输入。", 400);
    } catch { return fail("无效输入。", 400); }
    const response = route(url, method, body);
    if (!response.ok && method !== "GET") notify((await response.clone().json()).error);
    return response;
  };
  // Programmatic network paths used by integrations are unavailable in the demo.
  if (navigator.sendBeacon) navigator.sendBeacon = () => false;
  const initialPrefs = new Set(Object.keys(localStorage));
  function mount() {
    document.getElementById("demoReset").onclick = () => {
      if (!window.confirm("恢复初始演示内容？本浏览器里修改的聊天、日记、记忆和外观设置将被清除。")) return;
      let owned = [];
      try { owned = JSON.parse(localStorage.getItem(PREFS_KEY) || "[]"); } catch { /* no saved preference list */ }
      for (const key of [...Object.keys(localStorage)]) if (!initialPrefs.has(key) || owned.includes(key) || [STORAGE_KEY, PREFS_KEY].includes(key)) localStorage.removeItem(key);
      location.reload();
    };
    const blocked = new Set(["provKey", "provUrl", "provSave", "provDel", "provFetch", "ttsKey", "ttsUrl", "ttsSave", "ttsCheck", "ttsFetchModels", "ttsGenerate", "mcpEditUrl", "mcpEditHeaders", "mcpSave", "mcpTestConnection", "mcpDiscover", "musicCookie", "musicCookieSave", "pushTest", "tgPush", "tgPushPreview", "backupImport", "rikkahubImport"]);
    for (const control of document.querySelectorAll("input,textarea,button")) {
      if (blocked.has(control.id) || (control.matches("input,textarea") && /key|token|cookie|password|secret|header/i.test(control.id))) {
        control.disabled = true; control.dataset.demoDisabled = "true"; control.title = "演示模式不接收凭据或连接外部服务，请自行部署完整版。";
        if (control.matches("input,textarea")) { control.value = ""; control.placeholder = "演示模式 · 不接收真实凭据"; }
      }
    }
    const caption = document.createElement("p"); caption.className = "caption";
    caption.textContent = "演示配置仅供查看。本站不接收真实 key，也不调用模型。";
    document.getElementById("provKey")?.closest(".field")?.after(caption);
    const logout = document.getElementById("logoutBtn"); if (logout) { logout.textContent = "重置演示"; logout.onclick = () => document.getElementById("demoReset").click(); }
    window.addEventListener("pagehide", () => {
      try {
        const owned = JSON.parse(localStorage.getItem(PREFS_KEY) || "[]");
        localStorage.setItem(PREFS_KEY, JSON.stringify([...new Set([...owned, ...Object.keys(localStorage).filter(key => !initialPrefs.has(key))])]));
      } catch { /* private browsing may disable storage */ }
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount); else mount();
})();
