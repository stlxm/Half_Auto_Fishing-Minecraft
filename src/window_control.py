"""Windows game-window selection and foreground activation.

All input must be sent only after the selected HWND is truly foreground.
"""
import ctypes
import time
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.restype = ctypes.c_int
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL
user32.GetForegroundWindow.argtypes = []
user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.SetForegroundWindow.restype = wintypes.BOOL
user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
user32.AttachThreadInput.restype = wintypes.BOOL
user32.BringWindowToTop.argtypes = [wintypes.HWND]
user32.BringWindowToTop.restype = wintypes.BOOL
user32.keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]
kernel32.GetCurrentThreadId.argtypes = []
kernel32.GetCurrentThreadId.restype = wintypes.DWORD

SW_RESTORE = 9
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002


def window_title(hwnd):
    length = user32.GetWindowTextLengthW(hwnd)
    if not length:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def list_windows():
    """Return distinct visible titled top-level windows (hwnd, title)."""
    found = []
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            title = window_title(hwnd)
            if title.strip():
                found.append((int(hwnd), title))
        return True

    callback_func = callback_type(callback)  # hold a reference for EnumWindows
    user32.EnumWindows(callback_func, 0)
    return found


def default_minecraft_window(windows):
    matches = [hwnd for hwnd, title in windows
               if "minecraft" in title.casefold()
               and "launcher" not in title.casefold()]
    return matches[0] if len(matches) == 1 else None


def is_foreground(hwnd):
    return bool(hwnd and user32.IsWindow(hwnd)
                and user32.GetForegroundWindow() == hwnd)


def _request_foreground(hwnd):
    # Foreground activation is restricted by Windows. A harmless ALT key event
    # permits SetForegroundWindow in many desktop configurations.
    user32.keybd_event(VK_MENU, 0, 0, 0)
    user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
    user32.SetForegroundWindow(hwnd)


def activate_window(hwnd):
    """Attempt to foreground the selected window; return success and explanation."""
    if not hwnd or not user32.IsWindow(hwnd) or not window_title(hwnd):
        return False, "操作対象のウィンドウが閉じられています。再選択してください。"
    if is_foreground(hwnd):
        return True, ""
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    _request_foreground(hwnd)
    for _ in range(12):
        if is_foreground(hwnd):
            return True, ""
        time.sleep(0.05)

    # Try attaching the calling thread to the existing foreground thread.
    # Never leave threads attached even when SetForegroundWindow fails.
    current = user32.GetForegroundWindow()
    own_tid = kernel32.GetCurrentThreadId()
    foreground_tid = user32.GetWindowThreadProcessId(current, None) if current else 0
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    attached = []
    try:
        for tid in {foreground_tid, target_tid}:
            if tid and tid != own_tid and user32.AttachThreadInput(own_tid, tid, True):
                attached.append(tid)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
    finally:
        for tid in reversed(attached):
            user32.AttachThreadInput(own_tid, tid, False)
    for _ in range(12):
        if is_foreground(hwnd):
            return True, ""
        time.sleep(0.05)
    return False, "Windowsが画面の切り替えを許可しませんでした。ウィンドウモードでお試しください。"

def window_pid(hwnd):
    if not hwnd or not user32.IsWindow(hwnd):
        return 0
    process_id = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
    return int(process_id.value)


def restore_same_process_window(hwnd, expected_pid):
    """Reacquire only a unique visible window from the original game process."""
    if hwnd and window_pid(hwnd) == expected_pid and window_title(hwnd):
        return hwnd, ""
    if not expected_pid:
        return None, "ゲームのウィンドウを再検索して選択してください。"
    candidates = [candidate for candidate, title in list_windows()
                  if candidate != hwnd and window_pid(candidate) == expected_pid
                  and "launcher" not in title.casefold()]
    if len(candidates) == 1:
        return candidates[0], "ゲーム画面を再検出しました"
    return None, "ゲーム画面が変わりました。再検索して正しい画面を選択してください。"


def client_center(hwnd):
    """Return a screen point inside the selected game (even on monitor 2)."""
    rect = wintypes.RECT()
    if not hwnd or not user32.IsWindow(hwnd) or not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("ゲーム画面の位置を取得できません。再検索してください。")
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise RuntimeError("ゲーム画面が最小化されているか、表示できません。")
    point = wintypes.POINT((rect.right - rect.left) // 2,
                           (rect.bottom - rect.top) // 2)
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
        raise RuntimeError("ゲーム画面の座標を取得できません。")
    return point.x, point.y
