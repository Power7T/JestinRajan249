/**
 * Airbnb Host WhatsApp Companion Bot — Option 4
 * ===============================================
 * Runs as a companion device on the host's personal WhatsApp.
 *
 * Guest messages → classify → auto-reply (routine) or draft to host (complex)
 * Host approval  → APPROVE <id> / EDIT <id>: <text> / SKIP <id>
 *
 * Also exposes a local HTTP server so email_watcher.py can push complex
 * email drafts to the host via WhatsApp.
 *
 * First run: scan the QR code printed in the terminal to link your phone.
 * Session is saved in .wwebjs_auth/ so you only need to scan once.
 *
 * Run: node bot.js  (or via start.sh)
 */

"use strict";

const path   = require("path");
const http   = require("http");
const https  = require("https");
const { URL }  = require("url");

require("dotenv").config({ path: path.join(__dirname, "../.env") });

const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const HOST_NUMBER = (process.env.HOST_WHATSAPP_NUMBER || "").trim();
if (!HOST_NUMBER) {
  console.error("❌  HOST_WHATSAPP_NUMBER is not set in .env");
  process.exit(1);
}

const ROUTER_URL  = `http://127.0.0.1:${process.env.ROUTER_PORT || 7771}`;
const WA_BOT_PORT = parseInt(process.env.WA_BOT_PORT || "7772", 10);

// Normalise to WhatsApp chat ID: strip "+" prefix, append "@c.us"
const HOST_WA_ID  = HOST_NUMBER.replace(/^\+/, "") + "@c.us";

// ---------------------------------------------------------------------------
// Pending approvals: draft_id → { guestChatId | null, draft, channel }
// (in-memory; restarts clear it — low-volume use case, acceptable)
// ---------------------------------------------------------------------------
const pending = new Map();

// ---------------------------------------------------------------------------
// WhatsApp client
// ---------------------------------------------------------------------------
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: path.join(__dirname, ".wwebjs_auth") }),
  puppeteer: {
    args: ["--no-sandbox", "--disable-setuid-sandbox"],
    headless: true,
  },
});

client.on("qr", (qr) => {
  console.log("\n📱  Scan this QR code with WhatsApp on your phone:");
  console.log("    Open WhatsApp → Linked Devices → Link a Device\n");
  qrcode.generate(qr, { small: true });
});

client.on("loading_screen", (pct, msg) => {
  process.stdout.write(`\r⏳  Loading WhatsApp: ${pct}% — ${msg}     `);
});

client.on("authenticated", () => {
  console.log("\n🔐  WhatsApp authenticated.");
});

client.on("ready", () => {
  console.log("✅  WhatsApp companion connected and ready.");
  console.log(`    Host number : ${HOST_NUMBER}`);
  console.log(`    Router URL  : ${ROUTER_URL}`);
  console.log(`    HTTP port   : ${WA_BOT_PORT}\n`);
});

client.on("disconnected", (reason) => {
  console.warn("⚠️  WhatsApp disconnected:", reason);
});

// ---------------------------------------------------------------------------
// Incoming message handler
// ---------------------------------------------------------------------------
client.on("message", async (msg) => {
  const from = msg.from;

  // ── Host approval commands ──────────────────────────────────────────────
  if (from === HOST_WA_ID && msg.body) {
    await handleHostApproval(msg.body.trim());
    return;
  }

  // Skip group chats and status updates
  if (msg.isGroupMsg || from === "status@broadcast") return;

  // ── Guest message ───────────────────────────────────────────────────────
  if (!msg.body) return;

  const contact   = await msg.getContact();
  const guestName = contact.pushname || contact.name || "Guest";
  const text      = msg.body;

  console.log(`📨  [${guestName}] ${text.slice(0, 80)}${text.length > 80 ? "…" : ""}`);

  try {
    const result  = await callRouter("/classify", {
      source:     "whatsapp",
      guest_name: guestName,
      message:    text,
      reply_to:   from,
    });

    const { draft_id, msg_type, draft } = result;

    if (msg_type === "routine") {
      await client.sendMessage(from, draft);
      console.log(`  ✅  Auto-replied to ${guestName} (routine)`);
      // Record approval in router (fire-and-forget)
      callRouter("/approve", { draft_id, action: "approve" }).catch(() => {});

    } else {
      pending.set(draft_id, { guestChatId: from, draft, channel: "whatsapp" });
      const notice = buildApprovalNotice(draft_id, guestName, draft, "WhatsApp");
      await client.sendMessage(HOST_WA_ID, notice);
      console.log(`  ⏳  Complex message from ${guestName} — draft sent to host`);
    }
  } catch (err) {
    console.error(`  ❌  Error processing message from ${guestName}:`, err.message);
  }
});

// ---------------------------------------------------------------------------
// Host approval parsing
// ---------------------------------------------------------------------------
const RE_APPROVE = /^APPROVE\s+([^\s]+)$/i;
const RE_EDIT    = /^EDIT\s+([^\s]+):\s*([\s\S]+)$/i;
const RE_SKIP    = /^SKIP\s+([^\s]+)$/i;

async function handleHostApproval(text) {
  let m;

  if ((m = RE_APPROVE.exec(text))) {
    const id = m[1];
    const entry = pending.get(id);
    if (!entry) return;
    if (entry.guestChatId) await client.sendMessage(entry.guestChatId, entry.draft);
    callRouter("/approve", { draft_id: id, action: "approve" }).catch(() => {});
    pending.delete(id);
    await client.sendMessage(HOST_WA_ID, "✅  Reply sent to guest.");

  } else if ((m = RE_EDIT.exec(text))) {
    const id      = m[1];
    const newText = m[2].trim();
    const entry   = pending.get(id);
    if (!entry) return;
    if (entry.guestChatId) await client.sendMessage(entry.guestChatId, newText);
    callRouter("/approve", { draft_id: id, action: "edit", edited_text: newText }).catch(() => {});
    pending.delete(id);
    await client.sendMessage(HOST_WA_ID, "✅  Edited reply sent to guest.");

  } else if ((m = RE_SKIP.exec(text))) {
    const id = m[1];
    if (!pending.has(id)) return;
    pending.delete(id);
    callRouter("/approve", { draft_id: id, action: "skip" }).catch(() => {});
    await client.sendMessage(HOST_WA_ID, "⏭️  Skipped — no reply sent.");
  }
}

// ---------------------------------------------------------------------------
// HTTP server — receives complex email drafts from email_watcher.py
// POST /notify-host  { draft_id, guest_name, draft, channel }
// ---------------------------------------------------------------------------
const httpServer = http.createServer((req, res) => {
  if (req.method !== "POST" || req.url !== "/notify-host") {
    res.writeHead(404);
    res.end();
    return;
  }

  let body = "";
  req.on("data", (chunk) => { body += chunk; });
  req.on("end", async () => {
    try {
      const { draft_id, guest_name, draft, channel } = JSON.parse(body);
      pending.set(draft_id, { guestChatId: null, draft, channel });
      const notice = buildApprovalNotice(draft_id, guest_name, draft, "Email");
      await client.sendMessage(HOST_WA_ID, notice);
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "notified" }));
      console.log(`📧  Email draft from ${guest_name} forwarded to host via WhatsApp`);
    } catch (err) {
      console.error("notify-host error:", err.message);
      res.writeHead(500);
      res.end();
    }
  });
});

httpServer.listen(WA_BOT_PORT, "127.0.0.1", () => {
  console.log(`📡  HTTP server listening on 127.0.0.1:${WA_BOT_PORT}`);
});

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function buildApprovalNotice(draft_id, guestName, draft, source) {
  return (
    `📋 *Draft reply — ${source}*\n` +
    `Guest: *${guestName}*\n\n` +
    `${draft}\n\n` +
    `───────────────\n` +
    `Reply with:\n` +
    `• \`APPROVE ${draft_id}\`\n` +
    `• \`EDIT ${draft_id}: <your revised text>\`\n` +
    `• \`SKIP ${draft_id}\``
  );
}

function callRouter(endpoint, body) {
  return new Promise((resolve, reject) => {
    const data    = JSON.stringify(body);
    const parsed  = new URL(ROUTER_URL + endpoint);
    const options = {
      hostname: parsed.hostname,
      port:     Number(parsed.port) || 80,
      path:     parsed.pathname,
      method:   "POST",
      headers:  {
        "Content-Type":   "application/json",
        "Content-Length": Buffer.byteLength(data),
      },
    };
    const lib = parsed.protocol === "https:" ? https : http;
    const req = lib.request(options, (res) => {
      let out = "";
      res.on("data", (c) => { out += c; });
      res.on("end", () => {
        try { resolve(JSON.parse(out)); }
        catch { resolve({}); }
      });
    });
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
client.initialize();
