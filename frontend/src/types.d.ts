export {};

declare global {
  interface Window {
    robotworld?: {
      isElectron: true;
      minimize: () => void;
      toggleMaximize: () => void;
      close: () => void;
      isMaximized: () => Promise<boolean>;
      openExternal: (url: string) => Promise<boolean>;
      onMaximizedChange: (cb: (v: boolean) => void) => () => void;
    };
  }
}
