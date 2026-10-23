// toxi cloud relay: a one-way mailbox from Claude Code cloud sessions to local
// sessions. Cloud sessions POST /send (SEND_TOKEN); the poller on the local
// machine takes messages with GET /pull and removes them with POST /ack
// (PULL_TOKEN). Messages wait in a Durable Object while the machine is offline.
import { DurableObject } from "cloudflare:workers";

const MAX_QUEUE = 500;
const MAX_BODY = 8000;
const NAME = /^[\w.-]{1,64}$/;

export class Inbox extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.storage.sql.exec(
      "CREATE TABLE IF NOT EXISTS msgs (id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER, msg TEXT)");
  }
  push(msg) {
    const n = this.ctx.storage.sql.exec("SELECT COUNT(*) AS n FROM msgs").one().n;
    if (n >= MAX_QUEUE) return false;
    this.ctx.storage.sql.exec("INSERT INTO msgs (ts, msg) VALUES (?, ?)", Date.now(), JSON.stringify(msg));
    return true;
  }
  pull() {
    return this.ctx.storage.sql.exec("SELECT id, ts, msg FROM msgs ORDER BY id LIMIT 20").toArray()
      .map((r) => ({ id: r.id, ts: r.ts, ...JSON.parse(r.msg) }));
  }
  ack(ids) {
    for (const id of ids) this.ctx.storage.sql.exec("DELETE FROM msgs WHERE id = ?", id);
  }
}

function authorized(req, token) {
  const got = new TextEncoder().encode(req.headers.get("Authorization") || "");
  const want = new TextEncoder().encode(`Bearer ${token}`);
  return Boolean(token) && got.byteLength === want.byteLength && crypto.subtle.timingSafeEqual(got, want);
}

const json = (data, status = 200) => Response.json(data, { status });

export default {
  async fetch(req, env) {
    const { pathname } = new URL(req.url);
    const inbox = env.INBOX.get(env.INBOX.idFromName("inbox"));
    if (req.method === "POST" && pathname === "/send") {
      if (!authorized(req, env.SEND_TOKEN)) return json({ error: "unauthorized" }, 401);
      let m;
      try { m = await req.json(); } catch { return json({ error: "body must be JSON" }, 400); }
      const body = typeof m.body === "string" ? m.body.trim() : "";
      if (!body || body.length > MAX_BODY) return json({ error: `body: 1-${MAX_BODY} chars` }, 400);
      for (const k of ["to", "from"]) {
        if (m[k] !== undefined && !NAME.test(String(m[k]))) return json({ error: `${k}: [A-Za-z0-9_.-]{1,64}` }, 400);
      }
      const ok = await inbox.push({ to: m.to, from: m.from, body });
      return ok ? json({ queued: true }) : json({ error: "queue full" }, 429);
    }
    if (pathname === "/pull" || pathname === "/ack") {
      if (!authorized(req, env.PULL_TOKEN)) return json({ error: "unauthorized" }, 401);
      if (req.method === "GET" && pathname === "/pull") return json({ messages: await inbox.pull() });
      if (req.method === "POST" && pathname === "/ack") {
        const { ids } = await req.json().catch(() => ({}));
        if (!Array.isArray(ids)) return json({ error: "ids: array" }, 400);
        await inbox.ack(ids.filter(Number.isInteger).slice(0, 100));
        return json({ acked: true });
      }
    }
    return json({ error: "not found" }, 404);
  },
};
