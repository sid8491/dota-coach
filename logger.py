import logging
import os
import re
import sys
from datetime import datetime


def _sanitize(name):
    return re.sub(r"[^A-Za-z0-9_-]", "_", str(name))


class _LogFile:
    def __init__(self, path):
        self.path = path
        self.file = open(path, "a", encoding="utf-8", buffering=1)

    def write(self, data):
        try:
            self.file.write(data)
        except Exception:
            pass

    def flush(self):
        try:
            self.file.flush()
        except Exception:
            pass

    def close(self):
        try:
            self.file.close()
        except Exception:
            pass

    def swap(self, new_path, carry_forward=False):
        self.close()
        if (
            carry_forward
            and os.path.exists(self.path)
            and not os.path.exists(new_path)
        ):
            try:
                os.rename(self.path, new_path)
            except OSError:
                pass
        self.path = new_path
        self.file = open(new_path, "a", encoding="utf-8", buffering=1)


class _Tee:
    def __init__(self, console, log_file):
        self.console = console
        self.log_file = log_file

    def write(self, data):
        try:
            self.console.write(data)
        except Exception:
            pass
        self.log_file.write(data)

    def flush(self):
        try:
            self.console.flush()
        except Exception:
            pass
        self.log_file.flush()

    def isatty(self):
        return False


_state = {}


def setup_logging(log_dir="logs"):
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = os.path.join(log_dir, f"session_{stamp}.log")

    log_file = _LogFile(log_path)
    sys.stdout = _Tee(sys.__stdout__, log_file)
    sys.stderr = _Tee(sys.__stderr__, log_file)

    # Silence Werkzeug's per-request 200-OK access logs; keep warnings/errors.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _state["log_dir"] = log_dir
    _state["log_file"] = log_file

    print(f"--- log started {datetime.now().isoformat(timespec='seconds')} ---")
    print(f"--- writing to {log_path} ---")
    return log_path


def switch_to_match(matchid):
    if not matchid:
        return None
    log_file = _state.get("log_file")
    log_dir = _state.get("log_dir", "logs")
    if not log_file:
        return None
    new_path = os.path.join(log_dir, f"{_sanitize(matchid)}.log")
    if log_file.path == new_path:
        return new_path
    # Carry pre-game content forward only from a session_* log (first match).
    # Match-to-match transitions get a clean cut.
    is_session = os.path.basename(log_file.path).startswith("session_")
    print(f"--- switching log to {new_path} ---")
    log_file.swap(new_path, carry_forward=is_session)
    print(f"--- match {matchid} log continues ---")
    return new_path
