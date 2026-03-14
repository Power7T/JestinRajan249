/**
 * © 2024 Jestin Rajan. All rights reserved.
 * Licensed under the Airbnb Host AI License Agreement.
 * Unauthorized copying, distribution or use is prohibited.
 *
 * Airbnb Host — WhatsApp Business Cloud API Mode (WA_MODE=business_api)
 * =======================================================================
 * For property managers with 20+ units. Uses Meta's official Cloud API:
 *   - No ban risk (officially supported)
 *   - No QR pairing, no persistent session to maintain
 *   - Webhook-based (Meta pushes messages to this server)
 *   - Free for inbound (guest replies within 24h window)
 *   - ~$0.004–0.02/message for outbound notifications
 *   - Scales to 10,000+ conversations per day
 *
 * Setup:
 *   1. Create a Meta Business account at business.facebook.com
 *   2. Add a WhatsApp product to your app at developers.facebook.com
 *   3. Get a permanent token, phone number ID, and set a webhook verify token
 *   4. Set WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_VERIFY_TOKEN in .env
 *   5. Set your webhook URL to:  https://your-server.com/webhook
 *   6. Set WA_MODE=business_api in .env
 *   7. Run: node webhook.js
 *
 * All business logic (guest management, vendor cascade, approval commands)
 * is shared with bot.js via the same handler functions. Only the transport
 * (send/receive) is different.
 */

"use strict";

const path   = require("path");
const fs     = require("fs");
const http   = require("http");
const https  = require("https");
const { URL } = require("url");

require("dotenv").config({ path: path.join(__dirname, "../.env") });

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const HOST_NUMBER    = (process.env.HOST_WHATSAPP_NUMBER || "").trim();
const WA_TOKEN       = process.env.WHATSAPP_TOKEN       || "";
const WA_PHONE_ID    = process.env.WHATSAPP_PHONE_ID    || "";
const VERIFY_TOKEN   = process.env.WHATSAPP_VERIFY_TOKEN || "airbnb_host_webhook";
const ROUTER_URL     = `http://127.0.0.1:${process.env.ROUTER_PORT || 7771}`;
const WA_BOT_PORT    = parseInt(process.env.WA_BOT_PORT || "7772", 10);
const INTERNAL_TOKEN = process.env.INTERNAL_TOKEN || "";
const MAX_BODY_BYTES = 65536;
const AIRBNB_LISTING = (process.env.AIRBNB_LISTING_URL || "").trim();

if (!WA_TOKEN || !WA_PHONE_ID) {
  console.error("❌  WHATSAPP_TOKEN and WHATSAPP_PHONE_ID must be set in .env for business_api mode.");
  process.exit(1);
}
if (!HOST_NUMBER) {
  console.error("❌  HOST_WHATSAPP_NUMBER not set"); process.exit(1);
}

// Normalise to plain E.164 (no @suffix) for Cloud API sends
const HOST_PHONE = HOST_NUMBER.replace(/[^\d+]/g, "");

function toPhone(jidOrNum) {
  // Accept either +1234567890 or 1234567890@s.whatsapp.net
  return jidOrNum.replace(/@.+$/, "").replace(/[^\d]/g, "");
}

// ---------------------------------------------------------------------------
// State files — identical to bot.js (shared disk state)
// ---------------------------------------------------------------------------
const FILES = {
  pending:    path.join(__dirname, "pending.json"),
  guests:     path.join(__dirname, "guests.json"),
  services:   path.join(__dirname, "service_requests.json"),
  pendingReg: path.join(__dirname, "pending_reg.json"),
};

function loadJSON(file, fallback = {}) {
  try {
    if (fs.existsSync(file)) return JSON.parse(fs.readFileSync(file, "utf8"));
  } catch (e) { console.warn(`⚠️  Could not load ${path.basename(file)}: ${e.message}`); }
  return fallback;
}
function saveJSON(file, data) {
  try {
    const tmp = file + ".tmp";
    fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
    fs.renameSync(tmp, file);
  } catch (e) { console.error(`❌  Could not save ${path.basename(file)}: ${e.message}`); }
}

const pending    = new Map(Object.entries(loadJSON(FILES.pending)));
const guests     = new Map(Object.entries(loadJSON(FILES.guests)));
const services   = new Map(Object.entries(loadJSON(FILES.services)));
const pendingReg = new Map(Object.entries(loadJSON(FILES.pendingReg)));
console.log(`📂  Loaded: ${pending.size} pending, ${guests.size} guests`);

// ---------------------------------------------------------------------------
// Vendor registry
// ---------------------------------------------------------------------------
let VENDORS = { cleaners: [], ac_technicians: [], plumbers: [], electricians: [], locksmiths: [] };
try {
  VENDORS = JSON.parse(fs.readFileSync(path.join(__dirname, "../vendors.json"), "utf8"));
  delete VENDORS._comment;
} catch (e) { console.warn("⚠️  vendors.json not found"); }

const _VENDOR_LABELS = {
  cleaners: "Cleaner", ac_technicians: "AC Technician",
  plumbers: "Plumber", electricians: "Electrician", locksmiths: "Locksmith",
};
const label = t => _VENDOR_LABELS[t] || t;

// ---------------------------------------------------------------------------
// WhatsApp Cloud API — send a text message
// ---------------------------------------------------------------------------
async function sendMsg(phone, text) {
  const to = String(phone).replace(/[^\d]/g, "");
  const body = JSON.stringify({
    messaging_product: "whatsapp",
    to,
    type: "text",
    text: { body: String(text) },
  });
  return new Promise((resolve, reject) => {
    const req = https.request({
      hostname: "graph.facebook.com",
      path:     `/v18.0/${WA_PHONE_ID}/messages`,
      method:   "POST",
      headers:  {
        "Authorization": `Bearer ${WA_TOKEN}`,
        "Content-Type":  "application/json",
        "Content-Length": Buffer.byteLength(body),
      },
    }, res => {
      let out = "";
      res.on("data", c => out += c);
      res.on("end", () => {
        if (res.statusCode >= 400) {
          console.error(`Cloud API error ${res.statusCode}:`, out.slice(0, 200));
          reject(new Error(`HTTP ${res.statusCode}`));
        } else {
          resolve(JSON.parse(out));
        }
      });
    });
    req.on("error", reject);
    req.write(body); req.end();
  });
}

// ---------------------------------------------------------------------------
// Message routing — same logic as bot.js, different transport
// ---------------------------------------------------------------------------
async function routeIncoming(fromPhone, bodyText) {
  const from = fromPhone;  // plain phone string in Cloud API mode

  if (from === HOST_PHONE.replace(/[^\d]/g, "")) {
    await handleHostMessage(bodyText.trim());
    return;
  }

  const guestEntry = findGuestByPhone(from);
  if (guestEntry) {
    await handleGuestMessageWithExtension({ body: bodyText, from }, guestEntry);
    return;
  }

  const vendor = findVendorByPhone(from);
  if (vendor) {
    await handleVendorResponse({ body: bodyText.trim().toUpperCase(), from }, vendor);
  }
}

function findGuestByPhone(phone) {
  const clean = phone.replace(/[^\d]/g, "");
  for (const [booking_uid, guest] of guests) {
    if (guest.wa_id && guest.wa_id.replace(/[^\d]/g, "") === clean) {
      return { booking_uid, guest };
    }
  }
  return null;
}

function findVendorByPhone(phone) {
  const clean = phone.replace(/[^\d]/g, "");
  for (const [type, list] of Object.entries(VENDORS)) {
    for (let i = 0; i < (list || []).length; i++) {
      if (list[i].whatsapp.replace(/[^\d]/g, "") === clean) {
        return { ...list[i], type, index: i };
      }
    }
  }
  return null;
}

// ---------------------------------------------------------------------------
// Shared business logic (mirrors bot.js — kept in sync manually)
// For a production implementation these would live in a shared module.
// ---------------------------------------------------------------------------

function buildDraftNotice(draft_id, guestName, draft, source) {
  return (
    `📋 *Draft reply — ${source}*\n` +
    `Guest: *${guestName}*\n\n` +
    `${draft}\n\n` +
    `───────────────\n` +
    `• \`APPROVE ${draft_id}\`\n` +
    `• \`EDIT ${draft_id}: <revised text>\`\n` +
    `• \`SKIP ${draft_id}\``
  );
}

// Host command regexes (same as bot.js)
const RE_APPROVE    = /^APPROVE\s+(\S+)$/i;
const RE_EDIT       = /^EDIT\s+(\S+):\s*([\s\S]{1,2000})$/i;
const RE_SKIP       = /^SKIP\s+(\S+)$/i;
const RE_GUEST_WA   = /^GUEST_WA\s+(\S+)\s+(\+?\d[\d\s\-]{6,})(.*)?$/i;
const RE_EXTEND_YES = /^EXTEND\s+YES\s+(\S+)$/i;
const RE_EXTEND_NO  = /^EXTEND\s+NO\s+(\S+)$/i;
const RE_VENDOR_YES = /^VENDOR_YES\s+(\S+)$/i;
const RE_VENDOR_SKP = /^VENDOR_SKIP\s+(\S+)$/i;
const RE_NEXT_VND   = /^NEXT_VENDOR\s+(\S+)$/i;
const RE_STOP_VND   = /^STOP_VENDOR\s+(\S+)$/i;
const RE_LIST_VND   = /^LIST_VENDORS?(?:\s+(\S+))?$/i;
const RE_LIST_GSTS  = /^LIST_GUESTS?$/i;
const RE_STATUS     = /^STATUS$/i;
const RE_HELP       = /^HELP$/i;

async function handleHostMessage(text) {
  let m;
  if ((m = RE_APPROVE.exec(text)))    return onApprove(m[1]);
  if ((m = RE_EDIT.exec(text)))       return onEdit(m[1], m[2].trim());
  if ((m = RE_SKIP.exec(text)))       return onSkip(m[1]);
  if ((m = RE_GUEST_WA.exec(text))) {
    return registerGuest(m[1], m[2].trim(), m[3]?.trim() || null);
  }
  if ((m = RE_EXTEND_YES.exec(text))) return onExtendApproved(m[1]);
  if ((m = RE_EXTEND_NO.exec(text)))  return onExtendDenied(m[1]);
  if ((m = RE_VENDOR_YES.exec(text))) return dispatchVendor(m[1]);
  if ((m = RE_VENDOR_SKP.exec(text))) return cancelVendor(m[1]);
  if ((m = RE_NEXT_VND.exec(text)))   return tryNextVendor(m[1]);
  if ((m = RE_STOP_VND.exec(text)))   return cancelVendor(m[1]);
  if ((m = RE_LIST_VND.exec(text)))   return listVendors(m[1] || null);
  if (RE_LIST_GSTS.test(text))        return listGuests();
  if (RE_STATUS.test(text))           return showStatus();
  if (RE_HELP.test(text))             return showHelp();
}

async function handleGuestMessage(msg, { booking_uid, guest }) {
  const today = new Date().toISOString().slice(0, 10);
  if (guest.checkout < today) {
    await sendMsg(msg.from, `Your stay ended on ${guest.checkout}. We hope you had a great time! 🙏`);
    return;
  }
  try {
    const result = await callRouterWithRetry("/classify", {
      source: "whatsapp", guest_name: guest.guest_name, message: msg.body, reply_to: msg.from,
    });
    const { draft_id, msg_type, draft, vendor_type } = result;
    if (msg_type === "routine") {
      await sendMsg(msg.from, draft);
      callRouterWithRetry("/approve", { draft_id, action: "approve" }).catch(() => {});
    } else {
      pending.set(draft_id, { guestPhone: msg.from, draft, channel: "whatsapp", vendor_type, guest_name: guest.guest_name });
      saveJSON(FILES.pending, Object.fromEntries(pending));
      await sendMsg(HOST_PHONE, buildDraftNotice(draft_id, guest.guest_name, draft, "WhatsApp (guest)"));
    }
  } catch (err) {
    console.error("Guest message error:", err.message);
  }
}

async function handleGuestMessageWithExtension(msg, ctx) {
  const { booking_uid, guest } = ctx;
  if (guest.extension_offered) {
    const up = msg.body.trim().toUpperCase();
    if (up === "YES" || up.startsWith("YES,")) {
      await sendMsg(msg.from, `Great! 🎉 We'll check availability and your host will confirm shortly.`);
      await sendMsg(HOST_PHONE,
        `🏨 *Extension Request — ${guest.property}*\nGuest: *${guest.guest_name}*\n` +
        `Current checkout: ${guest.checkout}\n\nReply:\n• \`EXTEND YES ${booking_uid}\`\n• \`EXTEND NO ${booking_uid}\``);
      return;
    }
    if (up === "NO" || up.startsWith("NO,")) {
      guests.set(booking_uid, { ...guest, extension_offered: false });
      saveJSON(FILES.guests, Object.fromEntries(guests));
      await sendMsg(msg.from, `Understood! We'll see you at checkout. Have a great rest of your stay! 😊`);
      return;
    }
  }
  return handleGuestMessage(msg, ctx);
}

async function handleVendorResponse(msg, vendor) {
  const text  = msg.body.trim().toUpperCase();
  const req   = findActiveServiceRequest(vendor);
  if (!req) return;
  const isYes = /^(YES|OK|CONFIRMED|AVAILABLE|SURE|CAN DO)/.test(text);
  const isNo  = /^(NO|UNAVAILABLE|CANT|CAN'T|SORRY|BUSY|NOT AVAILABLE)/.test(text);
  if (isYes)      await onVendorConfirmed(req, vendor);
  else if (isNo)  await onVendorUnavailable(req, vendor);
}

function findActiveServiceRequest(vendor) {
  const vPhone = vendor.whatsapp.replace(/[^\d]/g, "");
  for (const [id, req] of services) {
    if (req.status === "contacted" && req.current_vendor_phone === vPhone) {
      return { id, ...req };
    }
  }
  return null;
}

async function onVendorConfirmed(req, vendor) {
  services.set(req.id, { ...req, status: "confirmed", confirmed_vendor: vendor.name });
  saveJSON(FILES.services, Object.fromEntries(services));
  await sendMsg(HOST_PHONE, `✅ *${label(req.vendor_type)} confirmed — ${vendor.name}*\nProperty: ${req.property}\nThey are on their way.`);
}

async function onVendorUnavailable(req, vendor) {
  const list      = VENDORS[req.vendor_type] || [];
  const nextIndex = (req.vendor_index || 0) + 1;
  const hasNext   = nextIndex < list.length;
  services.set(req.id, { ...req, status: "vendor_unavailable" });
  saveJSON(FILES.services, Object.fromEntries(services));
  let msg = `⚠️ *${vendor.name} is unavailable* for ${label(req.vendor_type)} at ${req.property}.`;
  if (hasNext) {
    msg += `\n\nProceed with *${list[nextIndex].name}*?\n• \`NEXT_VENDOR ${req.id}\`\n• \`STOP_VENDOR ${req.id}\``;
  } else {
    msg += `\n\nNo more backup vendors. Please arrange manually.`;
    services.set(req.id, { ...req, status: "failed" });
    saveJSON(FILES.services, Object.fromEntries(services));
  }
  await sendMsg(HOST_PHONE, msg);
}

async function onApprove(id) {
  const entry = pending.get(id);
  if (!entry) { await sendMsg(HOST_PHONE, `⚠️ Draft ${id} not found.`); return; }
  if (entry.guestPhone) await sendMsg(entry.guestPhone, entry.draft);
  callRouterWithRetry("/approve", { draft_id: id, action: "approve" }).catch(() => {});
  pending.delete(id);
  saveJSON(FILES.pending, Object.fromEntries(pending));
  await sendMsg(HOST_PHONE, "✅  Reply sent to guest.");
  if (entry.vendor_type) {
    const reqId = await createServiceRequest(entry.vendor_type, null, null, entry.guestPhone);
    await sendMsg(HOST_PHONE, `🔧 *Dispatch ${label(entry.vendor_type)}?*\n• \`VENDOR_YES ${reqId}\`\n• \`VENDOR_SKIP ${reqId}\``);
  }
}

async function onEdit(id, newText) {
  const entry = pending.get(id);
  if (!entry) { await sendMsg(HOST_PHONE, `⚠️ Draft ${id} not found.`); return; }
  if (entry.guestPhone) await sendMsg(entry.guestPhone, newText);
  callRouterWithRetry("/approve", { draft_id: id, action: "edit", edited_text: newText }).catch(() => {});
  pending.delete(id);
  saveJSON(FILES.pending, Object.fromEntries(pending));
  await sendMsg(HOST_PHONE, "✅  Edited reply sent.");
}

async function onSkip(id) {
  pending.delete(id);
  saveJSON(FILES.pending, Object.fromEntries(pending));
  callRouterWithRetry("/approve", { draft_id: id, action: "skip" }).catch(() => {});
  await sendMsg(HOST_PHONE, "⏭️  Skipped.");
}

async function registerGuest(booking_uid, rawNumber, nameOverride) {
  const reg = pendingReg.get(booking_uid);
  if (!reg) { await sendMsg(HOST_PHONE, `⚠️ No pending check-in for booking ${booking_uid}.`); return; }
  const phone     = rawNumber.replace(/[^\d+]/g, "");
  const formatted = phone.startsWith("+") ? phone : "+" + phone;
  const guestName = nameOverride || reg.guest_name;
  guests.set(booking_uid, {
    guest_name: guestName, wa_id: formatted, property: reg.property,
    checkin: reg.checkin, checkout: reg.checkout,
    welcome_sent: false, extension_offered: false, review_sent: false,
  });
  saveJSON(FILES.guests, Object.fromEntries(guests));
  pendingReg.delete(booking_uid);
  saveJSON(FILES.pendingReg, Object.fromEntries(pendingReg));
  await sendMsg(HOST_PHONE, `✅  *${guestName}* registered. Sending welcome message.`);
  await sendMsg(phone.replace(/[^\d]/g, ""),
    `Welcome to ${reg.property}, ${guestName}! 👋\n\nI'm the automated assistant. Ask me anything:\n` +
    `• 📶 WiFi password\n• 🅿️ Parking\n• 🔑 Access codes\n• 🕐 Check-out time\n\nYour host is notified for anything personal.`
  ).catch(() => {});
}

async function onExtendApproved(uid) {
  const g = guests.get(uid);
  if (g) await sendMsg(g.wa_id.replace(/[^\d]/g, ""),
    `Great news! 🎉 Your extension has been arranged. Enjoy your extended stay!`);
  await sendMsg(HOST_PHONE, "✅  Extension confirmed — guest notified.");
}

async function onExtendDenied(uid) {
  const g = guests.get(uid);
  if (g) await sendMsg(g.wa_id.replace(/[^\d]/g, ""),
    `Thanks for asking! Unfortunately we can't extend due to another booking. Hope you had a great stay! 🙏`);
  await sendMsg(HOST_PHONE, "✅  Extension declined — guest notified.");
}

let _reqCounter = Date.now();
const newReqId = () => `req_${(++_reqCounter).toString(36)}`;

async function createServiceRequest(vendor_type, booking_uid, property, guest_phone) {
  const id = newReqId();
  services.set(id, { vendor_type, booking_uid, property, guest_phone, vendor_index: 0, status: "pending" });
  saveJSON(FILES.services, Object.fromEntries(services));
  return id;
}

async function dispatchVendor(req_id) {
  const req  = services.get(req_id);
  if (!req) return;
  const list = VENDORS[req.vendor_type] || [];
  const v    = list[req.vendor_index || 0];
  if (!v) { await sendMsg(HOST_PHONE, `⚠️ No vendor for ${label(req.vendor_type)}.`); return; }
  const vPhone = v.whatsapp.replace(/[^\d+]/g, "").replace(/^\+/, "");
  services.set(req_id, { ...req, status: "contacted", current_vendor_phone: vPhone });
  saveJSON(FILES.services, Object.fromEntries(services));
  await sendMsg(vPhone, `Hi ${v.name}! 🏠\nWe need ${label(req.vendor_type)} at *${req.property || "our property"}*.\nAvailable? Reply *YES* or *NO*.\nRef: ${req_id}`);
  await sendMsg(HOST_PHONE, `📤  Contacting ${v.name} (${label(req.vendor_type)})…`);
}

async function tryNextVendor(req_id) {
  const req  = services.get(req_id);
  if (!req) return;
  const list = VENDORS[req.vendor_type] || [];
  const next = (req.vendor_index || 0) + 1;
  if (next >= list.length) {
    await sendMsg(HOST_PHONE, `⚠️ No more backup ${label(req.vendor_type)} vendors.`);
    services.set(req_id, { ...req, status: "failed" });
    saveJSON(FILES.services, Object.fromEntries(services));
    return;
  }
  services.set(req_id, { ...req, vendor_index: next });
  saveJSON(FILES.services, Object.fromEntries(services));
  await dispatchVendor(req_id);
}

async function cancelVendor(req_id) {
  services.set(req_id, { ...services.get(req_id), status: "cancelled" });
  saveJSON(FILES.services, Object.fromEntries(services));
  await sendMsg(HOST_PHONE, "⏭️  Service request cancelled.");
}

async function listVendors(typeInput) {
  let msg = "📋 *Vendor Contacts:*\n";
  for (const [type, list] of Object.entries(VENDORS)) {
    if (!list?.length || (typeInput && type !== typeInput)) continue;
    msg += `\n*${label(type)}s:*\n`;
    list.forEach((v, i) => { msg += `  ${i+1}. ${v.name}${i===0?" ⭐":""}\n`; });
  }
  await sendMsg(HOST_PHONE, msg);
}

async function listGuests() {
  if (!guests.size) { await sendMsg(HOST_PHONE, "No registered guest sessions."); return; }
  const today = new Date().toISOString().slice(0, 10);
  let msg = `🏠 *Guest Sessions (${guests.size}):*\n\n`;
  for (const [uid, g] of guests) {
    const status = g.checkout < today ? "✅ checked out" : "🟢 active";
    msg += `*${g.guest_name}* | ${g.checkin}→${g.checkout} ${status}\n  ${uid.slice(0,12)}…\n\n`;
  }
  await sendMsg(HOST_PHONE, msg);
}

async function showStatus() {
  const today = new Date().toISOString().slice(0, 10);
  const active = Array.from(guests.values()).filter(g => g.checkout >= today).length;
  await sendMsg(HOST_PHONE,
    `📊 *System Status (business_api mode)*\n\n` +
    `Pending drafts: ${pending.size}\n` +
    `Active guests: ${active} / ${guests.size}\n` +
    `Service requests: ${Array.from(services.values()).filter(s=>s.status==="contacted").length} active\n\n` +
    `Reply HELP for all commands`);
}

async function showHelp() {
  await sendMsg(HOST_PHONE,
    `🤖 *Host Commands*\n\n` +
    `APPROVE/EDIT/SKIP [id] — drafts\n` +
    `GUEST_WA [uid] +num [name] — register guest\n` +
    `EXTEND YES/NO [uid] — extension\n` +
    `VENDOR_YES/SKIP/NEXT_VENDOR/STOP_VENDOR [id]\n` +
    `LIST_VENDORS [type]\n` +
    `LIST_GUESTS | STATUS | HELP`);
}

// ---------------------------------------------------------------------------
// Router helpers (identical to bot.js)
// ---------------------------------------------------------------------------
function callRouterWithRetry(endpoint, body, attempts = 3) {
  const delays = [2000, 4000, 8000];
  let lastErr;
  const attempt = async (i) => {
    try { return await callRouter(endpoint, body); }
    catch (err) {
      lastErr = err;
      if (i < attempts - 1) {
        await new Promise(r => setTimeout(r, delays[i]));
        return attempt(i + 1);
      }
      throw lastErr;
    }
  };
  return attempt(0);
}

function callRouter(endpoint, body) {
  return new Promise((resolve, reject) => {
    const data    = JSON.stringify(body);
    const headers = { "Content-Type": "application/json", "Content-Length": Buffer.byteLength(data) };
    if (INTERNAL_TOKEN) headers["X-Internal-Token"] = INTERNAL_TOKEN;
    const parsed  = new URL(ROUTER_URL + endpoint);
    const req = http.request(
      { hostname: parsed.hostname, port: Number(parsed.port)||80, path: parsed.pathname, method: "POST", headers, timeout: 15000 },
      res => { let out=""; res.on("data",c=>out+=c); res.on("end",()=>{ try{resolve(JSON.parse(out))}catch{resolve({})} }); }
    );
    req.on("timeout", () => req.destroy(new Error("Timeout")));
    req.on("error", reject);
    req.write(data); req.end();
  });
}

// ---------------------------------------------------------------------------
// HTTP server — two roles:
//   1. Meta webhook (GET /webhook verification + POST /webhook messages)
//   2. Internal bot endpoints (/notify-host, /guest-checkin, /offer-extension, /post-checkout)
// ---------------------------------------------------------------------------
const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost`);

  // ── Meta webhook verification (GET) ─────────────────────────────────────
  if (req.method === "GET" && url.pathname === "/webhook") {
    const mode      = url.searchParams.get("hub.mode");
    const token     = url.searchParams.get("hub.verify_token");
    const challenge = url.searchParams.get("hub.challenge");
    if (mode === "subscribe" && token === VERIFY_TOKEN) {
      res.writeHead(200); res.end(challenge);
    } else {
      res.writeHead(403); res.end();
    }
    return;
  }

  if (req.method !== "POST") { res.writeHead(405); res.end(); return; }

  let bodyBytes = 0, rawBody = "";
  req.on("data", chunk => {
    bodyBytes += chunk.length;
    if (bodyBytes > MAX_BODY_BYTES) { res.writeHead(413); res.end(); req.destroy(); return; }
    rawBody += chunk;
  });

  req.on("end", async () => {
    let body;
    try { body = JSON.parse(rawBody); }
    catch { res.writeHead(400); res.end(); return; }

    // ── Meta webhook messages (POST /webhook) ──────────────────────────────
    if (url.pathname === "/webhook") {
      try {
        const entry = body?.entry?.[0]?.changes?.[0]?.value;
        if (entry?.messages) {
          for (const msg of entry.messages) {
            if (msg.type === "text") {
              await routeIncoming(msg.from, msg.text.body).catch(e =>
                console.error("Route error:", e.message));
            }
          }
        }
        res.writeHead(200); res.end("EVENT_RECEIVED");
      } catch (err) {
        console.error("Webhook error:", err.message);
        res.writeHead(200); res.end("EVENT_RECEIVED"); // always 200 to Meta
      }
      return;
    }

    // ── Internal bot endpoints (auth required) ──────────────────────────────
    if (INTERNAL_TOKEN && req.headers["x-internal-token"] !== INTERNAL_TOKEN) {
      res.writeHead(401); res.end(); return;
    }

    try {
      switch (url.pathname) {
        case "/notify-host": {
          const { draft_id, guest_name, draft, channel } = body;
          if (!draft_id || !guest_name || !draft) throw new Error("Missing fields");
          pending.set(draft_id, { guestPhone: null, draft, channel, guest_name });
          saveJSON(FILES.pending, Object.fromEntries(pending));
          await sendMsg(HOST_PHONE, buildDraftNotice(draft_id, guest_name, draft, channel || "Email"));
          break;
        }
        case "/guest-checkin": {
          const { booking_uid, guest_name, property, checkin, checkout } = body;
          pendingReg.set(booking_uid, { guest_name, property, checkin, checkout });
          saveJSON(FILES.pendingReg, Object.fromEntries(pendingReg));
          await sendMsg(HOST_PHONE,
            `🏠 *${property}* — *${guest_name}* has checked in!\n\n` +
            `Reply:\n\`GUEST_WA ${booking_uid} +[number]\``);
          break;
        }
        case "/offer-extension": {
          const { booking_uid, guest_name, property, checkout } = body;
          const g = guests.get(booking_uid);
          if (!g?.wa_id) {
            await sendMsg(HOST_PHONE, `⏰  *${guest_name}* checks out today (guest WA not registered).`);
            break;
          }
          if (!g.extension_offered) {
            const checkoutHour = process.env.CHECKOUT_BRIEF_HOUR || "11";
            await sendMsg(g.wa_id.replace(/[^\d]/g,""),
              `Hi ${guest_name}! 🌟 Your checkout is today at ${checkoutHour}:00.\nWould you like to *extend*?\nReply *YES* or *NO*.`);
            guests.set(booking_uid, { ...g, extension_offered: true });
            saveJSON(FILES.guests, Object.fromEntries(guests));
          }
          break;
        }
        case "/post-checkout": {
          const { booking_uid, guest_name, property } = body;
          const g = guests.get(booking_uid);
          await sendMsg(HOST_PHONE, `🏁 *${guest_name}* checked out of *${property}*. Contacting cleaner.`);
          if (g?.wa_id && !g.review_sent) {
            const phone = g.wa_id.replace(/[^\d]/g, "");
            await sendMsg(phone,
              `Hi ${guest_name}! 🙏 Thank you for staying at ${property}!\n\n` +
              `We'd love a review on Airbnb — it means the world to us.\n` +
              (AIRBNB_LISTING || `Search for ${property} on Airbnb.`) +
              `\n\nHope to host you again! 🏠✨`).catch(()=>{});
            guests.set(booking_uid, { ...g, review_sent: true });
            saveJSON(FILES.guests, Object.fromEntries(guests));
          }
          const reqId = await createServiceRequest("cleaners", booking_uid, property, g?.wa_id || null);
          await dispatchVendor(reqId);
          break;
        }
        default: res.writeHead(404); res.end(); return;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok" }));
    } catch (err) {
      console.error(`Handler error (${url.pathname}):`, err.message);
      res.writeHead(500); res.end();
    }
  });
});

server.listen(WA_BOT_PORT, "0.0.0.0", () => {
  console.log(`\n✅  WhatsApp Business API mode`);
  console.log(`📡  Webhook server on :${WA_BOT_PORT}`);
  console.log(`    GET  /webhook  — Meta verification`);
  console.log(`    POST /webhook  — incoming messages from Meta`);
  console.log(`    POST /notify-host /guest-checkin /offer-extension /post-checkout\n`);
  console.log(`🔑  Set webhook URL in Meta dashboard to: https://your-domain.com/webhook`);
  console.log(`🔑  Verify token: ${VERIFY_TOKEN}\n`);
});
