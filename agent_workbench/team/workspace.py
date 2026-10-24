"""Bounded working copies, file tools, and conflict-checked integration.

These file boundaries are not an OS sandbox. Executing commands always requires approval.
"""

import difflib
import functools
import hashlib
import http.server
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

EXCLUDED = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".cache",
    ".pytest_cache",
    ".ruff_cache",
    "var",
    "output",
    "dist",
    "build",
    ".aws",
    ".ssh",
    ".codex",
    ".agents",
}


def forbidden(part):
    part = part.casefold()
    return (
        part in EXCLUDED
        or (part.startswith(".env") and part != ".env.example")
        or part.endswith((".pem", ".key", ".p12", ".db", ".sqlite", ".sqlite3"))
    )


def safe_path(root, relative):
    root = Path(root).resolve()
    raw = Path(relative)
    if (
        raw.is_absolute()
        or ".." in raw.parts
        or any(forbidden(p) for p in raw.parts)
        or ":" in relative
    ):
        raise ValueError("路径不在可访问的工作副本范围")
    candidate = root / raw
    current = root
    for part in raw.parts:
        current = current / part
        if current.is_symlink() or (
            os.name == "nt" and current.exists() and current.stat().st_file_attributes & 1024
        ):
            raise ValueError("不允许跟随链接或目录联接")
    target = candidate.resolve()
    if not target.is_relative_to(root):
        raise ValueError("路径超出工作副本")
    return target


def file_map(root):
    root, result, size = Path(root), {}, 0
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = (
            sorted(
                d
                for d in dirs
                if not forbidden(d)
                and not Path(directory, d).is_symlink()
                and not os.path.islink(Path(directory, d))
                and not Path(directory, d).stat().st_file_attributes & 1024
            )
            if os.name == "nt"
            else sorted(d for d in dirs if not forbidden(d) and not Path(directory, d).is_symlink())
        )
        for name in sorted(names):
            path = Path(directory, name)
            if forbidden(name) or path.is_symlink():
                continue
            if path.stat().st_size > 1_000_000:
                continue
            raw = path.read_bytes()
            size += len(raw)
            if len(result) >= 4000 or size > 40_000_000:
                raise ValueError("工作区超过 4000 个文件或 40MB，请选择较小项目目录")
            result[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
    return result


class Workspace:
    def __init__(self, data_dir):
        self.data_dir = Path(data_dir).resolve()
        self.apply_lock = threading.Lock()

    def prepare(self, task, project, dependencies):
        folder = self.data_dir / "workspaces" / task["id"] / str(task["attempt"])
        root = Path(project["root"]).resolve()
        files = file_map(root)
        for sub in ("base", "work"):
            target = folder / sub
            target.mkdir(parents=True, exist_ok=False)
            for name in files:
                dest = safe_path(target, name)
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(safe_path(root, name), dest)
        # Completed dependencies carry immutable patches; merge only compatible changes.
        for dependency in dependencies:
            for change in dependency.get("changes", []):
                destination = safe_path(folder / "work", change["path"])
                actual = (
                    hashlib.sha256(destination.read_bytes()).hexdigest()
                    if destination.exists()
                    else None
                )
                if actual == change["after"]:
                    continue
                if actual != change["before"]:
                    raise ValueError("依赖成果发生冲突：" + change["path"])
                if change["after"] is None:
                    destination.unlink(missing_ok=True)
                else:
                    source = safe_path(Path(dependency["workspace"]) / "work", change["path"])
                    if hashlib.sha256(source.read_bytes()).hexdigest() != change["after"]:
                        raise ValueError("依赖产物已改变，请重新验证依赖任务")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, destination)
        return str(folder)

    def changes(self, workspace):
        folder = Path(workspace)
        before, after = file_map(folder / "base"), file_map(folder / "work")
        result = []
        for name in sorted(before.keys() | after.keys()):
            if before.get(name) == after.get(name):
                continue
            old = (folder / "base" / name).read_bytes() if name in before else b""
            new = (folder / "work" / name).read_bytes() if name in after else b""
            try:
                diff = "".join(
                    difflib.unified_diff(
                        old.decode("utf-8").splitlines(True),
                        new.decode("utf-8").splitlines(True),
                        fromfile="a/" + name,
                        tofile="b/" + name,
                    )
                )
            except UnicodeDecodeError:
                diff = "二进制文件变更"
            result.append(
                {
                    "path": name,
                    "before": before.get(name),
                    "after": after.get(name),
                    "diff": diff[:50000],
                }
            )
        return result

    def apply(self, task, project):
        with self.apply_lock:
            changes = self.changes(task["workspace"])
            if [(c["path"], c["before"], c["after"]) for c in changes] != [
                (c["path"], c["before"], c["after"]) for c in task.get("changes", [])
            ]:
                raise ValueError("工作副本在完成后发生变化，请重新执行或检查成果")
            root, backups, pending = Path(project["root"]), {}, []
            for change in changes:
                path = safe_path(root, change["path"])
                raw = path.read_bytes() if path.exists() else None
                actual = hashlib.sha256(raw).hexdigest() if raw is not None else None
                if actual == change["after"]:
                    continue
                if actual != change["before"]:
                    raise ValueError("原项目已被修改，拒绝覆盖：" + change["path"])
                backups[path] = raw
                pending.append((path, change))
            try:
                for path, change in pending:
                    if change["after"] is None:
                        path.unlink(missing_ok=True)
                    else:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        raw = safe_path(
                            Path(task["workspace"]) / "work", change["path"]
                        ).read_bytes()
                        self.atomic_write(path, raw)
            except Exception:
                for path, raw in backups.items():
                    if raw is None:
                        path.unlink(missing_ok=True)
                    else:
                        self.atomic_write(path, raw)
                raise
            return len(pending)

    @staticmethod
    def atomic_write(path, raw):
        fd, temp = tempfile.mkstemp(prefix=".aw-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(raw)
            os.replace(temp, path)
        finally:
            Path(temp).unlink(missing_ok=True)


def clean_env():
    names = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "HOME",
        "USERPROFILE",
        "COMSPEC",
        "PATHEXT",
        "LANG",
        "LOCALAPPDATA",
        "APPDATA",
    }
    return {k: v for k, v in os.environ.items() if k.upper() in names} | {
        "PYTHONIOENCODING": "utf-8",
        "GIT_TERMINAL_PROMPT": "0",
    }


def run_command(root, argv, timeout, cancelled=lambda: False):
    argv = list(argv)
    if argv[0] in {"python", "python3"}:
        argv[0] = sys.executable
    if not argv[0] or any("\x00" in part for part in argv):
        raise ValueError("无效命令")
    options = (
        {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    with tempfile.TemporaryFile() as output:
        proc = subprocess.Popen(
            argv,
            cwd=root,
            env=clean_env(),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            **options,
        )
        started, stopped = time.monotonic(), None
        try:
            while proc.poll() is None:
                if cancelled() or time.monotonic() - started > timeout:
                    stopped = "cancelled" if cancelled() else "timeout"
                    break
                if output.tell() > 2_000_000:
                    stopped = "output_limit"
                    break
                time.sleep(0.1)
        finally:
            # Stop descendants too: test servers must not survive a tool invocation.
            if os.name == "nt" and proc.poll() is None:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            elif os.name != "nt":
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            proc.wait(timeout=10)
        output.seek(0)
        raw = output.read(24000)
        return {
            "argv": argv,
            "exit_code": proc.returncode,
            "status": stopped or "exited",
            "output": raw.decode("utf-8", errors="replace"),
            "seconds": round(time.monotonic() - started, 2),
        }


def browser_check(root, path, actions, artifact_dir):
    from playwright.sync_api import sync_playwright

    safe_path(root, path)
    cached = Path.cwd() / ".cache/ms-playwright"
    if cached.is_dir():
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(cached.resolve()))

    class Handler(http.server.SimpleHTTPRequestHandler):
        extensions_map = {
            **http.server.SimpleHTTPRequestHandler.extensions_map,
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
        }

        def log_message(self, *args):
            pass

        def translate_path(self, url):
            from urllib.parse import unquote, urlsplit

            relative = unquote(urlsplit(url).path).lstrip("/")
            try:
                return str(safe_path(root, relative))
            except ValueError:
                return str(Path(root) / ".blocked-file")

    server = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Handler, directory=str(root))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)[:500]))
            page.goto(
                f"http://127.0.0.1:{server.server_port}/{path}",
                wait_until="networkidle",
                timeout=15000,
            )
            for action in actions:
                if set(action) - {"action", "role", "name", "value"} or action.get(
                    "action"
                ) not in {"click", "fill"}:
                    raise ValueError("浏览器动作仅支持 role/name 定位的 click 和 fill")
                locator = page.get_by_role(action["role"], name=action["name"], exact=True)
                if action["action"] == "click":
                    locator.click(timeout=5000)
                else:
                    locator.fill(action.get("value", ""), timeout=5000)
            page.screenshot(path=str(artifact_dir / "browser.png"), full_page=True)
            result = {
                "title": page.title(),
                "text": page.locator("body").inner_text()[:15000],
                "errors": errors,
                "screenshot": "browser.png",
            }
            browser.close()
            return result
    finally:
        server.shutdown()
        server.server_close()
