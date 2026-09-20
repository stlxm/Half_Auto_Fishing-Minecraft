"""Minecraft semi-automatic fishing: one manual hotkey press per action."""
import ctypes
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk

import keyboard
import pyautogui

APP_NAME = "Minecraft 半自動釣り"
MODES = {
    "右クリックのみ": "click",
    "Esc → 右クリック": "esc_click",
    "Esc → Esc → 右クリック": "esc_esc_click",
}
KEYS = [f"f{i}" for i in range(6, 13)] + ["insert", "home", "end", "page up", "page down"]
DEFAULT = {"hotkey": "f8", "mode": "click", "delay_ms": 120, "cooldown_ms": 500,
           "minecraft_only": True, "focus_minecraft": True}
pyautogui.PAUSE = 0


def config_path():
    base = Path(os.getenv("APPDATA") or Path.home()) / "HalfAutoFishing"
    base.mkdir(parents=True, exist_ok=True)
    return base / "config.json"


def minecraft_active():
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    size = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(size + 1)
    user32.GetWindowTextW(hwnd, title, size + 1)
    return "minecraft" in title.value.casefold()


def find_minecraft_window():
    """Find the Minecraft game window, excluding the launcher."""
    user32 = ctypes.windll.user32
    found = []
    callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def check(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        name = title.value.casefold()
        if "minecraft" in name and "launcher" not in name:
            found.append(hwnd)
        return True

    callback = callback_type(check)
    user32.EnumWindows(callback, 0)
    active = user32.GetForegroundWindow()
    if active in found:
        return active
    return found[0] if found else None


def activate_minecraft():
    """Bring the game to front. Refuse to send keys if activation fails."""
    user32 = ctypes.windll.user32
    hwnd = find_minecraft_window()
    if not hwnd:
        return False, "Minecraftのゲーム画面が見つかりません"
    if user32.GetForegroundWindow() != hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        # Windows may block foreground activation: verify rather than
        # accidentally sending game commands to the current application.
        for _ in range(12):
            if user32.GetForegroundWindow() == hwnd:
                break
            time.sleep(0.05)
    if user32.GetForegroundWindow() != hwnd:
        return False, "Minecraftを前面にできませんでした。ウィンドウモードでお試しください"
    return True, ""


def load_settings():
    try:
        raw = json.loads(config_path().read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return DEFAULT.copy()
        data = {**DEFAULT, **raw}
        if data["hotkey"] not in KEYS or data["mode"] not in MODES.values():
            return DEFAULT.copy()
        data["delay_ms"] = max(0, min(2000, int(data["delay_ms"])))
        data["cooldown_ms"] = max(300, min(10000, int(data["cooldown_ms"])))
        data["minecraft_only"] = bool(data["minecraft_only"])
        data["focus_minecraft"] = bool(data["focus_minecraft"])
        return data
    except (OSError, ValueError, TypeError, KeyError):
        return DEFAULT.copy()


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("510x450")
        self.root.resizable(False, False)
        self.data = load_settings()
        self.hotkey_var = tk.StringVar(value=self.data["hotkey"])
        self.mode_var = tk.StringVar(value=next(
            k for k, v in MODES.items() if v == self.data["mode"]))
        self.delay_var = tk.StringVar(value=str(self.data["delay_ms"]))
        self.cooldown_var = tk.StringVar(value=str(self.data["cooldown_ms"]))
        self.only_var = tk.BooleanVar(value=self.data["minecraft_only"])
        self.focus_var = tk.BooleanVar(value=self.data["focus_minecraft"])
        self.status = tk.StringVar(value="停止中")
        self.enabled = False
        self.hook = None
        self.lock = threading.Lock()
        self.last_start = 0.0
        self.events = queue.Queue()
        self.closing = False
        self.draw()
        self.root.after(100, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

    def draw(self):
        box = ttk.Frame(self.root, padding=18)
        box.pack(fill="both", expand=True)
        ttk.Label(box, text=APP_NAME, font=("Yu Gothic UI", 16, "bold")).pack(anchor="w")
        ttk.Label(box, text="音を聞いて、設定したキーを1回押すと操作します。").pack(
            anchor="w", pady=(4, 14))
        row = ttk.Frame(box)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="操作キー", width=18).pack(side="left")
        ttk.Combobox(row, textvariable=self.hotkey_var, values=KEYS,
                     state="readonly", width=19).pack(side="left")
        row = ttk.Frame(box)
        row.pack(fill="x", pady=4)
        ttk.Label(row, text="操作内容", width=18).pack(side="left")
        ttk.Combobox(row, textvariable=self.mode_var, values=list(MODES),
                     state="readonly", width=25).pack(side="left")
        for caption, var in [("操作間隔 (ms)", self.delay_var),
                              ("連続実行防止 (ms)", self.cooldown_var)]:
            row = ttk.Frame(box)
            row.pack(fill="x", pady=4)
            ttk.Label(row, text=caption, width=18).pack(side="left")
            ttk.Entry(row, textvariable=var, width=12).pack(side="left")
        ttk.Checkbutton(box, text="Minecraftが前面にある場合だけ実行する（推奨）",
                        variable=self.only_var).pack(anchor="w", pady=12)
        ttk.Checkbutton(box, text="別の画面からMinecraftへ切り替えて操作する",
                        variable=self.focus_var).pack(anchor="w", pady=(0, 8))
        ttk.Label(box, text="注意：Escでメニューが開く場合は右クリックのみを選択。",
                  foreground="#8a5200").pack(anchor="w")
        self.button = ttk.Button(box, text="保存して開始", command=self.toggle)
        self.button.pack(fill="x", pady=(18, 8))
        ttk.Label(box, textvariable=self.status).pack(anchor="w")
        ttk.Label(box, text="設定保存先：%APPDATA%\\HalfAutoFishing\\config.json",
                  foreground="#666666").pack(anchor="w", pady=(12, 0))

    def validated(self):
        try:
            delay = int(self.delay_var.get())
            cooldown = int(self.cooldown_var.get())
        except ValueError as exc:
            raise ValueError("時間は整数で入力してください。") from exc
        if not 0 <= delay <= 2000 or not 300 <= cooldown <= 10000:
            raise ValueError("操作間隔は0～2000ms、連続実行防止は300～10000msです。")
        return {"hotkey": self.hotkey_var.get(),
                "mode": MODES[self.mode_var.get()],
                "delay_ms": delay, "cooldown_ms": cooldown,
                "minecraft_only": self.only_var.get(),
                "focus_minecraft": self.focus_var.get()}

    def toggle(self):
        if self.enabled:
            self.stop()
            return
        try:
            new = self.validated()
            config_path().write_text(json.dumps(new, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
            self.data = new
            self.hook = keyboard.add_hotkey(new["hotkey"], self.on_hotkey,
                                             suppress=False, trigger_on_release=False)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("開始できません", str(exc))
            return
        self.enabled = True
        self.button.configure(text="停止")
        self.status.set(f"動作中：{self.data['hotkey'].upper()} で1回実行")

    def on_hotkey(self):
        if not self.enabled or not self.lock.acquire(blocking=False):
            return
        now = time.monotonic()
        if now - self.last_start < self.data["cooldown_ms"] / 1000:
            self.lock.release()
            return
        self.last_start = now
        threading.Thread(target=self.execute, daemon=True).start()

    def execute(self):
        try:
            settings = self.data.copy()
            if settings["focus_minecraft"]:
                ok, reason = activate_minecraft()
                if not ok:
                    self.events.put(reason)
                    return
                # Allow the game to process focus before sending input.
                time.sleep(max(0.12, settings["delay_ms"] / 1000))
            elif settings["minecraft_only"] and not minecraft_active():
                self.events.put("Minecraftが前面ではないため操作しませんでした")
                return
            actions = {
                "click": [],
                "esc_click": ["esc"],
                "esc_esc_click": ["esc", "esc"],
            }[settings["mode"]]
            for key in actions:
                if not self.enabled:
                    return
                if (settings["focus_minecraft"] or settings["minecraft_only"]) and not minecraft_active():
                    self.events.put("Minecraftからフォーカスが外れたため中断しました")
                    return
                pyautogui.press(key)
                time.sleep(settings["delay_ms"] / 1000)
            if self.enabled and (not (settings["focus_minecraft"] or settings["minecraft_only"]) or minecraft_active()):
                pyautogui.click(button="right")
                self.events.put("操作を1回実行しました")
        except Exception as exc:
            self.events.put(f"操作に失敗：{exc}")
        finally:
            self.lock.release()

    def poll(self):
        if self.closing:
            return
        try:
            while True:
                self.status.set(self.events.get_nowait())
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def stop(self):
        self.enabled = False
        if self.hook is not None:
            keyboard.remove_hotkey(self.hook)
            self.hook = None
        self.button.configure(text="保存して開始")
        self.status.set("停止中")

    def close(self):
        self.stop()
        self.closing = True
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
