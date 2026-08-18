const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("robotworld", {
  isElectron: true,
  minimize: () => ipcRenderer.send("win:minimize"),
  toggleMaximize: () => ipcRenderer.send("win:toggle-maximize"),
  close: () => ipcRenderer.send("win:close"),
  isMaximized: () => ipcRenderer.invoke("win:is-maximized"),
  onMaximizedChange: (cb) => {
    const handler = (_e, v) => cb(v);
    ipcRenderer.on("win:maximized", handler);
    return () => ipcRenderer.removeListener("win:maximized", handler);
  },
});
