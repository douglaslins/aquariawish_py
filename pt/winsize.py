"""Resizing the frameless window from the edge handles of the page.

A frameless WinForms window has no edges the system can grab, so the page draws eight handles (js/window.js) and this module moves the
edges. On Windows the page makes ONE call when the button goes down (WindowApi.windowStartResize) and a thread here follows the cursor
with the Win32 functions until the button is released. No message goes between the page and Python while the mouse moves: a message through
the WebView2 bridge costs more than the resize itself, and one per mouse move is what makes a resize through the page feel sluggish.
SetWindowPos is called from the thread and returns when the window has taken the new size, so a new size is computed from the newest cursor
position after each one (sizes the window could not keep up with are skipped, not queued).

Elsewhere (other systems, or when the Win32 route fails) windowStartResize answers {"native": false} and the page does the same job
itself, one windowSetRect call per drawn frame (js/window.js).
"""
from __future__ import annotations

import logging
import sys
import threading
import time

log = logging.getLogger("pt.winsize")

EDGES = ("n", "s", "e", "w", "ne", "nw", "se", "sw")
MIN_SIZE = (960, 720)           # smallest window in CSS pixels (width, height); the interface is checked down to this size
POLL = 0.004                    # seconds between cursor reads
MAX_SECONDS = 120.0             # a resize that is still "held" after this long is ended (the button state could not be read)

SWP_NOZORDER, SWP_NOACTIVATE, SWP_NOOWNERZORDER = 0x0004, 0x0010, 0x0200
VK_LBUTTON, VK_RBUTTON, SM_SWAPBUTTON = 0x01, 0x02, 23

Rect = tuple[int, int, int, int]        # left, top, right, bottom


def new_rect(edge: str, rect: Rect, dx: int, dy: int, min_size: tuple[int, int]) -> Rect:
    """The window rectangle when the handle `edge` has been dragged by (dx, dy) from `rect`. The opposite edges stay where they are.
    A window that is already smaller than min_size can still be resized (it is not forced to grow)."""
    left, top, right, bottom = rect
    min_w, min_h = min(min_size[0], right - left), min(min_size[1], bottom - top)
    if "w" in edge:
        left = min(left + dx, right - min_w)
    elif "e" in edge:
        right = max(right + dx, left + min_w)
    if "n" in edge:
        top = min(top + dy, bottom - min_h)
    elif "s" in edge:
        bottom = max(bottom + dy, top + min_h)
    return left, top, right, bottom


def follow(native, hwnd, edge: str, min_size: tuple[int, int], poll: float = POLL, max_seconds: float = MAX_SECONDS,
           sleep=time.sleep, clock=time.monotonic) -> int:
    """Resize the window while the mouse button is down. Returns how many times the window was placed."""
    start = native.cursor()
    first = native.rect(hwnd)
    scale = native.scale(hwnd)
    least = (round(min_size[0] * scale), round(min_size[1] * scale))
    last, placed, end = first, 0, clock() + max_seconds
    while native.button_down() and clock() < end:
        try:
            x, y = native.cursor()
        except OSError:         # the desktop was locked or switched (a UAC box, Win+L): the resize ends
            break
        rect = new_rect(edge, first, x - start[0], y - start[1], least)
        if rect != last:
            native.place(hwnd, rect)
            last, placed = rect, placed + 1
        sleep(poll)
    return placed


class Win32:
    """The few Win32 calls the resize needs (user32 through ctypes). Coordinates are the ones of GetCursorPos / GetWindowRect, so the
    same units whether the process is DPI aware or not."""

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes
        self._ct, self._wt = ctypes, wintypes
        # A library of our own, NOT ctypes.windll.user32: that one is shared by the whole process, and the functions below get argtypes here.
        # pywebview moves the window with windll.user32.SetWindowPos(hwnd, None, x, y, None, None, flags), None for the sizes; with the
        # integer argtypes set on the shared function that call raises "TypeError: 'NoneType' object cannot be interpreted as an integer",
        # and after the first resize the window could not be dragged any more.
        self.u = ctypes.WinDLL("user32")
        self.u.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self.u.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        self.u.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._dpi = getattr(self.u, "GetDpiForWindow", None)       # missing before Windows 10 1607: the scale is then 1
        if self._dpi is not None:
            self._dpi.argtypes = [wintypes.HWND]

    def cursor(self) -> tuple[int, int]:
        p = self._wt.POINT()
        if not self.u.GetCursorPos(self._ct.byref(p)):      # fails on a locked desktop; (0, 0) would make the window jump
            raise OSError("GetCursorPos failed")
        return p.x, p.y

    def rect(self, hwnd) -> Rect:
        r = self._wt.RECT()
        if not self.u.GetWindowRect(hwnd, self._ct.byref(r)):       # the window is gone
            raise OSError("GetWindowRect failed")
        return r.left, r.top, r.right, r.bottom

    def scale(self, hwnd) -> float:
        if self._dpi is None:
            return 1.0
        return (self._dpi(hwnd) or 96) / 96

    def button_down(self) -> bool:
        key = VK_RBUTTON if self.u.GetSystemMetrics(SM_SWAPBUTTON) else VK_LBUTTON      # GetAsyncKeyState reads the physical buttons
        return bool(self.u.GetAsyncKeyState(key) & 0x8000)

    def place(self, hwnd, rect: Rect) -> None:
        left, top, right, bottom = rect
        self.u.SetWindowPos(hwnd, None, left, top, right - left, bottom - top, SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER)


_busy = threading.Lock()


def start(hwnd, edge: str, min_size: tuple[int, int] = MIN_SIZE, native=None) -> bool:
    """Start following the mouse for a resize from `edge`. False when this system cannot do it (the page then resizes by itself)."""
    if edge not in EDGES or not hwnd:
        return False
    if native is None:
        if sys.platform != "win32":
            return False
        try:
            native = Win32()
        except Exception:
            log.exception("The Win32 resize is not available")
            return False
    if not _busy.acquire(blocking=False):       # a resize is already running
        return True

    def run() -> None:
        try:
            follow(native, hwnd, edge, min_size)
        except OSError as e:
            log.info("Resize ended: %s", e)
        except Exception:
            log.exception("Resize failed")
        finally:
            _busy.release()

    try:
        threading.Thread(target=run, name="pt-resize", daemon=True).start()
    except RuntimeError:        # no thread could be started: without this the lock would stay taken and no resize would ever start again
        _busy.release()
        log.exception("The resize thread did not start")
        return False
    return True
