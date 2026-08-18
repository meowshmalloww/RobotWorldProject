# RobotWorld client

The client is the React 19, TypeScript, Three.js, and Electron surface for RobotWorld. It contains no fixture data; every page reads the FastAPI contract under `/api`, and live evaluation uses `/ws/live/{sessionId}`.

See the [repository README](../README.md) for setup, verification, packaging, runtime architecture, and integration configuration.

Useful commands:

```powershell
npm ci
npm run dev:electron
npm run typecheck
npm run lint
npm run build
npm run dist
```
