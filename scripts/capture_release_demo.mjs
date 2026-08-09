#!/usr/bin/env node

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import process from "node:process";
import { fileURLToPath } from "node:url";

const scriptDirectory = path.dirname(fileURLToPath(import.meta.url));
const projectRoot = path.resolve(scriptDirectory, "..");
const width = 1600;
const height = 900;
const outputPath = path.resolve(projectRoot, process.argv[2] || "artifacts/motorlab-x-release.mp4");
const posterPath = outputPath.replace(/\.mp4$/i, "-poster.png");
const temporaryDirectory = fs.mkdtempSync(path.join(os.tmpdir(), "motorlab-release-"));
const chromeProfile = path.join(temporaryDirectory, "chrome-profile");
const framesDirectory = path.join(temporaryDirectory, "frames");
fs.mkdirSync(framesDirectory, { recursive: true });
fs.mkdirSync(path.dirname(outputPath), { recursive: true });

let serverProcess;
let chromeProcess;
let cdp;
let shuttingDown = false;

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function openPort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.unref();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => resolve(address.port));
    });
  });
}

async function waitForHttp(url, timeout = 12_000) {
  const deadline = Date.now() + timeout;
  let lastError;
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url);
      if (response.ok) return response;
    } catch (error) {
      lastError = error;
    }
    await sleep(120);
  }
  throw new Error(`Timed out waiting for ${url}: ${lastError || "no response"}`);
}

function launchServer(port) {
  const virtualPython = path.join(projectRoot, ".venv", "bin", "python");
  const python = fs.existsSync(virtualPython) ? virtualPython : "python3";
  const child = spawn(
    python,
    ["run.py", "--simulate", "--no-browser", "--port", String(port)],
    { cwd: projectRoot, stdio: ["ignore", "pipe", "pipe"] }
  );
  child.stderr.on("data", (chunk) => process.stderr.write(chunk));
  child.on("exit", (code) => {
    if (!shuttingDown && code !== null) console.error(`Simulation server exited with code ${code}`);
  });
  return child;
}

function launchChrome(debugPort) {
  const chrome = process.env.MOTORLAB_CHROME || "google-chrome";
  const args = [
    "--headless=new",
    "--no-sandbox",
    "--hide-scrollbars",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-renderer-backgrounding",
    "--force-device-scale-factor=1",
    `--window-size=${width},${height}`,
    `--remote-debugging-port=${debugPort}`,
    `--user-data-dir=${chromeProfile}`,
    "about:blank"
  ];
  const child = spawn(chrome, args, { stdio: ["ignore", "ignore", "pipe"] });
  child.stderr.on("data", (chunk) => {
    const message = String(chunk);
    if (/ERROR:|FATAL:/.test(message) && !/dbus/i.test(message)) process.stderr.write(message);
  });
  child.on("exit", (code) => {
    if (!shuttingDown && code !== null) console.error(`Chrome exited with code ${code}`);
  });
  return child;
}

class DevToolsClient {
  constructor(webSocketUrl) {
    this.webSocket = new WebSocket(webSocketUrl);
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.webSocket.addEventListener("open", resolve, { once: true });
      this.webSocket.addEventListener("error", reject, { once: true });
    });
    this.webSocket.addEventListener("message", (event) => this.handleMessage(String(event.data)));
  }

  handleMessage(rawMessage) {
    const message = JSON.parse(rawMessage);
    if (message.id) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(`${pending.method}: ${message.error.message}`));
      else pending.resolve(message.result || {});
      return;
    }
    for (const listener of this.listeners.get(message.method) || []) listener(message.params || {});
  }

  async call(method, params = {}) {
    await this.ready;
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      this.webSocket.send(JSON.stringify({ id, method, params }));
    });
  }

  send(method, params = {}) {
    const id = this.nextId++;
    this.webSocket.send(JSON.stringify({ id, method, params }));
  }

  on(method, listener) {
    const listeners = this.listeners.get(method) || [];
    listeners.push(listener);
    this.listeners.set(method, listeners);
  }

  close() {
    this.webSocket.close();
  }
}

async function evaluate(expression) {
  const response = await cdp.call("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: false
  });
  if (response.exceptionDetails) throw new Error(response.exceptionDetails.text || "Browser evaluation failed");
  return response.result?.value;
}

async function waitForExpression(expression, timeout = 30_000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await evaluate(expression)) return;
    await sleep(120);
  }
  throw new Error(`Timed out waiting for browser expression: ${expression}`);
}

function encodeVideo(frames) {
  if (frames.length < 2) throw new Error(`Only captured ${frames.length} frame(s)`);
  const concatPath = path.join(temporaryDirectory, "frames.txt");
  const lines = [];
  for (let index = 0; index < frames.length - 1; index += 1) {
    const duration = Math.max(1 / 120, Math.min(.25, frames[index + 1].timestamp - frames[index].timestamp));
    lines.push(`file '${frames[index].file.replaceAll("'", "'\\''")}'`);
    lines.push(`duration ${duration.toFixed(6)}`);
  }
  lines.push(`file '${frames.at(-1).file.replaceAll("'", "'\\''")}'`);
  lines.push(`duration ${(1 / 30).toFixed(6)}`);
  lines.push(`file '${frames.at(-1).file.replaceAll("'", "'\\''")}'`);
  fs.writeFileSync(concatPath, `${lines.join("\n")}\n`);

  const result = spawnSync(
    "ffmpeg",
    [
      "-hide_banner", "-loglevel", "warning", "-y",
      "-f", "concat", "-safe", "0", "-i", concatPath,
      "-vf", `fps=30,scale=${width}:${height}:flags=lanczos,format=yuv420p`,
      "-c:v", "libx264", "-preset", "slow", "-crf", "18",
      "-movflags", "+faststart", "-an", outputPath
    ],
    { cwd: projectRoot, encoding: "utf8" }
  );
  if (result.status !== 0) throw new Error(`ffmpeg failed:\n${result.stderr}`);

  const posterResult = spawnSync(
    "ffmpeg",
    ["-hide_banner", "-loglevel", "warning", "-y", "-sseof", "-1.2", "-i", outputPath, "-frames:v", "1", posterPath],
    { cwd: projectRoot, encoding: "utf8" }
  );
  if (posterResult.status !== 0) throw new Error(`Poster extraction failed:\n${posterResult.stderr}`);
}

async function cleanup() {
  if (shuttingDown) return;
  shuttingDown = true;
  if (cdp) cdp.close();
  if (chromeProcess && chromeProcess.exitCode === null) chromeProcess.kill("SIGTERM");
  if (serverProcess && serverProcess.exitCode === null) serverProcess.kill("SIGTERM");
  await sleep(200);
  fs.rmSync(temporaryDirectory, { recursive: true, force: true });
}

async function main() {
  const [serverPort, debugPort] = await Promise.all([openPort(), openPort()]);
  serverProcess = launchServer(serverPort);
  await waitForHttp(`http://127.0.0.1:${serverPort}/api/bootstrap`);

  chromeProcess = launchChrome(debugPort);
  await waitForHttp(`http://127.0.0.1:${debugPort}/json/version`);
  const targets = await (await waitForHttp(`http://127.0.0.1:${debugPort}/json/list`)).json();
  const pageTarget = targets.find((target) => target.type === "page");
  if (!pageTarget?.webSocketDebuggerUrl) throw new Error("Chrome did not expose a page target");

  cdp = new DevToolsClient(pageTarget.webSocketDebuggerUrl);
  await cdp.ready;
  await cdp.call("Page.enable");
  await cdp.call("Runtime.enable");
  await cdp.call("Emulation.setDeviceMetricsOverride", {
    width, height, deviceScaleFactor: 1, mobile: false, screenWidth: width, screenHeight: height
  });

  const browserErrors = [];
  cdp.on("Runtime.exceptionThrown", ({ exceptionDetails }) => {
    browserErrors.push(exceptionDetails?.exception?.description || exceptionDetails?.text || "Unknown browser exception");
  });

  const demoUrl = `http://127.0.0.1:${serverPort}/?release-demo=1&autoplay=0#bus`;
  await cdp.call("Page.navigate", { url: demoUrl });
  await waitForExpression("document.readyState === 'complete' && window.__releaseDemoReady === true", 15_000);

  const frames = [];
  let frameNumber = 0;
  let recording = true;
  cdp.on("Page.screencastFrame", ({ data, metadata, sessionId }) => {
    cdp.send("Page.screencastFrameAck", { sessionId });
    if (!recording) return;
    const file = path.join(framesDirectory, `frame-${String(frameNumber).padStart(6, "0")}.jpg`);
    fs.writeFileSync(file, Buffer.from(data, "base64"));
    const fallbackTimestamp = frameNumber / 60;
    frames.push({ file, timestamp: Number(metadata?.timestamp ?? fallbackTimestamp) });
    frameNumber += 1;
  });

  await cdp.call("Page.startScreencast", {
    format: "jpeg", quality: 94, maxWidth: width, maxHeight: height, everyNthFrame: 1
  });
  await evaluate("window.startReleaseDemo(); true");
  await waitForExpression("window.__releaseDemoDone === true", 35_000);
  await sleep(450);
  recording = false;
  await cdp.call("Page.stopScreencast");

  const demoError = await evaluate("window.__releaseDemoError || ''");
  if (demoError) throw new Error(`Release demo failed: ${demoError}`);
  if (browserErrors.length) throw new Error(`Browser exception:\n${browserErrors.join("\n")}`);

  encodeVideo(frames);
  const stats = fs.statSync(outputPath);
  console.log(`Captured ${frames.length} browser frames.`);
  console.log(`Video:  ${outputPath} (${(stats.size / 1024 / 1024).toFixed(1)} MiB)`);
  console.log(`Poster: ${posterPath}`);
}

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, async () => {
    await cleanup();
    process.exit(128);
  });
}

try {
  await main();
} finally {
  await cleanup();
}
