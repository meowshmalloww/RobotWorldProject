"""Frozen desktop entry point for the RobotWorld loopback API."""
import multiprocessing

from app.config import env
from app.main import app


if __name__ == "__main__":
    import uvicorn

    multiprocessing.freeze_support()
    uvicorn.run(app, host=env.host, port=env.port, reload=False, access_log=False)
