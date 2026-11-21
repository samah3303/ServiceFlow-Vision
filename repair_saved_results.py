#!/usr/bin/env python3
"""Repair impossible same-frame identities in a saved local analysis.

Keeps a timestamped backup before changing a result. The optional watcher
repairs an already-running analysis after that job finishes.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import shutil
from urllib.request import urlopen

from server import ProjectStore, RESULTS_PATH, ROOT, RUNS_DIR, rebuilt_results, save_json_atomic


def repair_saved_results() -> int:
    if not RESULTS_PATH.exists():
        raise FileNotFoundError(RESULTS_PATH)
    with RESULTS_PATH.open("r", encoding="utf-8") as handle:
        previous = json.load(handle)
    original_run = previous.get("runId")
    # A dry check avoids rebuilding a clean result or creating unnecessary backups.
    from collections import Counter
    collisions = Counter((row["cameraId"], row["time"], row["globalId"])
                         for row in previous.get("observations", []))
    if not any(count > 1 for count in collisions.values()):
        print(f"No same-frame identity collisions in {original_run or 'current result'}", flush=True)
        return 0

    backup_stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    backup = RESULTS_PATH.with_name(f"results.before-frame-repair-{backup_stamp}.json")
    shutil.copy2(RESULTS_PATH, backup)
    run_path = RUNS_DIR / f"{original_run}.json" if original_run else None
    if run_path and run_path.exists():
        shutil.copy2(run_path, run_path.with_name(f"{original_run}.before-frame-repair-{backup_stamp}.json"))

    repaired = rebuilt_results(ProjectStore.load(), previous)
    remaining = Counter((row["cameraId"], row["time"], row["globalId"])
                        for row in repaired.get("observations", []))
    if any(count > 1 for count in remaining.values()):
        raise RuntimeError("Same-frame identity collision remained after repair; original result is untouched")
    if RESULTS_PATH.stat().st_mtime_ns != backup.stat().st_mtime_ns:
        raise RuntimeError("The result changed during repair; original result is untouched. Retry when analysis is idle")
    save_json_atomic(RESULTS_PATH, repaired)
    if run_path:
        save_json_atomic(run_path, repaired)
    count = repaired.get("identityConsistency", {}).get("sameFrameIdentitiesSplit", 0)
    print(f"Repaired {count} identity assignments in {original_run}; backup: {backup}", flush=True)
    return count


def wait_for_job(job_id: str, port: int) -> bool:
    deadline = time.monotonic() + 8 * 60 * 60
    url = f"http://127.0.0.1:{port}/api/jobs/{job_id}"
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=10) as response:
                job = json.load(response)
        except Exception as error:
            print(f"Could not check analysis job: {error}", flush=True)
            return False
        if job.get("status") == "complete":
            return True
        if job.get("status") == "error":
            print(f"Analysis failed: {job.get('message')}", flush=True)
            return False
        time.sleep(15)
    print("Analysis watcher timed out", flush=True)
    return False


def restart_server(pid: int, port: int) -> None:
    os.kill(pid, signal.SIGTERM)
    for _ in range(30):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                break
        time.sleep(.5)
    else:
        raise RuntimeError("Previous server did not release its port")
    log_path = ROOT / "data" / "serviceflow-server.log"
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen([sys.executable, str(ROOT / "server.py"), "--host", "127.0.0.1",
                          "--port", str(port)], cwd=ROOT, stdin=subprocess.DEVNULL,
                         stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    for _ in range(30):
        try:
            with urlopen(f"http://127.0.0.1:{port}/api/health", timeout=2):
                print(f"Revised server ready on port {port}", flush=True)
                return
        except Exception:
            time.sleep(.5)
    raise RuntimeError("Revised server did not become healthy; check the server log")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--watch-job", help="Wait for a running analysis before repairing its result")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--restart-pid", type=int, help="Restart the old server once a watched job is repaired")
    args = parser.parse_args()
    if args.watch_job and not wait_for_job(args.watch_job, args.port):
        return
    repair_saved_results()
    if args.restart_pid:
        restart_server(args.restart_pid, args.port)


if __name__ == "__main__":
    main()
