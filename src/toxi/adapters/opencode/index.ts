/**
 * toxi for OpenCode V2 (a server plugin, design §13.10).
 *
 * Delivers the toxi peer messages routed to an OpenCode session into it: at
 * the next step boundary while the session runs, and as a new run while it is
 * idle when the mail should wake it ([wake] kinds). Sending uses the toxi MCP
 * server, which learns the calling session from OpenCode's `_meta`.
 *
 * One OpenCode server holds every session of the user and keeps them for good.
 * So the plugin decides which sessions toxi counts as live: a top-level session
 * of this plugin instance's directory that had activity in the last 12 hours.
 * It renews their toxi entries while it runs, and ends them after 12 quiet
 * hours, when the session is deleted or archived, or when the plugin unloads.
 * Child sessions (subagents) never take peer messages.
 *
 * Routing, the trust framing and the wake decision come from
 * `toxi peer-inbox --peek`, as for every agent. Installed by
 * `toxi setup opencode` into ~/.config/opencode/plugins/toxi/.
 * TOXI_BIN: the toxi command (default `toxi`). TOXI_HOME: a non-default identity.
 */
import { execFile } from "node:child_process"
import * as fs from "node:fs"
import * as os from "node:os"
import * as path from "node:path"

const TOXI = process.env.TOXI_BIN || "toxi"
const HOME = process.env.TOXI_HOME || path.join(os.homedir(), ".config", "toxi")
const FLAG = path.join(HOME, "inbox.flag")
const LIVE_MS = 12 * 3600 * 1000 // a session with no activity for this long goes offline
const RENEW_MS = 10 * 60 * 1000 // well inside toxi's one-hour SID_TTL
const POLL_MS = 1000

type Peek = { text: string; msg_uuids: string[]; wake?: boolean }
type Tracked = { dir: string; title?: string; busy: boolean; active: number; delivering: boolean }

function toxi(args: string[]): Promise<string> {
  return new Promise((resolve) => {
    // HOME may be written in by `toxi setup opencode`: the server lacks the shell's TOXI_HOME.
    execFile(TOXI, args, { timeout: 8000, env: { ...process.env, TOXI_HOME: HOME } }, (err, stdout) =>
      resolve(err ? "" : stdout),
    )
  })
}

export default {
  id: "toxi",
  async setup(ctx: any) {
    const here: string = ctx.location.directory
    const sessions = new Map<string, Tracked>()
    const controller = new AbortController()

    async function track(sessionID: string): Promise<Tracked | undefined> {
      let s = sessions.get(sessionID)
      if (s) return s
      let info: any
      try {
        info = await ctx.session.get({ sessionID })
      } catch {
        return undefined
      }
      // Only this instance's directory (every instance sees every event), and no subagents.
      if (!info || info.parentID || info.location?.directory !== here) return undefined
      s = { dir: here, title: info.title, busy: false, active: Date.now(), delivering: false }
      sessions.set(sessionID, s)
      return s
    }

    async function end(sessionID: string) {
      sessions.delete(sessionID)
      await toxi(["session-end", "--session-id", sessionID])
    }

    async function deliver(sessionID: string, s: Tracked) {
      if (s.delivering) return
      s.delivering = true
      try {
        const args = ["peer-inbox", "--peek", "--cwd", s.dir, "--session-id", sessionID]
        if (s.title) args.push("--name", s.title)
        const out = await toxi(args) // also registers the session, or renews its entry
        if (!out) return
        let peek: Peek
        try {
          peek = JSON.parse(out)
        } catch {
          return
        }
        if (peek.text) {
          // Idle, and the mail does not wake the session: leave it unread for its next run.
          if (!s.busy && peek.wake === false) return
          // `steer`: at the next step boundary of a run; an idle session starts a run.
          await ctx.session.synthetic({ sessionID, text: peek.text, delivery: "steer" })
        }
        if (peek.msg_uuids.length) await toxi(["mark-read", "--session-id", sessionID, "--", ...peek.msg_uuids])
      } catch {
        // A failed synthetic leaves the mail unread: the next look delivers it.
      } finally {
        s.delivering = false
      }
    }

    // Sessions that were active before this plugin loaded.
    try {
      const page = await ctx.session.list({ directory: here, parentID: null, limit: 20, order: "desc" })
      for (const info of page.data ?? []) {
        if (!info.parentID && Date.now() - (info.time?.updated ?? 0) < LIVE_MS) {
          sessions.set(info.id, { dir: here, title: info.title, busy: false, active: info.time.updated, delivering: false })
        }
      }
    } catch {
      // an older server: sessions join on their next activity
    }

    void (async () => {
      const types = [
        "session.execution.started",
        "session.execution.succeeded",
        "session.execution.failed",
        "session.execution.interrupted",
        "session.renamed",
        "session.deleted",
        "session.archived",
      ]
      for await (const ev of ctx.event.subscribe({ types, signal: controller.signal })) {
        const sessionID: string | undefined = ev.data?.sessionID
        if (!sessionID) continue
        if (ev.type === "session.deleted" || ev.type === "session.archived") {
          if (sessions.has(sessionID)) await end(sessionID)
          continue
        }
        const s = await track(sessionID)
        if (!s) continue
        s.active = Date.now()
        if (ev.type === "session.renamed") {
          s.title = ev.data.title
          continue
        }
        s.busy = ev.type === "session.execution.started"
        if (s.busy) void deliver(sessionID, s) // a run starts: hand over what waited
      }
    })().catch(() => {})

    // A stat once a second: the daemon touches inbox.flag when mail arrives.
    fs.watchFile(FLAG, { interval: POLL_MS }, (cur, prev) => {
      if (cur.mtimeMs === prev.mtimeMs) return
      for (const [sessionID, s] of sessions) void deliver(sessionID, s)
    })

    const renew = setInterval(() => {
      for (const [sessionID, s] of sessions) {
        if (Date.now() - s.active > LIVE_MS) void end(sessionID)
        else void deliver(sessionID, s)
      }
    }, RENEW_MS)
    for (const [sessionID, s] of sessions) void deliver(sessionID, s)

    return async () => {
      controller.abort()
      fs.unwatchFile(FLAG)
      clearInterval(renew)
      await Promise.all([...sessions.keys()].map(end))
    }
  },
}
