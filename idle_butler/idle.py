"""Cross-platform idle-time detection."""

import ctypes
import platform
import re
import shutil
import subprocess

from .state import ButlerError

NANOSECONDS_PER_SECOND = 1_000_000_000
MILLISECONDS_PER_SECOND = 1_000
COMMAND_TIMEOUT_SECONDS = 5


def parse_macos_idle_seconds(output: str) -> float:
    """Parse HIDIdleTime nanoseconds from ioreg output."""
    match = re.search(r'HIDIdleTime"\s*=\s*(\d+)', output)
    if not match:
        raise ButlerError("macOS did not report HIDIdleTime.")
    return int(match.group(1)) / NANOSECONDS_PER_SECOND


def parse_linux_idle_seconds(output: str) -> float:
    """Parse xprintidle milliseconds into seconds."""
    return int(output.strip()) / MILLISECONDS_PER_SECOND


def get_idle_seconds() -> float:
    """Return seconds since the last user input event."""
    system = platform.system()
    if system == "Windows":
        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LastInputInfo()
        info.cbSize = ctypes.sizeof(LastInputInfo)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            raise ButlerError("Windows could not read the last input time.")
        kernel32 = ctypes.windll.kernel32
        tick = getattr(kernel32, "GetTickCount64", kernel32.GetTickCount)
        return max(0.0, (float(tick()) - info.dwTime) / MILLISECONDS_PER_SECOND)
    if system == "Darwin":
        try:
            result = subprocess.run(["ioreg", "-c", "IOHIDSystem"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ButlerError("macOS idle detection failed; use --now.") from exc
        return parse_macos_idle_seconds(result.stdout)
    if system == "Linux":
        if shutil.which("xprintidle") is None:
            raise ButlerError("Linux needs xprintidle for idle detection; install it or use --now (Wayland may not support it).")
        try:
            result = subprocess.run(["xprintidle"], capture_output=True, text=True, timeout=COMMAND_TIMEOUT_SECONDS, check=True)
            return parse_linux_idle_seconds(result.stdout)
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ButlerError("Linux idle detection failed; use --now or check xprintidle.") from exc
    raise ButlerError("Idle detection is unavailable on this operating system; use --now.")

