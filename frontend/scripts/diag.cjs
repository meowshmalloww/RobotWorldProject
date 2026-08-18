/* CDP diagnostic: open a page, evaluate WebGL canvas state, take a screenshot. */
const { spawn } = require("child_process");
const http = require("http");
const fs = require("fs");

const CHROME = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
const URL_ = process.argv[2] || "http://127.0.0.1:5173/#/worlds/live";
const OUT = process.argv[3] || "C:\\Users\\wenje\\AppData\\Local\\Temp\\opencode\\shots\\cdp.png";
const WAIT_MS = Number(process.argv[4] || 6000);

const chrome = spawn(CHROME, [
  "--headless=new",
  "--disable-gpu",
  "--remote-debugging-port=9223",
  "--window-size=1500,940",
  "--user-data-dir=C:\\Users\\wenje\\AppData\\Local\\Temp\\opencode\\chrome-profile",
  URL_,
], { stdio: "ignore" });

const get = (path) => new Promise((res, rej) => {
  http.get({ host: "127.0.0.1", port: 9223, path }, (r) => {
    let d = "";
    r.on("data", (c) => (d += c));
    r.on("end", () => res(d));
  }).on("error", rej);
});

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function main() {
  let targets = null;
  for (let i = 0; i < 30; i++) {
    await sleep(500);
    try {
      targets = JSON.parse(await get("/json"));
      if (targets.find((t) => t.type === "page")) break;
    } catch { /* retry */ }
  }
  const page = targets.find((t) => t.type === "page");
  const ws = new WebSocket(page.webSocketDebuggerUrl);
  let id = 0;
  const pending = new Map();
  const send = (method, params = {}) => new Promise((res, rej) => {
    const mid = ++id;
    pending.set(mid, { res, rej });
    ws.send(JSON.stringify({ id: mid, method, params }));
  });
  ws.onmessage = (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.id && pending.has(msg.id)) {
      pending.get(msg.id).res(msg);
      pending.delete(msg.id);
    }
  };
  await new Promise((r) => (ws.onopen = r));
  await send("Runtime.enable");
  await send("Page.enable");

  await sleep(WAIT_MS);

  const evalJs = async (expr) => {
    const r = await send("Runtime.evaluate", { expression: expr, returnByValue: true, awaitPromise: true });
    return r.result?.result?.value;
  };

  const report = await evalJs(`(() => {
    const cs = [...document.querySelectorAll("canvas")];
    return cs.map((c) => {
      let px = null;
      try {
        const g = c.getContext("webgl2", { preserveDrawingBuffer: true });
        // note: getContext returns the EXISTING context if compatible
        const buf = new Uint8Array(4);
        if (g) { g.readPixels(Math.floor(c.width/2), Math.floor(c.height/2), 1, 1, g.RGBA, g.UNSIGNED_BYTE, buf); px = [...buf]; }
      } catch (e) { px = "err:" + e.message; }
      return { w: c.width, h: c.height, px };
    });
  })()`);
  console.log("CANVAS REPORT:", JSON.stringify(report));

  const errors = await evalJs(`window.__errs ?? "none"`);
  console.log("PAGE ERRORS:", errors);

  const tb = await evalJs(`(() => {
    const el = [...document.querySelectorAll(".vp-overlay")].find((d) => d.style.top === "10px" && d.style.right === "10px");
    if (!el) return "no overlay";
    const t = el.querySelector(".vp-toolbar");
    const r = t.getBoundingClientRect();
    const cs = getComputedStyle(t);
    return { w: r.width, h: r.height, wrap: cs.flexWrap, display: cs.display, kids: t.children.length, overlayW: el.getBoundingClientRect().width };
  })()`);
  console.log("TOOLBAR:", JSON.stringify(tb));

  const shot = await send("Page.captureScreenshot", { format: "png" });
  if (shot.result?.data) {
    fs.writeFileSync(OUT, Buffer.from(shot.result.data, "base64"));
    console.log("SHOT SAVED:", OUT);
  }
  ws.close();
  chrome.kill();
  process.exit(0);
}

main().catch((e) => {
  console.error("DIAG FAILED", e);
  chrome.kill();
  process.exit(1);
});
