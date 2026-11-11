"""Detached OS-neutral MAI restart coordinator. Never kill processes by name matching."""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "MAI_MEMORY"
IDENTITY = DATA / "server-process.json"
LOG = DATA / "restart.log"
LOCK = DATA / "restart.lock"
PORTS = (8000, 8080, 8081)

def process_identity(pid: int) -> str | None:
    if sys.platform == "win32":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.c_void_p,) * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return None
        try:
            times = [ctypes.c_ulonglong() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise OSError(ctypes.get_last_error(), "GetProcessTimes failed")
            return str(times[0].value)
        finally:
            kernel.CloseHandle(handle)
    if sys.platform.startswith("linux"):
        try:
            data = Path(f"/proc/{pid}/stat").read_text()
        except FileNotFoundError:
            return None
        return data.rsplit(")", 1)[1].split()[19]
    raise RuntimeError(f"Unsupported OS: {sys.platform}")

def register_server() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    item = {"schema": 1, "pid": os.getpid(), "identity": process_identity(os.getpid()),
            "root": str(ROOT.resolve())}
    temp = IDENTITY.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(item), encoding="utf-8")
    temp.replace(IDENTITY)

def unregister_server() -> None:
    if IDENTITY.exists():
        item = json.loads(IDENTITY.read_text(encoding="utf-8"))
        if item.get("pid") == os.getpid() and item.get("identity") == process_identity(os.getpid()):
            IDENTITY.unlink()

def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex(("127.0.0.1", port)) == 0

def wait_ports(starting: bool, seconds: int) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        states = [listening(p) for p in PORTS]
        if (states[0] and states[1]) if starting else not any(states):
            return
        time.sleep(0.5)
    raise TimeoutError(f"Port readiness failure: {dict(zip(PORTS, states))}")

def stop_registered_server() -> None:
    if not IDENTITY.exists():
        if any(map(listening, PORTS)):
            raise RuntimeError("MAI running without process registration; manually restart once before automatic restart")
        return
    item = json.loads(IDENTITY.read_text(encoding="utf-8"))
    if item.get("schema") != 1 or item.get("root") != str(ROOT.resolve()):
        raise RuntimeError("Invalid server process registration")
    pid = item.get("pid")
    if not isinstance(pid, int) or pid < 1:
        raise RuntimeError("Invalid registered PID")
    identity = process_identity(pid)
    if identity is None:
        if any(map(listening, PORTS)):
            raise RuntimeError("Stale process registration with occupied ports")
        IDENTITY.unlink()
        return
    if identity != item.get("identity"):
        raise RuntimeError("PID reused; refusing to kill unrelated process")
    if not listening(8000):
        raise RuntimeError("Registered MAI server is not listening on port 8000")
    # Never kill a process tree: the detached coordinator must survive.
    os.kill(pid, signal.SIGTERM)
    wait_ports(False, 60)
    if IDENTITY.exists():
        current = json.loads(IDENTITY.read_text(encoding="utf-8"))
        if current != item:
            raise RuntimeError("Process registration changed during shutdown")
        IDENTITY.unlink()

def coordinate() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as f:
            f.write(str(os.getpid()))
        # Avoid pulling onto a non-dev branch, even when restarting an offline instance.
        branch = subprocess.check_output(["git", "-C", str(ROOT), "branch", "--show-current"], text=True).strip()
        if branch != "dev":
            raise RuntimeError(f"Only dev may be updated by restart (current: {branch!r})")
        stop_registered_server()
        subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only", "origin", "dev"], check=True)
        command = (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(ROOT / "mai.ps1"), "start"] if sys.platform == "win32"
                   else ["bash", str(ROOT / "mai.sh"), "start"])
        with LOG.open("ab", buffering=0) as output:
            output.write(b"\n--- Starting MAI ---\n")
            flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else 0
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                       creationflags=flags, start_new_session=sys.platform != "win32")
            output.write(f"Launcher pid={process.pid}\n".encode())
        wait_ports(True, 150)
        print("MAI restart successful", flush=True)
    finally:
        LOCK.unlink(missing_ok=True)

def detach() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    with LOG.open("ab", buffering=0) as output:
        output.write(b"\n--- Restart requested ---\n")
        flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else 0
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--coordinate"],
                                   cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                   stderr=subprocess.STDOUT, close_fds=True, creationflags=flags,
                                   start_new_session=sys.platform != "win32")
    print(f"Detached restart coordinator launched: PID {process.pid}. Log: {LOG}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--detach", action="store_true")
    mode.add_argument("--coordinate", action="store_true")
    args = parser.parse_args()
    if args.detach:
        detach()
    else:
        coordinate()
