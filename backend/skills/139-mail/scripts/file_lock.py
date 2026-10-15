#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cross-platform file lock + atomic JSON write helper.

Uses only the standard library so it works on Windows, Linux, and macOS
without extra dependencies:
- mutual exclusion via atomic ``os.open(path, O_CREAT | O_EXCL)`` lock file
- atomic persistence via temp-file + ``os.replace()``
"""

import getpass
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager


_THREAD_LOCK = threading.RLock()


@contextmanager
def file_lock(lock_path, timeout_seconds=10.0, stale_seconds=10.0, poll_interval=0.05):
    """Hold an inter-process lock guards a shared file.

    The lock file lives next to the guarded file so both stay on the same
    filesystem. Stale locks (crashed holders) are reclaimed by mtime.
    Raises TimeoutError when the lock cannot be acquired in time.
    """
    lock_dir = os.path.dirname(os.path.abspath(lock_path))
    if lock_dir and not os.path.exists(lock_dir):
        os.makedirs(lock_dir, exist_ok=True)
    deadline = time.time() + max(timeout_seconds, 0.1)
    with _THREAD_LOCK:
        while True:
            try:
                fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                try:
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        json.dump({"pid": os.getpid(), "createdAt": time.time()}, handle)
                except OSError:
                    pass
                break
            except FileExistsError:
                try:
                    if time.time() - os.path.getmtime(lock_path) >= stale_seconds:
                        try:
                            os.remove(lock_path)
                        except OSError:
                            pass
                        continue
                except OSError:
                    pass
                if time.time() >= deadline:
                    raise TimeoutError(f"file lock timeout: {lock_path}")
                time.sleep(poll_interval)
        try:
            yield
        finally:
            try:
                os.remove(lock_path)
            except OSError:
                pass


def atomic_write_json(path, data):
    """Write JSON atomically via temp file + os.replace()."""
    target_dir = os.path.dirname(os.path.abspath(path))
    if target_dir and not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=target_dir, prefix=".tmp-", suffix=f"-{os.getpid()}.json"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def atomic_write_text(path, text):
    """Write plain text atomically via temp file + os.replace()."""
    target_dir = os.path.dirname(os.path.abspath(path))
    if target_dir and not os.path.exists(target_dir):
        os.makedirs(target_dir, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=target_dir, prefix=".tmp-", suffix=f"-{os.getpid()}.txt"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_path, path)
    except BaseException:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise


def _harden_private_file_windows(path):
    '''Remove inheritance on Windows so only the current user keeps access.

    Best effort: never raises, returns True on success.
    '''
    try:
        try:
            user = getpass.getuser()
        except Exception:
            user = os.environ.get('USERNAME', '')
        user = str(user or '').strip()
        if not user:
            return False
        completed = subprocess.run(
            ['icacls', path, '/inheritance:r', '/grant:r', user + ':F'],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or 'unknown error').strip()
            print('[WARNING] icacls failed for private file: ' + detail, file=sys.stderr)
            return False
        return True
    except Exception as exc:
        print('[WARNING] failed to harden private file on Windows: ' + str(exc), file=sys.stderr)
        return False


def harden_private_file(path):
    '''Restrict a private file so only the current user can read it.

    POSIX: chmod 0600. Windows: strip inheritance via icacls and grant
    the current user full control. Other platforms: warn and skip.
    Best effort: never raises, returns True on success and False otherwise,
    so permission hardening can never block an already-authorized user.
    '''
    try:
        if os.name == 'posix':
            os.chmod(path, 0o600)
            return True
        if os.name == 'nt':
            return _harden_private_file_windows(path)
        print('[WARNING] unsupported platform for file hardening: ' + os.name, file=sys.stderr)
        return False
    except Exception as exc:
        print('[WARNING] failed to harden private file: ' + str(exc), file=sys.stderr)
        return False
