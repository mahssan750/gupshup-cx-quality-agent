#!/usr/bin/env node

import fs from "node:fs";
import path from "node:path";

const API_ORIGIN = "https://gupshuptest.assist.gupshup.io";
const LIST_PATH = "/api/91110d/agentassist-views/v1/brandview/571428/execution/filter/1";
const TRANSCRIPT_PATH = "/api/91110d/chat-msg/v1/customer/transcript/pdf";
const DUBAI_OFFSET = "+04:00";

function usage(exitCode = 0) {
  console.log(`
Gupshup transcript cascade downloader

Required:
  --auth-curl PATH        A recent "Copy as cURL" file from either captured API request

Date selection:
  --from YYYY-MM-DD       Select chats updated on/after this Dubai date
  --to YYYY-MM-DD         Select chats updated on/before this Dubai date
                          Defaults to the last 7 calendar days, including today

Transcript range:
  --transcript-mode entire30|selected
                          entire30 (default): mimic "Entire Conversation History
                          (Last 30 days)" for every selected chat
                          selected: include only messages inside --from/--to

Output and safety:
  --out PATH              Output folder (default: ./gupshup-export-FROM-to-TO)
  --concurrency N         Simultaneous transcript requests (default: 4)
  --page-size N           Chat-list page size (default: 250)
  --max-chats N           Limit downloads; 0 means all (default: 0)
  --delay-ms N            Delay after each chat per worker (default: 150)
  --plan-only             Enumerate matching chats without downloading transcripts
  --help                  Show this help

The script never stores the bearer token in its output. Re-run the same command after
replacing the cURL file with a fresh capture; completed ZIPs are detected and skipped.
`);
  process.exit(exitCode);
}

function parseArgs(argv) {
  const options = {
    authCurl: null,
    from: null,
    to: null,
    out: null,
    concurrency: 4,
    pageSize: 250,
    maxChats: 0,
    delayMs: 150,
    transcriptMode: "entire30",
    planOnly: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    const next = () => {
      if (i + 1 >= argv.length) throw new Error(`Missing value for ${arg}`);
      i += 1;
      return argv[i];
    };
    if (arg === "--auth-curl") options.authCurl = next();
    else if (arg === "--from") options.from = next();
    else if (arg === "--to") options.to = next();
    else if (arg === "--out") options.out = next();
    else if (arg === "--concurrency") options.concurrency = Number(next());
    else if (arg === "--page-size") options.pageSize = Number(next());
    else if (arg === "--max-chats") options.maxChats = Number(next());
    else if (arg === "--delay-ms") options.delayMs = Number(next());
    else if (arg === "--transcript-mode") options.transcriptMode = next();
    else if (arg === "--plan-only") options.planOnly = true;
    else if (arg === "--help" || arg === "-h") usage(0);
    else throw new Error(`Unknown argument: ${arg}`);
  }

  if (!options.authCurl) throw new Error("--auth-curl is required");
  if (!["entire30", "selected"].includes(options.transcriptMode)) {
    throw new Error("--transcript-mode must be entire30 or selected");
  }
  for (const [name, value] of [
    ["concurrency", options.concurrency],
    ["page-size", options.pageSize],
    ["max-chats", options.maxChats],
    ["delay-ms", options.delayMs],
  ]) {
    if (!Number.isInteger(value) || value < 0) throw new Error(`Invalid --${name}: ${value}`);
  }
  if (options.concurrency < 1 || options.concurrency > 12) {
    throw new Error("--concurrency must be between 1 and 12");
  }
  if (options.pageSize < 1 || options.pageSize > 499) {
    throw new Error("--page-size must be between 1 and 499");
  }
  return options;
}

function ymdInDubai(date = new Date()) {
  return new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Dubai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(date);
}

function addCalendarDays(ymd, days) {
  const [year, month, day] = ymd.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day));
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

function validateYmd(value, name) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new Error(`${name} must be YYYY-MM-DD`);
  const parsed = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(parsed.getTime()) || parsed.toISOString().slice(0, 10) !== value) {
    throw new Error(`${name} is not a valid date`);
  }
}

function dubaiRange(fromYmd, toYmd) {
  const fromMs = Date.parse(`${fromYmd}T00:00:00.000${DUBAI_OFFSET}`);
  const toMs = Date.parse(`${toYmd}T23:59:59.999${DUBAI_OFFSET}`);
  if (fromMs > toMs) throw new Error("--from cannot be later than --to");
  return { fromMs, toMs };
}

function capture(pattern, text, label) {
  const match = text.match(pattern);
  if (!match) throw new Error(`Could not parse ${label} from the cURL file`);
  return match[1].replaceAll("^", "").trim();
}

function parseCapturedCurl(filePath) {
  const raw = fs.readFileSync(filePath, "utf8");
  const token = capture(/authorization:\s*Bearer\s+([^\^"\r\n]+)/i, raw, "Authorization token");
  const header = (name, fallback = null) => {
    const regex = new RegExp(`${name}:\\s*([^\\^"\\r\\n]+)`, "i");
    const match = raw.match(regex);
    return match ? match[1].replaceAll("^", "").trim() : fallback;
  };
  return {
    token,
    brandId: header("brandid"),
    brandUserId: header("branduserid"),
    productId: header("productid", "4"),
    roleId: header("roleid"),
    timezone: header("timezone", "261"),
    offset: header("offset", "14400"),
  };
}

function decodeJwtExpiry(token) {
  try {
    const payload = token.split(".")[1].replaceAll("-", "+").replaceAll("_", "/");
    const decoded = JSON.parse(Buffer.from(payload, "base64").toString("utf8"));
    return decoded.exp ? new Date(decoded.exp * 1000) : null;
  } catch {
    return null;
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function safeJson(value) {
  return JSON.stringify(value).replaceAll("\r", "\\r").replaceAll("\n", "\\n");
}

async function fetchWithRetry(url, init, label, maxAttempts = 5) {
  let lastError;
  for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
    try {
      const response = await fetch(url, init);
      if (response.status === 401 || response.status === 403) {
        const error = new Error(`${label}: authentication expired or rejected (${response.status})`);
        error.fatalAuth = true;
        throw error;
      }
      if (response.ok) return response;
      const body = (await response.text()).slice(0, 500);
      const error = new Error(`${label}: HTTP ${response.status}: ${body}`);
      if (response.status !== 429 && response.status < 500) throw error;
      lastError = error;
    } catch (error) {
      if (error.fatalAuth) throw error;
      lastError = error;
    }
    if (attempt < maxAttempts) await sleep(Math.min(1000 * 2 ** (attempt - 1), 12000));
  }
  throw lastError;
}

async function main() {
  let options;
  try {
    options = parseArgs(process.argv.slice(2));
  } catch (error) {
    console.error(`Error: ${error.message}`);
    usage(2);
  }

  const today = ymdInDubai();
  options.to ??= today;
  options.from ??= addCalendarDays(options.to, -6);
  validateYmd(options.from, "--from");
  validateYmd(options.to, "--to");
  const selectionRange = dubaiRange(options.from, options.to);
  const transcriptFromYmd =
    options.transcriptMode === "entire30" ? addCalendarDays(options.to, -30) : options.from;
  const transcriptRange = dubaiRange(transcriptFromYmd, options.to);

  const auth = parseCapturedCurl(path.resolve(options.authCurl));
  for (const required of ["brandId", "brandUserId", "roleId"]) {
    if (!auth[required]) throw new Error(`Missing ${required} in the cURL file`);
  }
  const tokenExpiry = decodeJwtExpiry(auth.token);
  if (tokenExpiry && tokenExpiry.getTime() <= Date.now()) {
    throw new Error(`The captured bearer token expired at ${tokenExpiry.toISOString()}. Capture a fresh cURL.`);
  }

  const outputDir = path.resolve(options.out ?? `gupshup-export-${options.from}-to-${options.to}`);
  const zipDir = path.join(outputDir, "zips");
  const manifestPath = path.join(outputDir, "manifest.jsonl");
  const selectionPath = path.join(outputDir, "selection.json");
  fs.mkdirSync(zipDir, { recursive: true });

  const headers = {
    accept: "application/json, text/plain, */*",
    authorization: `Bearer ${auth.token}`,
    brandid: auth.brandId,
    branduserid: auth.brandUserId,
    productid: auth.productId,
    roleid: auth.roleId,
    timezone: auth.timezone,
    offset: auth.offset,
  };

  console.log(`Selecting chats updated ${options.from} through ${options.to} (Asia/Dubai).`);
  console.log(
    `Transcript range: ${transcriptFromYmd} through ${options.to} (${options.transcriptMode}).`,
  );
  if (tokenExpiry) console.log(`Captured token expires: ${tokenExpiry.toISOString()}`);

  const selected = [];
  const seen = new Set();
  let offset = 0;
  let totalAvailable = null;
  let reachedOlderChats = false;

  while (!reachedOlderChats) {
    const url = new URL(LIST_PATH, API_ORIGIN);
    url.searchParams.set("offset", String(offset));
    url.searchParams.set("size", String(options.pageSize));
    const response = await fetchWithRetry(url, { headers }, `Chat-list page at offset ${offset}`);
    const payload = await response.json();
    const chats = Array.isArray(payload.chats) ? payload.chats : [];
    totalAvailable ??= Number(payload.count ?? 0);
    if (chats.length === 0) break;

    for (const item of chats) {
      const meta = item?.metaDataDTO;
      if (!meta?.chatId || meta.id == null || !meta.updatedAt) continue;
      const updatedMs = Date.parse(meta.updatedAt);
      if (!Number.isFinite(updatedMs)) continue;
      if (updatedMs < selectionRange.fromMs) {
        reachedOlderChats = true;
        continue;
      }
      if (updatedMs <= selectionRange.toMs && !seen.has(String(meta.id))) {
        seen.add(String(meta.id));
        selected.push({
          id: String(meta.id),
          chatId: String(meta.chatId),
          updatedAt: meta.updatedAt,
          createdAt: meta.createdAt ?? null,
          status: meta.status ?? null,
          sessionStatus: meta.sessionStatus ?? null,
        });
      }
    }

    offset += chats.length;
    console.log(`Scanned ${offset}/${totalAvailable}; selected ${selected.length}.`);
    if (offset >= totalAvailable) break;
  }

  selected.sort((a, b) => Date.parse(b.updatedAt) - Date.parse(a.updatedAt));
  const workItems = options.maxChats > 0 ? selected.slice(0, options.maxChats) : selected;
  fs.writeFileSync(
    selectionPath,
    JSON.stringify(
      {
        generatedAt: new Date().toISOString(),
        selection: { from: options.from, to: options.to, timezone: "Asia/Dubai" },
        transcript: {
          from: transcriptFromYmd,
          to: options.to,
          mode: options.transcriptMode,
        },
        totalMatched: selected.length,
        queued: workItems.length,
        chats: workItems,
      },
      null,
      2,
    ),
  );
  console.log(`Matched ${selected.length} chats; queued ${workItems.length}.`);
  console.log(`Selection saved to ${selectionPath}`);
  if (options.planOnly) return;

  const completed = new Set();
  if (fs.existsSync(manifestPath)) {
    for (const line of fs.readFileSync(manifestPath, "utf8").split(/\r?\n/)) {
      if (!line.trim()) continue;
      try {
        const row = JSON.parse(line);
        if (row.state === "downloaded" || row.state === "existing") completed.add(String(row.id));
      } catch {
        // Ignore an incomplete final line from a previously interrupted run.
      }
    }
  }
  for (const chat of workItems) {
    const zipPath = path.join(zipDir, `${chat.id}.zip`);
    if (fs.existsSync(zipPath) && fs.statSync(zipPath).size > 0) completed.add(chat.id);
  }

  const pending = workItems.filter((chat) => !completed.has(chat.id));
  console.log(`Already complete: ${workItems.length - pending.length}; pending: ${pending.length}.`);
  if (pending.length === 0) return;

  let nextIndex = 0;
  let downloaded = 0;
  let failed = 0;
  let fatalError = null;

  const appendManifest = (row) => {
    fs.appendFileSync(manifestPath, `${safeJson({ at: new Date().toISOString(), ...row })}\n`);
  };

  async function downloadChat(chat) {
    const transcriptUrl = new URL(TRANSCRIPT_PATH, API_ORIGIN);
    transcriptUrl.searchParams.set("chatId", chat.chatId);
    transcriptUrl.searchParams.set("fromDate", String(transcriptRange.fromMs));
    transcriptUrl.searchParams.set("toDate", String(transcriptRange.toMs));
    transcriptUrl.searchParams.set("timeZoneId", auth.timezone);

    const response = await fetchWithRetry(
      transcriptUrl,
      { headers },
      `Transcript request for chat ${chat.id}`,
    );
    const body = await response.json();
    if (!body?.url || !/^https:\/\//i.test(body.url)) {
      throw new Error(`Transcript request for chat ${chat.id} did not return a download URL`);
    }
    const zipResponse = await fetchWithRetry(body.url, {}, `ZIP download for chat ${chat.id}`, 4);
    const bytes = Buffer.from(await zipResponse.arrayBuffer());
    if (bytes.length < 4 || bytes[0] !== 0x50 || bytes[1] !== 0x4b) {
      throw new Error(`Downloaded file for chat ${chat.id} is not a valid ZIP`);
    }
    const finalPath = path.join(zipDir, `${chat.id}.zip`);
    const tempPath = `${finalPath}.part`;
    fs.writeFileSync(tempPath, bytes);
    fs.renameSync(tempPath, finalPath);
    appendManifest({
      id: chat.id,
      chatId: chat.chatId,
      updatedAt: chat.updatedAt,
      state: "downloaded",
      bytes: bytes.length,
      file: path.relative(outputDir, finalPath),
    });
  }

  async function worker(workerNumber) {
    while (!fatalError) {
      const index = nextIndex;
      nextIndex += 1;
      if (index >= pending.length) return;
      const chat = pending[index];
      try {
        await downloadChat(chat);
        downloaded += 1;
      } catch (error) {
        if (error.fatalAuth) fatalError = error;
        failed += 1;
        appendManifest({
          id: chat.id,
          chatId: chat.chatId,
          updatedAt: chat.updatedAt,
          state: "failed",
          error: error.message,
          worker: workerNumber,
        });
        console.error(`Failed ${chat.id}: ${error.message}`);
      }
      const done = downloaded + failed;
      if (done % 25 === 0 || done === pending.length) {
        console.log(`Progress ${done}/${pending.length}: ${downloaded} downloaded, ${failed} failed.`);
      }
      if (options.delayMs > 0) await sleep(options.delayMs);
    }
  }

  await Promise.all(
    Array.from({ length: Math.min(options.concurrency, pending.length) }, (_, index) => worker(index + 1)),
  );
  if (fatalError) {
    throw new Error(
      `${fatalError.message}. Capture a fresh cURL and rerun the same command; completed ZIPs will be skipped.`,
    );
  }
  console.log(`Finished: ${downloaded} downloaded, ${failed} failed.`);
  if (failed > 0) {
    console.log("Rerun the same command to retry failures; successful ZIPs will be skipped.");
    process.exitCode = 1;
  }
}

main().catch((error) => {
  console.error(`Fatal: ${error.message}`);
  process.exitCode = 1;
});

