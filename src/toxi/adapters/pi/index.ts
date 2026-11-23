/**
 * toxi for the Pi coding agent (design §13.10).
 *
 * Delivers the toxi peer messages routed to this Pi session into it: between
 * tool calls while Pi works, and as a new turn while Pi is idle when the mail
 * should wake it ([wake] kinds). Sending uses the toxi MCP server.
 *
 * The extension only watches and forwards. Routing, the trust framing and the
 * wake decision come from `toxi peer-inbox --peek`, as for every agent.
 * Messages go in as custom messages, never as user messages: a peer's words
 * must not read as the user's own.
 *
 * Installed by `toxi setup pi` into ~/.pi/agent/extensions/toxi/.
 * TOXI_BIN: the toxi command (default `toxi`). TOXI_HOME: a non-default identity.
 */
import { execFile } from "node:child_process";
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

const TOXI = process.env.TOXI_BIN || "toxi";
const HOME = process.env.TOXI_HOME || path.join(os.homedir(), ".config", "toxi");
const FLAG = path.join(HOME, "inbox.flag");
const POLL_MS = 1000;

type Peek = { text: string; msg_uuids: string[]; wake?: boolean };

function toxi(args: string[]): Promise<string> {
	return new Promise((resolve) => {
		execFile(TOXI, args, { timeout: 8000 }, (err, stdout) => resolve(err ? "" : stdout));
	});
}

export default function (pi: ExtensionAPI) {
	let sid = "";
	let cwd = "";
	let name: string | undefined;
	let busy = false; // a delivery is running: one at a time
	let watching = false;
	let idleNow: () => boolean = () => false; // from the latest context: Pi can switch sessions

	async function deliver(idle: boolean): Promise<void> {
		if (busy || !sid) return;
		busy = true;
		try {
			const args = ["peer-inbox", "--peek", "--cwd", cwd, "--session-id", sid];
			if (name) args.push("--name", name);
			const out = await toxi(args);
			if (!out) return;
			let peek: Peek;
			try {
				peek = JSON.parse(out);
			} catch {
				return;
			}
			const wake = peek.wake !== false;
			if (peek.text) {
				// Idle and the mail does not wake the session: leave it unread for the next run.
				if (idle && !wake) return;
				pi.sendMessage(
					{ customType: "toxi", content: peek.text, display: true },
					{ deliverAs: "steer", triggerTurn: idle && wake },
				);
			}
			if (peek.msg_uuids.length) await toxi(["mark-read", "--session-id", sid, "--", ...peek.msg_uuids]);
		} finally {
			busy = false;
		}
	}

	pi.on("session_start", async (_event, ctx) => {
		sid = ctx.sessionManager.getSessionId();
		cwd = ctx.cwd;
		name = pi.getSessionName() || undefined;
		idleNow = () => ctx.isIdle();
		if (!watching) {
			watching = true;
			// A stat once a second: the daemon touches inbox.flag when mail arrives.
			fs.watchFile(FLAG, { interval: POLL_MS }, (cur, prev) => {
				if (cur.mtimeMs !== prev.mtimeMs) void deliver(idleNow());
			});
		}
		await deliver(ctx.isIdle()); // what came while no session was open
	});

	// A run starts: hand over what waited because it should not wake the session.
	pi.on("agent_start", async (_event, ctx) => {
		await deliver(false);
	});

	pi.on("session_shutdown", async () => {
		if (watching) {
			fs.unwatchFile(FLAG);
			watching = false;
		}
		if (sid) await toxi(["session-end", "--session-id", sid]);
		sid = "";
	});
}
