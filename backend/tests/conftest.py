"""Isolate integration tests from the user's persistent RobotWorld workspace."""
import os
import tempfile


os.environ["ROBOTWORLD_DATA_DIR"] = tempfile.mkdtemp(prefix="robotworld-tests-")
