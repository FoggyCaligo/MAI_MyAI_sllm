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
import uuid

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "mai" / "mcp_tools" / "mcp_memorizer" / "MAI_MEMORY"
IDENTITY = DATA / "server-process.json"
LOG = DATA / "restart.log"
LAUNCH_LOG = DATA / "restart-launcher.log"
LOCK = DATA / "restart.lock"
PENDING = DATA / "restart.pending"
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

def launcher_creation_flags(platform: str) -> int:
    """Provide a hidden, usable Windows console to the nested PowerShell launcher."""
    if platform == "win32":
        return subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return 0


def wait_for_launcher(process: subprocess.Popen, seconds: int) -> None:
    """Distinguish a failed launcher from a hung startup."""
    deadline = time.monotonic() + seconds
    states = {}
    while time.monotonic() < deadline:
        result = process.poll()
        if result is not None:
            raise RuntimeError(
                f"MAI launcher exited before startup: pid={process.pid} "
                f"exit_code={result}; inspect {LAUNCH_LOG}"
            )
        states = {port: listening(port) for port in PORTS}
        if states[8000] and states[8080]:
            return
        time.sleep(0.5)
    raise TimeoutError(
        f"MAI launcher startup timed out: pid={process.pid}, "
        f"port_states={states}; inspect {LAUNCH_LOG}"
    )


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
        # The API has already exited. Do not repeatedly signal its PID, and
        # never kill unknown port owners. Once all ports are free the stale
        # registration can be safely cleared without touching a process.
        occupied = [port for port in PORTS if listening(port)]
        if occupied:
            raise RuntimeError(
                f"MAI API port 8000 is down but companion ports remain occupied: {occupied}; "
                "inspect the owning processes before stopping them"
            )
        IDENTITY.unlink()
        return
    # Never kill a process tree: the detached coordinator must survive.
    os.kill(pid, signal.SIGTERM)
    wait_ports(False, 60)
    if IDENTITY.exists():
        current = json.loads(IDENTITY.read_text(encoding="utf-8"))
        if current != item:
            raise RuntimeError("Process registration changed during shutdown")
        IDENTITY.unlink()

def stop() -> None:
    """Stop only the registered MAI instance, rejecting concurrent restarts."""
    DATA.mkdir(parents=True, exist_ok=True)
    if PENDING.exists():
        raise RuntimeError(f"MAI restart is pending; refusing concurrent stop: {PENDING}")
    descriptor = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w") as lock_file:
            lock_file.write(str(os.getpid()))
        if PENDING.exists():
            raise RuntimeError(f"MAI restart became pending; refusing concurrent stop: {PENDING}")
        stop_registered_server()
        print("MAI stopped; startup ports are no longer listening.", flush=True)
    finally:
        LOCK.unlink(missing_ok=True)


def wait_for_handoff(request_id: str) -> None:
    signal_path = DATA / f"restart-handoff-{request_id}.json"
    deadline = time.monotonic() + 1800
    while time.monotonic() < deadline:
        if signal_path.exists():
            data = json.loads(signal_path.read_text(encoding="utf-8"))
            if data != {"request_id": request_id, "status": "completed"}:
                raise RuntimeError(f"Restart handoff failed: {data}")
            signal_path.unlink()
            return
        time.sleep(0.25)
    raise TimeoutError(f"Restart handoff not completed: {request_id}")


def signal_handoff(request_id: str, *, completed: bool) -> None:
    uuid.UUID(request_id)
    DATA.mkdir(parents=True, exist_ok=True)
    target = DATA / f"restart-handoff-{request_id}.json"
    if target.exists():
        raise FileExistsError(target)
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps({
        "request_id": request_id,
        "status": "completed" if completed else "failed",
    }), encoding="utf-8")
    temporary.replace(target)


def preflight_update() -> str:
    """Verify dev can update safely *before* terminating the running service.

    Only fetching the remote ref happens here, which leaves the working tree
    and running Python files unchanged. Applying the fast-forward is deferred
    until MAI and its launcher have stopped.
    """
    subprocess.run(["git", "-C", str(ROOT), "fetch", "origin", "dev"], check=True)
    remote = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "FETCH_HEAD"], text=True,
    ).strip()
    local = subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True,
    ).strip()
    subprocess.run(
        ["git", "-C", str(ROOT), "merge-base", "--is-ancestor", local, remote],
        check=True,
    )
    # git diff only considers tracked files; include staged and unstaged changes.
    subprocess.run(["git", "-C", str(ROOT), "diff", "--quiet"], check=True)
    subprocess.run(["git", "-C", str(ROOT), "diff", "--cached", "--quiet"], check=True)

    if local != remote:
        # A newly tracked upstream path must not overwrite an untracked local file.
        incoming_paths = set(subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-tree", "-r", "--name-only", "-z", remote],
        ).decode("utf-8").split(chr(0)))
        local_untracked = set(subprocess.check_output(
            ["git", "-C", str(ROOT), "ls-files", "--others", "--exclude-standard", "-z"],
        ).decode("utf-8").split(chr(0)))
        collisions = sorted((incoming_paths & local_untracked) - {""})
        if collisions:
            raise RuntimeError(
                f"Untracked local files would be overwritten by dev: {collisions}"
            )
    return remote


def coordinate(*, update: bool, request_id: str | None = None) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(LOCK, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        print(f"Restart manager starting pid={os.getpid()} update={update}", flush=True)
        with os.fdopen(descriptor, "w") as f:
            f.write(str(os.getpid()))
        if request_id is not None:
            wait_for_handoff(request_id)
        # Avoid pulling onto a non-dev branch, even when restarting an offline instance.
        branch = subprocess.check_output(["git", "-C", str(ROOT), "branch", "--show-current"], text=True).strip()
        if branch != "dev":
            raise RuntimeError(f"Only dev may be updated by restart (current: {branch!r})")
        target_revision = preflight_update() if update else None
        print(f"Restart preflight complete target={target_revision or 'no-update'}", flush=True)
        stop_registered_server()
        if target_revision is not None:
            subprocess.run(
                ["git", "-C", str(ROOT), "merge", "--ff-only", target_revision],
                check=True,
            )
        command = (["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(ROOT / "mai.ps1"), "start"] if sys.platform == "win32"
                   else ["bash", str(ROOT / "mai.sh"), "start"])
        print(f"Launching MAI with {command[0]}; launcher_log={LAUNCH_LOG}", flush=True)
        with LAUNCH_LOG.open("ab", buffering=0) as output:
            output.write(b"\n--- New MAI launcher ---\n")
            # The PowerShell startup script launches llama.cpp with -NoNewWindow.
            # DETACHED_PROCESS leaves that console contract unavailable and can
            # make PowerShell exit 0 before either service is started.
            # CREATE_NO_WINDOW retains a usable hidden console for child processes.
            flags = launcher_creation_flags(sys.platform)
            process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                       stdout=output, stderr=subprocess.STDOUT, close_fds=True,
                                       creationflags=flags, start_new_session=sys.platform != "win32")
        print(f"New MAI launcher pid={process.pid}", flush=True)
        wait_for_launcher(process, 150)
        print("MAI startup ports ready (not an application health check)", flush=True)
    finally:
        LOCK.unlink(missing_ok=True)
        PENDING.unlink(missing_ok=True)

def detach(*, update: bool, await_handoff: bool = False) -> dict[str, object]:
    DATA.mkdir(parents=True, exist_ok=True)
    request_id = str(uuid.uuid4()) if await_handoff else None
    fd = os.open(PENDING, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as pending:
            pending.write(json.dumps({"request_id": request_id, "update": update}))
        with LOG.open("ab", buffering=0) as output:
            output.write(b"\n--- Restart requested ---\n")
            flags = (subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS) if sys.platform == "win32" else 0
            process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--coordinate", "--update" if update else "--no-update", *(["--request-id", request_id] if request_id else [])],
                                       cwd=ROOT, stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, close_fds=True, creationflags=flags,
                                       start_new_session=sys.platform != "win32")
    except BaseException:
        PENDING.unlink(missing_ok=True)
        raise
    return {"status": "accepted", "request_id": request_id, "coordinator_pid": process.pid, "update": update, "log_path": str(LOG), "result_confirmed": False}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--detach", action="store_true")
    mode.add_argument("--coordinate", action="store_true")
    mode.add_argument("--stop", action="store_true")
    options = parser.add_mutually_exclusive_group()
    options.add_argument("--update", action="store_true")
    options.add_argument("--no-update", action="store_true")
    parser.add_argument("--request-id", type=str, default=None)
    args = parser.parse_args()
    if args.stop:
        if args.update or args.no_update or args.request_id is not None:
            parser.error("--stop does not accept restart options")
        stop()
    else:
        if args.update == args.no_update:
            parser.error("restart requires exactly one of --update or --no-update")
        if args.detach:
            print(json.dumps(detach(update=args.update)))
        else:
            coordinate(update=args.update, request_id=args.request_id)
