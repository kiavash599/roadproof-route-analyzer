const { app, BrowserWindow, dialog, shell } = require("electron");
const { autoUpdater } = require("electron-updater");
const { startLocalServer } = require("./server.cjs");

let mainWindow = null;
let localServer = null;

app.setAppUserModelId("com.kiavash599.roadproof");

if (!app.requestSingleInstanceLock()) {
  app.quit();
}

function isSafeExternalUrl(value) {
  try {
    return new URL(value).protocol === "https:";
  } catch {
    return false;
  }
}

function createMainWindow() {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 980,
    minHeight: 700,
    show: false,
    title: "RoadProof",
    backgroundColor: "#07111f",
    autoHideMenuBar: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      devTools: !app.isPackaged,
    },
  });

  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    if (isSafeExternalUrl(url)) void shell.openExternal(url);
    return { action: "deny" };
  });

  mainWindow.webContents.on("will-navigate", (event, url) => {
    if (url.startsWith(localServer.origin)) return;
    event.preventDefault();
    if (isSafeExternalUrl(url)) void shell.openExternal(url);
  });

  mainWindow.once("ready-to-show", () => mainWindow.show());
  mainWindow.on("closed", () => { mainWindow = null; });
  void mainWindow.loadURL(localServer.origin);
}

function enableUpdates() {
  if (!app.isPackaged || process.env.ROADPROOF_DISABLE_UPDATES === "1") return;

  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.allowPrerelease = false;
  autoUpdater.on("error", (error) => console.warn("RoadProof update check failed", error.message));
  autoUpdater.on("update-downloaded", async ({ version }) => {
    const choice = await dialog.showMessageBox(mainWindow, {
      type: "info",
      title: "RoadProof update ready",
      message: `RoadProof ${version} is ready to install.`,
      detail: "Restart now to install the update, or choose Later to install it when the app closes.",
      buttons: ["Restart and install", "Later"],
      defaultId: 0,
      cancelId: 1,
    });
    if (choice.response === 0) autoUpdater.quitAndInstall();
  });

  setTimeout(() => void autoUpdater.checkForUpdates(), 10_000);
}

app.on("second-instance", () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.focus();
});

app.whenReady().then(async () => {
  localServer = await startLocalServer(app.getAppPath());
  createMainWindow();
  enableUpdates();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createMainWindow();
  });
}).catch((error) => {
  console.error(error);
  dialog.showErrorBox("RoadProof could not start", error.message);
  app.quit();
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (localServer) void localServer.close();
});
