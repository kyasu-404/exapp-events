"""Supervise both HaRP's FRP client and FastAPI; Docker restarts on child failure."""

import os
import signal
import subprocess
import sys
import time


def supervise(commands):
    children = []
    stopping = False

    def stop(signum, frame):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    result = 0
    try:
        for command in commands:
            children.append(subprocess.Popen(command))
        while not stopping:
            if any(child.poll() is not None for child in children):
                result = 1
                break
            time.sleep(0.5)
    finally:
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 60
        for child in children:
            try:
                child.wait(timeout=max(0.1, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return result


if __name__ == "__main__":
    commands = [[sys.executable, "-m", "ex_app.lib.main"]]
    if os.environ.get("HP_SHARED_KEY"):
        subprocess.run([sys.executable, "/scripts/configure_frp.py"], check=True)
        commands.insert(0, ["frpc", "-c", "/tmp/frpc.toml"])
    sys.exit(supervise(commands))
