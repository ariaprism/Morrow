import Meting from "./node_modules/@eldment/meting-agent/src/meting/meting.js";

function parse(raw) {
  if (typeof raw !== "string") return raw;
  try { return JSON.parse(raw); } catch { return { raw }; }
}

function client(cookie = "") {
  const meting = new Meting("kugou").format(true);
  if (cookie) meting.cookie(cookie);
  return meting;
}

let input = "";
for await (const chunk of process.stdin) input += chunk;

try {
  const request = JSON.parse(input || "{}");
  const meting = client(String(request.cookie || ""));
  if (request.action === "search") {
    const result = parse(await meting.search(String(request.query || ""), {
      limit: Math.min(20, Math.max(1, Number(request.limit) || 12)),
      page: 1,
    }));
    process.stdout.write(JSON.stringify({ ok: true, result }));
  } else if (request.action === "prepare") {
    const id = String(request.id || "");
    const [url, pic, lyric] = await Promise.all([
      client(String(request.cookie || "")).url(id, 320).then(parse),
      client(String(request.cookie || "")).pic(id, 400).then(parse),
      client(String(request.cookie || "")).lyric(id).then(parse).catch(() => ({ lyric: "", tlyric: "" })),
    ]);
    process.stdout.write(JSON.stringify({ ok: true, url, pic, lyric }));
  } else if (request.action === "lyrics") {
    const lyric = parse(await meting.lyric(String(request.id || "")));
    process.stdout.write(JSON.stringify({ ok: true, lyric }));
  } else {
    throw new Error("unknown action");
  }
} catch (error) {
  process.stdout.write(JSON.stringify({ ok: false, error: error?.message || String(error) }));
  process.exitCode = 1;
}
