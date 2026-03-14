/**
 * © 2024 Jestin Rajan. All rights reserved.
 * Licensed under the Airbnb Host AI License Agreement.
 * Unauthorized copying, distribution or use is prohibited.
 *
 * Airbnb Host WhatsApp Companion Bot
 * ====================================
 * Three-way routing on the host's personal WhatsApp number:
 *
 *   HOST messages   → approval commands, guest registration, vendor dispatch
 *   GUEST messages  → AI classifier → auto-reply (routine) or draft to host (complex)
 *   VENDOR messages → YES/NO availability → cascade or confirm to host
 *
 * HTTP endpoints (called by calendar_watcher.py + email_watcher.py):
 *   POST /notify-host      email draft → host approval
 *   POST /guest-checkin    guest arrived → ask host for guest WA number
 *   POST /offer-extension  2h before checkout → offer extension to guest
 *   POST /post-checkout    checkout → review request to guest + cleaner cascade
 */

"use strict";

const path   = require("path");
const fs     = require("fs");
const http   = require("http");
const https  = require("https");
const { URL } = require("url");

require("dotenv").config({ path: path.join(__dirname, "../.env") });

const { Client, LocalAuth } = require("whatsapp-web.js");
const qrcode = require("qrcode-terminal");

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const HOST_NUMBER     = (process.env.HOST_WHATSAPP_NUMBER || "").trim();
if (!HOST_NUMBER) { console.error("❌  HOST_WHATSAPP_NUMBER not set"); process.exit(1); }

const ROUTER_URL      = `http://127.0.0.1:${process.env.ROUTER_PORT || 7771}`;
const WA_BOT_PORT     = parseInt(process.env.WA_BOT_PORT || "7772", 10);
const INTERNAL_TOKEN  = process.env.INTERNAL_TOKEN || "";
const MAX_BODY_BYTES  = 65536;
const AIRBNB_LISTING  = (process.env.AIRBNB_LISTING_URL || "").trim();

const HOST_WA_ID      = HOST_NUMBER.replace(/^\+/, "") + "@c.us";

// ---------------------------------------------------------------------------
// State files (all in scripts/whatsapp/)
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

// In-memory Maps backed by disk
const pending    = new Map(Object.entries(loadJSON(FILES.pending)));
const guests     = new Map(Object.entries(loadJSON(FILES.guests)));
const services   = new Map(Object.entries(loadJSON(FILES.services)));
const pendingReg = new Map(Object.entries(loadJSON(FILES.pendingReg)));
console.log(`📂  Loaded: ${pending.size} pending, ${guests.size} guests, ${services.size} service requests`);

// ---------------------------------------------------------------------------
// Vendor registry (scripts/vendors.json — host configures)
// ---------------------------------------------------------------------------
let VENDORS = { cleaners: [], ac_technicians: [], plumbers: [], electricians: [], locksmiths: [] };
try {
  VENDORS = JSON.parse(fs.readFileSync(path.join(__dirname, "../vendors.json"), "utf8"));
  delete VENDORS._comment;
  console.log(
    `📋  Vendors: ${VENDORS.cleaners?.length || 0} cleaners, ` +
    `${VENDORS.ac_technicians?.length || 0} AC techs, ` +
    `${VENDORS.plumbers?.length || 0} plumbers, ` +
    `${VENDORS.electricians?.length || 0} electricians, ` +
    `${VENDORS.locksmiths?.length || 0} locksmiths`
  );
} catch (e) { console.warn("⚠️  vendors.json not found — vendor cascade disabled"); }

function toWaId(num) {
  return num.replace(/^\+/, "").replace(/\s/g, "") + "@c.us";
}

// Build lookup: vendorWaId → {name, type, index}
const vendorMap = new Map();
for (const [type, list] of Object.entries(VENDORS)) {
  (list || []).forEach((v, i) => vendorMap.set(toWaId(v.whatsapp), { ...v, type, index: i }));
}

// ---------------------------------------------------------------------------
// WhatsApp client
// ---------------------------------------------------------------------------
const client = new Client({
  authStrategy: new LocalAuth({ dataPath: path.join(__dirname, ".wwebjs_auth") }),
  puppeteer: { args: ["--no-sandbox", "--disable-setuid-sandbox"], headless: true },
});

client.on("qr", (qr) => {
  console.log("\n📱  Scan QR code: WhatsApp → Linked Devices → Link a Device\n");
  qrcode.generate(qr, { small: true });
});
client.on("loading_screen", (pct, msg) => process.stdout.write(`\r⏳  ${pct}% ${msg}   `));
client.on("authenticated", () => console.log("\n🔐  Authenticated."));
client.on("ready", () => {
  console.log(`✅  Bot ready  |  Host: ${HOST_NUMBER}  |  Router: ${ROUTER_URL}  |  HTTP: ${WA_BOT_PORT}\n`);
});
client.on("disconnected", (r) => console.warn("⚠️  Disconnected:", r));

// ---------------------------------------------------------------------------
// Message routing
// ---------------------------------------------------------------------------
client.on("message", async (msg) => {
  const from = msg.from;
  if (!msg.body) return;

  // 1. Host commands
  if (from === HOST_WA_ID) {
    await handleHostMessage(msg.body.trim());
    return;
  }

  // 2. Skip groups / broadcasts
  if (msg.isGroupMsg || from === "status@broadcast") return;

  // 3. Registered guest?
  const guestEntry = findGuestByWaId(from);
  if (guestEntry) {
    await handleGuestMessage(msg, guestEntry);
    return;
  }

  // 4. Registered vendor?
  const vendor = vendorMap.get(from);
  if (vendor) {
    await handleVendorResponse(msg, vendor);
    return;
  }

  // 5. Unknown — check if we're waiting for a guest WA registration for THIS number
  // (host might have registered a guest number that texted first — ignore)
});

// ---------------------------------------------------------------------------
// HOST message handler
// ---------------------------------------------------------------------------
// APPROVE/EDIT/SKIP        [draft_id]              → draft approval
// GUEST_WA [booking_uid] +NUMBER [optional name]   → register guest
// EXTEND YES|NO [booking_uid]                      → extension response
// VENDOR_YES|VENDOR_SKIP [req_id]                  → dispatch or cancel vendor
// NEXT_VENDOR [req_id]                             → try next vendor
// STOP_VENDOR [req_id]                             → stop cascade

// ── Draft approval ──────────────────────────────────────────────────────────
const RE_APPROVE    = /^APPROVE\s+(\S+)$/i;
const RE_EDIT       = /^EDIT\s+(\S+):\s*([\s\S]{1,2000})$/i;
const RE_SKIP       = /^SKIP\s+(\S+)$/i;
// ── Guest registration & extension ─────────────────────────────────────────
const RE_GUEST_WA   = /^GUEST_WA\s+(\S+)\s+(\+?\d[\d\s\-]{6,})(.*)?$/i;
const RE_EXTEND_YES = /^EXTEND\s+YES\s+(\S+)$/i;
const RE_EXTEND_NO  = /^EXTEND\s+NO\s+(\S+)$/i;
// ── Vendor dispatch ─────────────────────────────────────────────────────────
const RE_VENDOR_YES = /^VENDOR_YES\s+(\S+)$/i;
const RE_VENDOR_SKP = /^VENDOR_SKIP\s+(\S+)$/i;
const RE_NEXT_VND   = /^NEXT_VENDOR\s+(\S+)$/i;
const RE_STOP_VND   = /^STOP_VENDOR\s+(\S+)$/i;
// ── Vendor management (CRUD via WhatsApp) ───────────────────────────────────
const RE_ADD_VND    = /^ADD_VENDOR\s+(\S+)\s+(.+?)\s+(\+?\d[\d\s()\-\.]{6,})$/i;
const RE_REM_VND    = /^REMOVE_VENDOR\s+(\S+)\s+(.+)$/i;
const RE_LIST_VND   = /^LIST_VENDORS?(?:\s+(\S+))?$/i;
const RE_SET_PRIM   = /^SET_PRIMARY\s+(\S+)\s+(.+)$/i;
// ── Guest management ────────────────────────────────────────────────────────
const RE_MSG_GUEST  = /^MSG_GUEST\s+(\S+):\s*([\s\S]{1,2000})$/i;
const RE_REM_GUEST  = /^REMOVE_GUEST\s+(\S+)$/i;
const RE_LIST_GSTS  = /^LIST_GUESTS?$/i;
// ── System & info ───────────────────────────────────────────────────────────
const RE_LIST_PND   = /^LIST_PENDING$/i;
const RE_STATUS     = /^STATUS$/i;
const RE_HELP       = /^HELP$/i;

async function handleHostMessage(text) {
  let m;

  // — Draft approvals —
  if ((m = RE_APPROVE.exec(text))) return onApprove(m[1]);
  if ((m = RE_EDIT.exec(text)))    return onEdit(m[1], m[2].trim());
  if ((m = RE_SKIP.exec(text)))    return onSkip(m[1]);

  // — Guest registration —
  if ((m = RE_GUEST_WA.exec(text))) {
    const uid   = m[1];
    const num   = m[2].trim().replace(/\s/g, "");
    const name  = m[3]?.trim() || null;
    return registerGuest(uid, num, name);
  }

  // — Extension —
  if ((m = RE_EXTEND_YES.exec(text))) return onExtendApproved(m[1]);
  if ((m = RE_EXTEND_NO.exec(text)))  return onExtendDenied(m[1]);

  // — Vendor dispatch —
  if ((m = RE_VENDOR_YES.exec(text))) return dispatchVendor(m[1]);
  if ((m = RE_VENDOR_SKP.exec(text))) return cancelServiceRequest(m[1]);
  if ((m = RE_NEXT_VND.exec(text)))   return tryNextVendor(m[1]);
  if ((m = RE_STOP_VND.exec(text)))   return cancelServiceRequest(m[1]);

  // — Vendor management (CRUD) —
  if ((m = RE_ADD_VND.exec(text)))   return addVendor(m[1], m[2].trim(), m[3].trim());
  if ((m = RE_REM_VND.exec(text)))   return removeVendor(m[1], m[2].trim());
  if ((m = RE_LIST_VND.exec(text)))  return listVendors(m[1] || null);
  if ((m = RE_SET_PRIM.exec(text)))  return setPrimary(m[1], m[2].trim());

  // — Guest management —
  if ((m = RE_MSG_GUEST.exec(text))) return msgGuest(m[1], m[2].trim());
  if ((m = RE_REM_GUEST.exec(text))) return removeGuest(m[1]);
  if (RE_LIST_GSTS.test(text))       return listGuests();

  // — System —
  if (RE_LIST_PND.test(text))        return listPendingDrafts();
  if (RE_STATUS.test(text))          return showStatus();
  if (RE_HELP.test(text))            return showHelp();
}

// ---------------------------------------------------------------------------
// GUEST message handler
// ---------------------------------------------------------------------------
async function handleGuestMessage(msg, { booking_uid, guest }) {
  const text = msg.body;
  const from = msg.from;

  // Guest messaging after checkout — politely decline
  const today = new Date().toISOString().slice(0, 10);
  if (guest.checkout < today) {
    await client.sendMessage(from,
      `Your stay ended on ${guest.checkout}. We hope you had a great time! 🙏\n` +
      `Feel free to book with us again at airbnb.com.`);
    return;
  }

  console.log(`📨  [GUEST: ${guest.guest_name}] ${text.slice(0, 80)}`);

  try {
    const result = await callRouterWithRetry("/classify", {
      source:     "whatsapp",
      guest_name: guest.guest_name,
      message:    text,
      reply_to:   from,
    });

    const { draft_id, msg_type, draft, vendor_type } = result;

    if (msg_type === "routine") {
      await client.sendMessage(from, draft);
      console.log(`  ✅  Auto-replied to guest ${guest.guest_name} (routine)`);
      callRouterWithRetry("/approve", { draft_id, action: "approve" }).catch(() => {});
    } else {
      pending.set(draft_id, { guestChatId: from, draft, channel: "whatsapp", vendor_type, guest_name: guest.guest_name });
      saveJSON(FILES.pending, Object.fromEntries(pending));

      let notice = buildDraftNotice(draft_id, guest.guest_name, draft, "WhatsApp (guest)");
      if (vendor_type) {
        notice += `\n\n⚠️  *Maintenance issue detected (${label(vendor_type)}).*\n` +
                  `After approving: reply \`VENDOR_YES [req_id]\` to dispatch a technician.`;
      }
      await client.sendMessage(HOST_WA_ID, notice);
      console.log(`  ⏳  Complex — draft sent to host for approval`);
    }
  } catch (err) {
    console.error(`  ❌  Guest message error:`, err.message);
  }
}

// ---------------------------------------------------------------------------
// VENDOR response handler
// ---------------------------------------------------------------------------
async function handleVendorResponse(msg, vendor) {
  const text    = msg.body.trim().toUpperCase();
  const req     = findActiveServiceRequestForVendor(vendor);
  if (!req) return;

  const isYes   = /^(YES|OK|CONFIRMED|AVAILABLE|SURE|CAN DO)/.test(text);
  const isNo    = /^(NO|UNAVAILABLE|CANT|CAN'T|SORRY|BUSY|NOT AVAILABLE)/.test(text);

  if (isYes) {
    await onVendorConfirmed(req, vendor);
  } else if (isNo) {
    await onVendorUnavailable(req, vendor);
  }
  // Ambiguous reply — wait for clearer answer
}

function findActiveServiceRequestForVendor(vendor) {
  for (const [id, req] of services) {
    if (req.status === "contacted" && req.current_vendor_wa === toWaId(vendor.whatsapp)) {
      return { id, ...req };
    }
  }
  return null;
}

async function onVendorConfirmed(req, vendor) {
  services.set(req.id, { ...req, status: "confirmed", confirmed_vendor: vendor.name });
  saveJSON(FILES.services, Object.fromEntries(services));

  const typeLabel = label(req.vendor_type);
  await client.sendMessage(HOST_WA_ID,
    `✅ *${typeLabel} confirmed — ${vendor.name}*\n` +
    `Property: ${req.property}\n` +
    `They are on their way.`);

  // If guest is still registered, optionally notify them (AC fix only)
  if (req.guest_wa_id && req.vendor_type === "ac_technicians") {
    const guestMsg =
      `Hi! Just to let you know, our ${typeLabel.toLowerCase()} has confirmed ` +
      `and will be attending to the issue shortly. 🔧`;
    await client.sendMessage(req.guest_wa_id, guestMsg).catch(() => {});
  }

  // If there's a pending cleaner brief draft for this booking, send it to the cleaner
  if (req.vendor_type === "cleaners") {
    const briefDraft = findCleanerBriefDraft(req.booking_uid);
    if (briefDraft) {
      await client.sendMessage(toWaId(vendor.whatsapp),
        `📋 *Cleaning brief for ${req.property}:*\n\n${briefDraft}`).catch(() => {});
    }
  }
  console.log(`✅  Vendor ${vendor.name} confirmed for ${req.id}`);
}

async function onVendorUnavailable(req, vendor) {
  const reqEntry = services.get(req.id);
  const typeLabel = label(req.vendor_type);
  const vendorList = VENDORS[req.vendor_type] || [];
  const nextIndex  = (reqEntry.vendor_index || 0) + 1;
  const hasNext    = nextIndex < vendorList.length;

  services.set(req.id, { ...reqEntry, status: "vendor_unavailable" });
  saveJSON(FILES.services, Object.fromEntries(services));

  let msg = `⚠️ *${vendor.name} is unavailable* for ${typeLabel} at ${req.property}.`;
  if (hasNext) {
    const next = vendorList[nextIndex];
    msg += `\n\nProceed with backup: *${next.name}*?\n` +
           `• \`NEXT_VENDOR ${req.id}\` — yes, contact them\n` +
           `• \`STOP_VENDOR ${req.id}\` — handle manually`;
  } else {
    msg += `\n\nNo more backup vendors. Please arrange ${typeLabel} manually.`;
    services.set(req.id, { ...reqEntry, status: "failed" });
    saveJSON(FILES.services, Object.fromEntries(services));
  }
  await client.sendMessage(HOST_WA_ID, msg);
}

// ---------------------------------------------------------------------------
// Draft approval (APPROVE / EDIT / SKIP)
// ---------------------------------------------------------------------------
async function onApprove(id) {
  const entry = pending.get(id);
  if (!entry) return;
  if (entry.guestChatId) await client.sendMessage(entry.guestChatId, entry.draft);
  callRouterWithRetry("/approve", { draft_id: id, action: "approve" }).catch(() => {});
  pending.delete(id);
  saveJSON(FILES.pending, Object.fromEntries(pending));
  await client.sendMessage(HOST_WA_ID, "✅  Reply sent to guest.");

  // If maintenance issue, prompt host to dispatch vendor
  if (entry.vendor_type) {
    const reqId = await createServiceRequest(entry.vendor_type, null, null, entry.guestChatId);
    const typeLabel = label(entry.vendor_type);
    await client.sendMessage(HOST_WA_ID,
      `🔧 *Dispatch ${typeLabel}?*\n` +
      `• \`VENDOR_YES ${reqId}\` — contact ${VENDORS[entry.vendor_type]?.[0]?.name || "primary tech"}\n` +
      `• \`VENDOR_SKIP ${reqId}\` — handle manually`);
  }
}

async function onEdit(id, newText) {
  const entry = pending.get(id);
  if (!entry) return;
  if (entry.guestChatId) await client.sendMessage(entry.guestChatId, newText);
  callRouterWithRetry("/approve", { draft_id: id, action: "edit", edited_text: newText }).catch(() => {});
  pending.delete(id);
  saveJSON(FILES.pending, Object.fromEntries(pending));
  await client.sendMessage(HOST_WA_ID, "✅  Edited reply sent.");
}

async function onSkip(id) {
  if (!pending.has(id)) return;
  pending.delete(id);
  saveJSON(FILES.pending, Object.fromEntries(pending));
  callRouterWithRetry("/approve", { draft_id: id, action: "skip" }).catch(() => {});
  await client.sendMessage(HOST_WA_ID, "⏭️  Skipped.");
}

// ---------------------------------------------------------------------------
// Guest registration
// ---------------------------------------------------------------------------
async function registerGuest(booking_uid, rawNumber, nameOverride) {
  const reg = pendingReg.get(booking_uid);
  if (!reg) {
    await client.sendMessage(HOST_WA_ID,
      `⚠️  No pending check-in found for booking ${booking_uid}. ` +
      `The calendar watcher may not have detected it yet.`);
    return;
  }

  const wa_id      = toWaId(rawNumber.startsWith("+") ? rawNumber : "+" + rawNumber);
  const guestName  = nameOverride || reg.guest_name;

  const guestData = {
    guest_name:       guestName,
    wa_id,
    property:         reg.property,
    checkin:          reg.checkin,
    checkout:         reg.checkout,
    welcome_sent:     false,
    extension_offered:false,
    review_sent:      false,
  };
  guests.set(booking_uid, guestData);
  saveJSON(FILES.guests, Object.fromEntries(guests));
  pendingReg.delete(booking_uid);
  saveJSON(FILES.pendingReg, Object.fromEntries(pendingReg));

  await client.sendMessage(HOST_WA_ID,
    `✅  *${guestName}* registered. Sending them a welcome message now.`);

  // Welcome message to guest
  const welcome =
    `Welcome to ${reg.property}, ${guestName}! 👋\n\n` +
    `I'm the automated assistant for your stay. Ask me anything:\n` +
    `• 📶 WiFi password\n• 🅿️ Parking info\n• 🔑 Access codes\n` +
    `• 🕐 Check-out time\n• Or anything else!\n\n` +
    `Your host will be notified for anything that needs personal attention.`;

  await client.sendMessage(wa_id, welcome).catch(async (e) => {
    console.error("Could not reach guest:", e.message);
    await client.sendMessage(HOST_WA_ID,
      `⚠️  Could not deliver welcome to ${guestName} — check the number is correct.`);
  });

  guests.set(booking_uid, { ...guestData, welcome_sent: true });
  saveJSON(FILES.guests, Object.fromEntries(guests));
  console.log(`✅  Guest ${guestName} registered (${booking_uid})`);
}

function findGuestByWaId(wa_id) {
  for (const [booking_uid, guest] of guests) {
    if (guest.wa_id === wa_id) return { booking_uid, guest };
  }
  return null;
}

// ---------------------------------------------------------------------------
// Extension flow
// ---------------------------------------------------------------------------
async function onExtendApproved(booking_uid) {
  const g = guests.get(booking_uid);
  if (!g) return;
  await client.sendMessage(g.wa_id,
    `Great news! 🎉 Your extension has been arranged. ` +
    `Your new checkout details will be confirmed by Airbnb shortly. Enjoy your extended stay!`);
  await client.sendMessage(HOST_WA_ID, `✅  Extension confirmed — guest notified.`);
}

async function onExtendDenied(booking_uid) {
  const g = guests.get(booking_uid);
  if (!g) return;
  await client.sendMessage(g.wa_id,
    `Thanks for asking! Unfortunately we're unable to extend for this period ` +
    `due to another booking. We hope you had a wonderful stay and look forward to hosting you again! 🙏`);
  await client.sendMessage(HOST_WA_ID, `✅  Extension declined — guest notified.`);
}

// ---------------------------------------------------------------------------
// Vendor cascade helpers
// ---------------------------------------------------------------------------
let _reqCounter = Date.now();
function newReqId() { return `req_${(++_reqCounter).toString(36)}`; }

async function createServiceRequest(vendor_type, booking_uid, property, guest_wa_id) {
  const id  = newReqId();
  const req = { vendor_type, booking_uid, property, guest_wa_id, vendor_index: 0, status: "pending", current_vendor_wa: null };
  services.set(id, req);
  saveJSON(FILES.services, Object.fromEntries(services));
  return id;
}

async function dispatchVendor(req_id) {
  const req = services.get(req_id);
  if (!req) return;
  const list = VENDORS[req.vendor_type] || [];
  const v    = list[req.vendor_index || 0];
  if (!v) {
    await client.sendMessage(HOST_WA_ID, `⚠️  No vendor configured for ${label(req.vendor_type)}.`);
    return;
  }
  await contactVendor(req_id, req, v, req.vendor_index || 0);
}

async function tryNextVendor(req_id) {
  const req  = services.get(req_id);
  if (!req) return;
  const list = VENDORS[req.vendor_type] || [];
  const next = (req.vendor_index || 0) + 1;
  if (next >= list.length) {
    await client.sendMessage(HOST_WA_ID, `⚠️  No more backup ${label(req.vendor_type)} vendors. Please arrange manually.`);
    services.set(req_id, { ...req, status: "failed" });
    saveJSON(FILES.services, Object.fromEntries(services));
    return;
  }
  await contactVendor(req_id, req, list[next], next);
}

async function cancelServiceRequest(req_id) {
  services.set(req_id, { ...services.get(req_id), status: "cancelled" });
  saveJSON(FILES.services, Object.fromEntries(services));
  await client.sendMessage(HOST_WA_ID, `⏭️  Service request cancelled — please arrange manually.`);
}

// ---------------------------------------------------------------------------
// Vendor management — CRUD via WhatsApp
// ---------------------------------------------------------------------------

// Normalize user-typed vendor type to canonical key in vendors.json
function normalizeVendorType(input) {
  const map = {
    cleaner:       "cleaners",      cleaners:       "cleaners",
    plumber:       "plumbers",      plumbers:       "plumbers",
    electrician:   "electricians",  electricians:   "electricians",
    locksmith:     "locksmiths",    locksmiths:     "locksmiths",
    ac:            "ac_technicians", ac_tech:       "ac_technicians",
    ac_technician: "ac_technicians", ac_technicians:"ac_technicians",
  };
  return map[input.toLowerCase().replace(/[\s\-]+/g, "_")] || null;
}

function saveVendorsJson() {
  const vendorsPath = path.join(__dirname, "../vendors.json");
  saveJSON(vendorsPath, VENDORS);
}

function rebuildVendorMap() {
  vendorMap.clear();
  for (const [type, list] of Object.entries(VENDORS)) {
    (list || []).forEach((v, i) => vendorMap.set(toWaId(v.whatsapp), { ...v, type, index: i }));
  }
}

async function addVendor(typeInput, name, rawNumber) {
  const type = normalizeVendorType(typeInput);
  if (!type) {
    await client.sendMessage(HOST_WA_ID,
      `⚠️ Unknown vendor type: *${typeInput}*\n` +
      `Valid types: cleaner, plumber, electrician, locksmith, ac`);
    return;
  }
  const number = rawNumber.replace(/[\s()\-\.]/g, "");
  const formatted = number.startsWith("+") ? number : "+" + number;
  if (!VENDORS[type]) VENDORS[type] = [];
  VENDORS[type].push({ name, whatsapp: formatted });
  saveVendorsJson();
  rebuildVendorMap();
  const pos = VENDORS[type].length;
  await client.sendMessage(HOST_WA_ID,
    `✅ *${name}* added as ${label(type)} #${pos}${pos === 1 ? " (primary)" : " (backup)"}.\n` +
    `Number: ${formatted}\n\n` +
    `Reply \`LIST_VENDORS ${typeInput}\` to confirm.`);
  console.log(`✅  Added vendor ${name} (${type})`);
}

async function removeVendor(typeInput, nameOrIndex) {
  const type = normalizeVendorType(typeInput);
  if (!type) {
    await client.sendMessage(HOST_WA_ID, `⚠️ Unknown vendor type: *${typeInput}*`);
    return;
  }
  const list = VENDORS[type] || [];
  if (!list.length) {
    await client.sendMessage(HOST_WA_ID, `⚠️ No ${label(type)} vendors configured.`);
    return;
  }
  let idx = parseInt(nameOrIndex, 10);
  if (!isNaN(idx)) {
    idx = idx - 1; // user sends 1-indexed
  } else {
    idx = list.findIndex(v => v.name.toLowerCase().includes(nameOrIndex.toLowerCase()));
  }
  if (idx < 0 || idx >= list.length) {
    await client.sendMessage(HOST_WA_ID,
      `⚠️ Vendor not found: "${nameOrIndex}"\n` +
      `Reply \`LIST_VENDORS ${typeInput}\` to see numbered list.`);
    return;
  }
  const [removed] = list.splice(idx, 1);
  VENDORS[type] = list;
  saveVendorsJson();
  rebuildVendorMap();
  await client.sendMessage(HOST_WA_ID,
    `✅ Removed *${removed.name}* from ${label(type)} list.\n` +
    `Remaining: ${list.length}`);
  console.log(`✅  Removed vendor ${removed.name} (${type})`);
}

async function listVendors(typeInput) {
  const types = typeInput
    ? [normalizeVendorType(typeInput)].filter(Boolean)
    : Object.keys(VENDORS);

  if (!types.length || (typeInput && !normalizeVendorType(typeInput))) {
    await client.sendMessage(HOST_WA_ID, `⚠️ Unknown vendor type: *${typeInput}*`);
    return;
  }

  let msg = "📋 *Vendor Contacts:*\n";
  let hasAny = false;
  for (const type of types) {
    const list = VENDORS[type] || [];
    if (!list.length) continue;
    hasAny = true;
    msg += `\n*${label(type)}s:*\n`;
    list.forEach((v, i) => {
      msg += `  ${i + 1}. ${v.name} — ${v.whatsapp}${i === 0 ? " ⭐" : ""}\n`;
    });
  }
  if (!hasAny) msg += "\n_No vendors configured yet._";

  msg += `\n_Commands: ADD_VENDOR, REMOVE_VENDOR, SET_PRIMARY_`;
  await client.sendMessage(HOST_WA_ID, msg);
}

async function setPrimary(typeInput, nameOrIndex) {
  const type = normalizeVendorType(typeInput);
  if (!type) {
    await client.sendMessage(HOST_WA_ID, `⚠️ Unknown vendor type: *${typeInput}*`);
    return;
  }
  const list = VENDORS[type] || [];
  let idx = parseInt(nameOrIndex, 10);
  if (!isNaN(idx)) {
    idx = idx - 1;
  } else {
    idx = list.findIndex(v => v.name.toLowerCase().includes(nameOrIndex.toLowerCase()));
  }
  if (idx <= 0 && isNaN(parseInt(nameOrIndex))) {
    await client.sendMessage(HOST_WA_ID, `⚠️ Vendor not found: "${nameOrIndex}"`);
    return;
  }
  if (idx === 0) {
    await client.sendMessage(HOST_WA_ID, `ℹ️ *${list[0].name}* is already the primary ${label(type)}.`);
    return;
  }
  const [v] = list.splice(idx, 1);
  list.unshift(v);
  VENDORS[type] = list;
  saveVendorsJson();
  rebuildVendorMap();
  await client.sendMessage(HOST_WA_ID,
    `✅ *${v.name}* is now the primary ${label(type)}.\n` +
    `They will be contacted first for all future ${label(type)} requests.`);
  console.log(`✅  Set ${v.name} as primary ${type}`);
}

// ---------------------------------------------------------------------------
// Guest management commands
// ---------------------------------------------------------------------------

async function msgGuest(booking_uid, text) {
  const g = guests.get(booking_uid);
  if (!g || !g.wa_id) {
    await client.sendMessage(HOST_WA_ID,
      `⚠️ No registered guest WhatsApp for booking ${booking_uid}.\n` +
      `Use \`LIST_GUESTS\` to see booking IDs.`);
    return;
  }
  await client.sendMessage(g.wa_id, text);
  await client.sendMessage(HOST_WA_ID, `✅ Message sent to *${g.guest_name}*.`);
  console.log(`📤  Host → guest ${g.guest_name}: ${text.slice(0, 60)}`);
}

async function removeGuest(booking_uid) {
  if (!guests.has(booking_uid)) {
    await client.sendMessage(HOST_WA_ID, `⚠️ No guest session found for booking ${booking_uid}.`);
    return;
  }
  const g = guests.get(booking_uid);
  guests.delete(booking_uid);
  saveJSON(FILES.guests, Object.fromEntries(guests));
  await client.sendMessage(HOST_WA_ID,
    `✅ Guest session for *${g.guest_name}* removed.\n` +
    `They will no longer be routed through the bot.`);
}

async function listGuests() {
  if (!guests.size) {
    await client.sendMessage(HOST_WA_ID, "No registered guest sessions.");
    return;
  }
  const today = new Date().toISOString().slice(0, 10);
  let msg = `🏠 *Guest Sessions (${guests.size}):*\n\n`;
  for (const [uid, g] of guests) {
    const status = g.checkout < today ? "✅ checked out" : "🟢 active";
    const short  = uid.length > 12 ? uid.slice(0, 12) + "…" : uid;
    msg += `*${g.guest_name}* (${short})\n`;
    msg += `  ${g.property} | ${g.checkin} → ${g.checkout} ${status}\n`;
    msg += `  📱 ${g.wa_id.replace("@c.us", "")}\n`;
    msg += `  _MSG_GUEST ${uid}: ..._\n\n`;
  }
  await client.sendMessage(HOST_WA_ID, msg);
}

// ---------------------------------------------------------------------------
// System commands
// ---------------------------------------------------------------------------

async function listPendingDrafts() {
  if (!pending.size) {
    await client.sendMessage(HOST_WA_ID, "✅ No pending drafts.");
    return;
  }
  let msg = `📋 *Pending drafts (${pending.size}):*\n\n`;
  for (const [id, entry] of pending) {
    const from   = entry.guest_name || entry.channel || "?";
    const preview = (entry.draft || "").slice(0, 80).replace(/\n/g, " ");
    msg += `• \`${id}\`\n  From: ${from}\n  "${preview}…"\n\n`;
  }
  msg += `_APPROVE/EDIT/SKIP [id] to action each draft_`;
  await client.sendMessage(HOST_WA_ID, msg);
}

async function showStatus() {
  const today = new Date().toISOString().slice(0, 10);
  const activeGuests   = Array.from(guests.values()).filter(g => g.checkout >= today).length;
  const activeServices = Array.from(services.values()).filter(s => s.status === "contacted").length;

  let vendorSummary = "";
  for (const [type, list] of Object.entries(VENDORS)) {
    if (list?.length) vendorSummary += `  ${label(type)}: ${list.length}\n`;
  }

  const msg =
    `📊 *System Status*\n\n` +
    `Pending drafts: ${pending.size}\n` +
    `Active guests:  ${activeGuests} (${guests.size} total)\n` +
    `Active service requests: ${activeServices}\n\n` +
    `*Vendors configured:*\n${vendorSummary || "  None\n"}\n` +
    `Router: ${ROUTER_URL}\n` +
    `Bot port: ${WA_BOT_PORT}\n\n` +
    `_Reply HELP for all commands_`;

  await client.sendMessage(HOST_WA_ID, msg);
}

async function showHelp() {
  const msg =
    `🤖 *Host Commands*\n` +
    `\n━━ Draft Approval ━━\n` +
    `\`APPROVE [id]\`            Send AI draft to guest\n` +
    `\`EDIT [id]: [text]\`       Edit draft then send\n` +
    `\`SKIP [id]\`               Discard draft\n` +
    `\n━━ Guest ━━\n` +
    `\`GUEST_WA [uid] +num\`     Register guest WhatsApp\n` +
    `\`MSG_GUEST [uid]: [text]\` Send message to guest\n` +
    `\`REMOVE_GUEST [uid]\`      Remove guest session\n` +
    `\`LIST_GUESTS\`             Show all guests\n` +
    `\`EXTEND YES/NO [uid]\`     Approve/deny extension\n` +
    `\n━━ Vendors ━━\n` +
    `\`LIST_VENDORS [type]\`     Show contacts (or all)\n` +
    `\`ADD_VENDOR [type] [name] +num\`  Add contact\n` +
    `\`REMOVE_VENDOR [type] [name/#]\` Remove contact\n` +
    `\`SET_PRIMARY [type] [name/#]\`   Set as #1 contact\n` +
    `\`VENDOR_YES [id]\`         Dispatch vendor now\n` +
    `\`VENDOR_SKIP [id]\`        Cancel service request\n` +
    `\`NEXT_VENDOR [id]\`        Try next backup\n` +
    `\`STOP_VENDOR [id]\`        Stop cascade\n` +
    `\n━━ System ━━\n` +
    `\`LIST_PENDING\`            Show pending drafts\n` +
    `\`STATUS\`                  System overview\n` +
    `\`HELP\`                    This message\n` +
    `\n_Vendor types: cleaner, plumber, electrician, locksmith, ac_`;
  await client.sendMessage(HOST_WA_ID, msg);
}

async function contactVendor(req_id, req, vendor, index) {
  const wa_id = toWaId(vendor.whatsapp);
  services.set(req_id, { ...req, status: "contacted", vendor_index: index, current_vendor_wa: wa_id });
  saveJSON(FILES.services, Object.fromEntries(services));

  const typeLabel = label(req.vendor_type);
  const msg =
    `Hi ${vendor.name}! 🏠\n\n` +
    `We need ${typeLabel} service at *${req.property || "our property"}* today.\n` +
    `Are you available? Please reply *YES* or *NO*.\n\n` +
    `Ref: ${req_id}`;
  await client.sendMessage(wa_id, msg);
  await client.sendMessage(HOST_WA_ID,
    `📤  Contacting ${vendor.name} (${typeLabel})… waiting for reply.`);
  console.log(`📤  Service request ${req_id} → ${vendor.name} (${wa_id})`);
}

function findCleanerBriefDraft(booking_uid) {
  // Look through pending for a cleaner brief draft linked to this booking
  for (const [, entry] of pending) {
    if (entry.channel === "calendar:cleaner" && entry.draft) return entry.draft;
  }
  return null;
}

const _VENDOR_LABELS = {
  cleaners:      "Cleaner",
  ac_technicians:"AC Technician",
  plumbers:      "Plumber",
  electricians:  "Electrician",
  locksmiths:    "Locksmith",
};
function label(vendor_type) {
  return _VENDOR_LABELS[vendor_type] || vendor_type;
}

// ---------------------------------------------------------------------------
// Build approval notice (host approval prompt)
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

// ---------------------------------------------------------------------------
// HTTP server — calendar_watcher + email_watcher call these endpoints
// ---------------------------------------------------------------------------
const server = http.createServer((req, res) => {
  if (req.method !== "POST") { res.writeHead(405); res.end(); return; }

  // Auth check
  if (INTERNAL_TOKEN && req.headers["x-internal-token"] !== INTERNAL_TOKEN) {
    res.writeHead(401); res.end(); return;
  }

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

    try {
      switch (req.url) {
        case "/notify-host":    await handleHttpNotifyHost(body); break;
        case "/guest-checkin":  await handleHttpGuestCheckin(body); break;
        case "/offer-extension":await handleHttpOfferExtension(body); break;
        case "/post-checkout":  await handleHttpPostCheckout(body); break;
        default: res.writeHead(404); res.end(); return;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ status: "ok" }));
    } catch (err) {
      console.error(`HTTP handler error (${req.url}):`, err.message);
      res.writeHead(500); res.end();
    }
  });
});

// ── /notify-host  (email drafts) ──────────────────────────────────────────
async function handleHttpNotifyHost({ draft_id, guest_name, draft, channel }) {
  if (!draft_id || !guest_name || !draft) throw new Error("Missing fields");
  pending.set(draft_id, { guestChatId: null, draft, channel, guest_name });
  saveJSON(FILES.pending, Object.fromEntries(pending));
  await client.sendMessage(HOST_WA_ID, buildDraftNotice(draft_id, guest_name, draft, channel || "Email"));
  console.log(`📧  Email draft from ${guest_name} sent to host`);
}

// ── /guest-checkin  (guest arrived, ask host for guest WA) ────────────────
async function handleHttpGuestCheckin({ booking_uid, guest_name, property, checkin, checkout }) {
  pendingReg.set(booking_uid, { guest_name, property, checkin, checkout });
  saveJSON(FILES.pendingReg, Object.fromEntries(pendingReg));
  await client.sendMessage(HOST_WA_ID,
    `🏠 *${property}* — *${guest_name}* has checked in!\n\n` +
    `What's their WhatsApp number? Reply:\n` +
    `\`GUEST_WA ${booking_uid} +[number]\`\n\n` +
    `_(Optional: add their name after the number if different)_`);
  console.log(`🏠  Guest arrival notified — ${guest_name} (${booking_uid})`);
}

// ── /offer-extension  (2h before checkout) ────────────────────────────────
async function handleHttpOfferExtension({ booking_uid, guest_name, property, checkout }) {
  const g = guests.get(booking_uid);
  if (!g || !g.wa_id) {
    // Guest WA not registered — notify host instead
    await client.sendMessage(HOST_WA_ID,
      `⏰  *${guest_name}* at ${property} checks out today.\n` +
      `_(Could not offer extension — guest WhatsApp not registered)_`);
    return;
  }
  if (g.extension_offered) return;

  const checkoutTime = process.env.CHECKOUT_BRIEF_HOUR
    ? `${process.env.CHECKOUT_BRIEF_HOUR}:00`
    : "11:00 AM";

  await client.sendMessage(g.wa_id,
    `Hi ${guest_name}! 🌟 Your checkout is scheduled for *${checkout}* at ${checkoutTime}.\n\n` +
    `We hope you're having a wonderful stay! Would you like to *extend*?\n\n` +
    `Reply *YES* to request an extension, or *NO* if you'll check out as planned.`);

  // Set up a listener for guest YES/NO — we do it via message routing
  // Store extension state so we can handle their reply
  guests.set(booking_uid, { ...g, extension_offered: true });
  saveJSON(FILES.guests, Object.fromEntries(guests));
  console.log(`⏰  Extension offer sent to ${guest_name}`);
}

// ── /post-checkout  (checkout complete) ───────────────────────────────────
async function handleHttpPostCheckout({ booking_uid, guest_name, property }) {
  const g = guests.get(booking_uid);

  // 1. Notify host
  await client.sendMessage(HOST_WA_ID,
    `🏁 *${guest_name}* has checked out of *${property}*.\n` +
    `Cleaner is being contacted now.`);

  // 2. Review request to guest (if WA registered + not already sent)
  if (g?.wa_id && !g.review_sent) {
    const reviewMsg =
      `Hi ${guest_name}! 🙏 Thank you for staying at ${property}!\n\n` +
      `We hope you had a wonderful experience. ` +
      `We'd love it if you could spare 2 minutes to leave us a review on Airbnb — ` +
      `it means the world to us and helps future guests.\n\n` +
      (AIRBNB_LISTING ? AIRBNB_LISTING : `Just search for ${property} on Airbnb.\n\n`) +
      `Thanks again — hope to host you soon! 🏠✨`;
    await client.sendMessage(g.wa_id, reviewMsg).catch(() => {});
    guests.set(booking_uid, { ...g, review_sent: true });
    saveJSON(FILES.guests, Object.fromEntries(guests));
    console.log(`⭐  Review request sent to ${guest_name}`);
  }

  // 3. Start cleaner cascade
  const reqId = await createServiceRequest("cleaners", booking_uid, property, g?.wa_id || null);
  await dispatchVendor(reqId);
  console.log(`🧹  Cleaner cascade started (${reqId})`);
}

// ── Extension reply from guest ─────────────────────────────────────────────
// Intercept inside handleGuestMessage (routine path won't catch YES/NO — handle here)
// We patch the guest message handler to detect extension responses
const _origHandleGuest = handleGuestMessage;
async function handleGuestMessageWithExtension(msg, ctx) {
  const { booking_uid, guest } = ctx;
  if (guest.extension_offered) {
    const up = msg.body.trim().toUpperCase();
    if (up === "YES" || up === "YES." || up.startsWith("YES,")) {
      await client.sendMessage(msg.from,
        `Great! 🎉 We'll check availability and your host will confirm shortly.`);
      await client.sendMessage(HOST_WA_ID,
        `🏨 *Extension Request — ${guest.property}*\n` +
        `Guest: *${guest.guest_name}*\n` +
        `Current checkout: ${guest.checkout}\n\n` +
        `They want to extend. Please arrange in Airbnb then reply:\n` +
        `• \`EXTEND YES ${booking_uid}\`\n` +
        `• \`EXTEND NO ${booking_uid}\``);
      return;
    }
    if (up === "NO" || up === "NO." || up.startsWith("NO,")) {
      guests.set(booking_uid, { ...guest, extension_offered: false });
      saveJSON(FILES.guests, Object.fromEntries(guests));
      await client.sendMessage(msg.from,
        `Understood! We'll see you at checkout. Have a great rest of your stay! 😊`);
      return;
    }
  }
  return _origHandleGuest(msg, ctx);
}
// Override
global.handleGuestMessage = handleGuestMessageWithExtension;
// Re-bind in the message listener (Node module-level override)
client.removeAllListeners("message");
client.on("message", async (msg) => {
  const from = msg.from;
  if (!msg.body) return;
  if (from === HOST_WA_ID)          { await handleHostMessage(msg.body.trim()); return; }
  if (msg.isGroupMsg || from === "status@broadcast") return;
  const guestEntry = findGuestByWaId(from);
  if (guestEntry)                   { await handleGuestMessageWithExtension(msg, guestEntry); return; }
  const vendor = vendorMap.get(from);
  if (vendor)                       { await handleVendorResponse(msg, vendor); return; }
});

// ---------------------------------------------------------------------------
// Router HTTP helpers
// ---------------------------------------------------------------------------
function callRouterWithRetry(endpoint, body, attempts = 3) {
  const delays = [2000, 4000, 8000];
  let lastErr;
  const attempt = async (i) => {
    try { return await callRouter(endpoint, body); }
    catch (err) {
      lastErr = err;
      if (i < attempts - 1) {
        console.warn(`  ⚠️  Router ${endpoint} attempt ${i + 1} failed — retrying`);
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
    const parsed  = new URL(ROUTER_URL + endpoint);
    const headers = {
      "Content-Type": "application/json",
      "Content-Length": Buffer.byteLength(data),
    };
    if (INTERNAL_TOKEN) headers["X-Internal-Token"] = INTERNAL_TOKEN;
    const req = (parsed.protocol === "https:" ? https : http).request(
      { hostname: parsed.hostname, port: Number(parsed.port) || 80,
        path: parsed.pathname, method: "POST", headers, timeout: 15000 },
      res => {
        let out = "";
        res.on("data", c => out += c);
        res.on("end",  () => { try { resolve(JSON.parse(out)); } catch { resolve({}); } });
      }
    );
    req.on("timeout", () => req.destroy(new Error("Timeout")));
    req.on("error", reject);
    req.write(data); req.end();
  });
}

// ---------------------------------------------------------------------------
// Start
// ---------------------------------------------------------------------------
server.listen(WA_BOT_PORT, "127.0.0.1", () =>
  console.log(`📡  HTTP server on 127.0.0.1:${WA_BOT_PORT}`));

client.initialize();
