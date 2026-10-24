import Meting from "./node_modules/@eldment/meting-agent/src/meting/meting.js";
import { pathToFileURL } from "node:url";

function parse(raw) {
  if (typeof raw !== "string") return raw;
  try { return JSON.parse(raw); } catch { return {}; }
}

export function client(platform = "kugou", cookie = "", MetingClass = Meting) {
  if (!["kugou", "netease"].includes(platform)) throw new Error("Unsupported music platform");
  if (/[\r\n]/.test(cookie)) throw new Error("Invalid music credential");
  const meting = new MetingClass(platform).format(true);
  if (platform === "netease") {
    const request = meting._curl.bind(meting);
    meting._curl = (url, ...args) => {
      const target = new URL(url);
      if (!["http:", "https:"].includes(target.protocol)) throw new Error("Invalid music endpoint");
      target.protocol = "https:";
      return request(target.href, ...args);
    };
  }
  if (cookie) {
    if (platform === "netease") {
      const entries = new Map();
      for (const part of `${meting.header.Cookie || ""};${cookie}`.split(";")) {
        const equals = part.indexOf("=");
        if (equals > 0) entries.set(part.slice(0, equals).trim(), part.slice(equals + 1).trim());
      }
      meting.cookie([...entries].map(([key, value]) => `${key}=${value}`).join("; "));
    } else meting.cookie(cookie);
  }
  return meting;
}

export async function runRequest(request, MetingClass = Meting) {
  const platform = request.platform || "kugou";
  const cookie = String(request.cookie || "");
  const create = () => client(platform, cookie, MetingClass);
  const meting = create();
  if (request.action === "search") {
    const result = parse(await meting.search(String(request.query || ""), {
      limit: Math.min(20, Math.max(1, Number(request.limit) || 12)), page: 1,
    }));
    return { ok: true, result };
  }
  const id = String(request.id || "");
  if (request.action === "prepare") {
    // NetEase cover IDs are image IDs, not song IDs. Missing artwork must not stop playback.
    const picture = platform === "netease"
      ? create().song(id).then(parse).then(rows => {
          const picId = Array.isArray(rows) ? rows[0]?.pic_id : null;
          return picId ? create().pic(String(picId), 400).then(parse) : {};
        }).catch(() => ({}))
      : create().pic(id, 400).then(parse).catch(() => ({}));
    const [url, pic, lyric] = await Promise.all([
      create().url(id, 320).then(parse), picture,
      create().lyric(id).then(parse).catch(() => ({ lyric: "", tlyric: "" })),
    ]);
    return { ok: true, url, pic, lyric };
  }
  if (request.action === "lyrics") {
    return { ok: true, lyric: parse(await meting.lyric(id)) };
  }
  throw new Error("Unsupported music action");
}

async function main() {
  let input = "";
  for await (const chunk of process.stdin) input += chunk;
  try {
    process.stdout.write(JSON.stringify(await runRequest(JSON.parse(input || "{}"))));
  } catch {
    // Provider errors can contain request headers; only return a safe public message.
    process.stdout.write(JSON.stringify({ ok: false, error: "音乐服务暂时不可用，请稍后重试或重新连接账号" }));
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) await main();
