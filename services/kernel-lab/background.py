from __future__ import annotations

import argparse
import hashlib
import multiprocessing
import os
import signal
import threading
import time
from pathlib import Path


def burn() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    value = b"ecoroute-background-load"
    while True:
        value = hashlib.sha256(value).digest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    parser.add_argument(
        "--pid-file",
        type=Path,
        default=Path("/run/ecoroute-kernel-lab/background.pids"),
    )
    arguments = parser.parse_args()
    stop = threading.Event()
    workers = [multiprocessing.Process(target=burn) for _ in range(arguments.workers)]
    for worker in workers:
        worker.start()
    arguments.pid_file.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    arguments.pid_file.write_text(
        ",".join(str(pid) for pid in [os.getpid(), *(worker.pid for worker in workers if worker.pid)])
    )

    def shutdown(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        while not stop.is_set():
            time.sleep(0.5)
    finally:
        stop.set()
        for worker in workers:
            if worker.is_alive():
                worker.terminate()
            worker.join(timeout=5)
        arguments.pid_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
