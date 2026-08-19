/** RobotWorld Electron host: owns the loopback API and loads one trusted origin. */
const { app, BrowserWindow, dialog, ipcMain, session, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

const API_ORIGIN = "http://127.0.0.1:8000";
const DEV_URL = process.env.VITE_DEV_SERVER_URL || "http://127.0.0.1:5173/";
const isDev = !app.isPackaged;
let win = null;
let backend = null;
let backendLog = "";

function appendBackendLog(chunk) {
  backendLog = `${backendLog}${String(chunk)}`.slice(-8000);
}

async function probeApi() {
  try {
    const response = await fetch(`${API_ORIGIN}/health`, { signal: AbortSignal.timeout(1200) });
    if (!response.ok) return false;
    const body = await response.json();
    return body && typeof body.version === "string" && body.database;
  } catch {
    return false;
  }
}

function backendCommand() {
  if (isDev) {
    const root = path.resolve(__dirname, "..", "..");
    return {
      executable: path.join(root, "backend", ".venv", "Scripts", "python.exe"),
      args: ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
      cwd: path.join(root, "backend"),
      env: process.env,
    };
  }
  return {
    executable: path.join(process.resourcesPath, "backend", "robotworld-api.exe"),
    args: [],
    cwd: path.join(process.resourcesPath, "backend"),
    env: {
      ...process.env,
      ROBOTWORLD_DATA_DIR: path.join(app.getPath("userData"), "data"),
      ROBOTWORLD_FRONTEND_DIR: path.join(process.resourcesPath, "frontend"),
    },
  };
}

async function ensureBackend() {
  if (await probeApi()) return;
  const command = backendCommand();
  backend = spawn(command.executable, command.args, {
    cwd: command.cwd,
    env: command.env,
    windowsHide: true,
    stdio: ["ignore", "pipe", "pipe"],
  });
  backend.stdout.on("data", appendBackendLog);
  backend.stderr.on("data", appendBackendLog);
  backend.on("exit", (code) => appendBackendLog(`\nBackend exited with code ${code}.`));

  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    if (await probeApi()) return;
    if (backend.exitCode !== null) break;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error(`RobotWorld API did not become healthy.\n\n${backendLog || "No backend output was captured."}`);
}

function createWindow() {
  win = new BrowserWindow({
    width: 1560,
    height: 980,
    minWidth: 1180,
    minHeight: 700,
    backgroundColor: "#181818",
    frame: false,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.cjs"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  win.webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  win.webContents.on("will-navigate", (event, url) => {
    const allowed = isDev ? url.startsWith(DEV_URL) : url.startsWith(API_ORIGIN);
    if (!allowed) event.preventDefault();
  });
  win.once("ready-to-show", () => win?.show());
  win.on("maximize", () => win?.webContents.send("win:maximized", true));
  win.on("unmaximize", () => win?.webContents.send("win:maximized", false));
  win.loadURL(isDev ? DEV_URL : API_ORIGIN);
}

ipcMain.on("win:minimize", () => win?.minimize());
ipcMain.on("win:toggle-maximize", () => {
  if (!win) return;
  if (win.isMaximized()) win.unmaximize();
  else win.maximize();
});
ipcMain.on("win:close", () => win?.close());
ipcMain.handle("win:is-maximized", () => win?.isMaximized() ?? false);
ipcMain.handle("shell:open-external", async (_event, value) => {
  const url = new URL(String(value));
  if (url.protocol !== "http:" && url.protocol !== "https:") throw new Error("Only HTTP(S) links can be opened.");
  await shell.openExternal(url.toString());
  return true;
});

app.whenReady().then(async () => {
  session.defaultSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  try {
    await ensureBackend();
    createWindow();
  } catch (error) {
    dialog.showErrorBox("RobotWorld could not start", error instanceof Error ? error.message : String(error));
    app.quit();
  }
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("before-quit", () => {
  if (backend && backend.exitCode === null) backend.kill();
});
app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});
