"""Minecraft semi-automatic fishing: one manual hotkey press per action."""
import ctypes
import json
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from resource_pack import create_resource_pack
from window_control import (list_windows, default_minecraft_window,
                            activate_window, is_foreground, window_title)

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
           "minecraft_only": True, "focus_minecraft": True, "volume_gain": 3.0}
pyautogui.PAUSE = 0


def config_path():
    base = Path(os.getenv("APPDATA") or Path.home()) / "HalfAutoFishing"
    base.mkdir(parents=True, exist_ok=True)
    return base / "config.json"


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
        data["volume_gain"] = max(1.0, min(5.0, round(float(data["volume_gain"]), 1)))
        return data
    except (OSError, ValueError, TypeError, KeyError):
        return DEFAULT.copy()


class App:
    def __init__(self, root):
        self.root = root
        self.root.title(APP_NAME)
        self.root.geometry("565x710")
        self.root.resizable(False, False)
        self.data = load_settings()
        self.hotkey_var = tk.StringVar(value=self.data["hotkey"])
        self.mode_var = tk.StringVar(value=next(
            k for k, v in MODES.items() if v == self.data["mode"]))
        self.delay_var = tk.StringVar(value=str(self.data["delay_ms"]))
        self.cooldown_var = tk.StringVar(value=str(self.data["cooldown_ms"]))
        self.only_var = tk.BooleanVar(value=self.data["minecraft_only"])
        self.focus_var = tk.BooleanVar(value=self.data["focus_minecraft"])
        self.target_hwnd = None
        self.window_choices = {}
        self.window_var = tk.StringVar(value="ウィンドウを選択してください")
        self.volume_var = tk.DoubleVar(value=self.data["volume_gain"])
        self.status = tk.StringVar(value="停止中")
        self.enabled = False
        self.hook = None
        self.lock = threading.Lock()
        self.last_start = 0.0
        self.events = queue.Queue()
        self.closing = False
        self.creating_pack = False
        self.draw()
        self.refresh_windows()
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
        ttk.Label(box, text="操作するゲーム画面（別モニターからでも選択可能）").pack(anchor="w", pady=(10, 3))
        target_row = ttk.Frame(box)
        target_row.pack(fill="x")
        self.window_combo = ttk.Combobox(target_row, textvariable=self.window_var,
                                         state="readonly", width=47)
        self.window_combo.pack(side="left", fill="x", expand=True)
        self.window_combo.bind("<<ComboboxSelected>>", self.select_window)
        ttk.Button(target_row, text="再検索", command=self.refresh_windows).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(box, text="F8で選択したウィンドウに切り替えてから操作する",
                        variable=self.focus_var).pack(anchor="w", pady=(9, 4))
        ttk.Label(box, text="対象を選択すると、ほかのアプリへの誤入力を防止します。",
                  foreground="#666666").pack(anchor="w")
        ttk.Label(box, text="注意：Escでメニューが開く場合は右クリックのみを選択。",
                  foreground="#8a5200").pack(anchor="w")
        self.button = ttk.Button(box, text="保存して開始", command=self.toggle)
        self.button.pack(fill="x", pady=(18, 8))
        ttk.Label(box, textvariable=self.status).pack(anchor="w")
        ttk.Label(box, text="設定保存先：%APPDATA%\\HalfAutoFishing\\config.json",
                  foreground="#666666").pack(anchor="w", pady=(12, 0))

        ttk.Separator(box, orient="horizontal").pack(fill="x", pady=14)
        ttk.Label(box, text="釣りSEリソースパック作成", font=("Yu Gothic UI", 11, "bold")).pack(anchor="w")
        ttk.Label(box, text="MP3・WAV・OGGから導入用ZIPを作ります。").pack(anchor="w", pady=(3, 6))
        gain_row = ttk.Frame(box)
        gain_row.pack(fill="x", pady=(2, 6))
        ttk.Label(gain_row, text="音量（1.0～5.0倍）").pack(side="left")
        self.gain_display = ttk.Label(gain_row, text=f'{self.volume_var.get():.1f} 倍')
        self.gain_display.pack(side="right")
        ttk.Scale(box, from_=1.0, to=5.0, orient="horizontal",
                  variable=self.volume_var, command=self.on_gain_change).pack(fill="x")
        self.pack_button = ttk.Button(box, text="音声を選んでリソースパックを作成", command=self.choose_pack_audio)
        self.pack_button.pack(fill="x")
        self.pack_status = tk.StringVar(value="作成待機中")
        ttk.Label(box, textvariable=self.pack_status).pack(anchor="w", pady=(6, 0))

    def refresh_windows(self):
        windows = [(hwnd, title) for hwnd, title in list_windows()
                   if hwnd != self.root.winfo_id()]
        choices = {}
        for hwnd, title in windows:
            label = f"{title[:56]}  [ID:{hwnd}]"
            choices[label] = hwnd
        self.window_choices = choices
        self.window_combo.configure(values=list(choices))
        if self.target_hwnd in choices.values():
            selected = next(label for label, hwnd in choices.items()
                            if hwnd == self.target_hwnd)
            self.window_var.set(selected)
        else:
            self.target_hwnd = default_minecraft_window(windows)
            if self.target_hwnd:
                selected = next(label for label, hwnd in choices.items()
                                if hwnd == self.target_hwnd)
                self.window_var.set(selected)
            else:
                self.window_var.set("操作対象を選択してください")
        if self.target_hwnd:
            self.status.set("操作対象：" + window_title(self.target_hwnd)[:55])

    def select_window(self, event=None):
        self.target_hwnd = self.window_choices.get(self.window_var.get())
        if self.target_hwnd:
            self.status.set("操作対象：" + window_title(self.target_hwnd)[:55])

    def on_gain_change(self, value):
        gain = round(float(value), 1)
        self.gain_display.configure(text=f"{gain:.1f} 倍")
        # Persist independently from the fishing hotkey/start button.
        try:
            updated = load_settings()
            updated["volume_gain"] = gain
            config_path().write_text(
                json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            self.pack_status.set("音量の設定を保存できませんでした")

    def choose_pack_audio(self):
        if self.creating_pack:
            return
        source = filedialog.askopenfilename(
            parent=self.root, title="釣りSEにする音声を選択",
            filetypes=[("音声ファイル", "*.mp3 *.wav *.ogg"), ("すべてのファイル", "*.*")]
        )
        if not source:
            return
        target = filedialog.asksaveasfilename(
            parent=self.root, title="リソースパックZIPの保存先",
            initialdir=str(Path(source).parent),
            initialfile="Fishing_SE_Custom_26_2.zip",
            defaultextension=".zip", filetypes=[("ZIPファイル", "*.zip")]
        )
        if not target:
            return
        if Path(target).suffix.lower() != ".zip":
            messagebox.showerror("保存できません", "保存先の拡張子は .zip にしてください。")
            return
        self.creating_pack = True
        self.pack_button.configure(state="disabled")
        self.pack_status.set("音声を変換してリソースパックを作成しています…")
        gain = round(self.volume_var.get(), 1)
        threading.Thread(target=self.build_pack, args=(source, target, gain), daemon=True).start()

    def build_pack(self, source, target, gain):
        try:
            create_resource_pack(source, target, gain=gain)
            self.events.put(("pack_ready", target))
        except Exception as exc:
            self.events.put(("pack_error", str(exc)))

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
            new["volume_gain"] = round(self.volume_var.get(), 1)
            config_path().write_text(json.dumps(new, ensure_ascii=False, indent=2),
                                     encoding="utf-8")
            self.data = new
            self.hook = keyboard.add_hotkey(new["hotkey"], self.on_hotkey,
                                             suppress=False, trigger_on_release=False)
        except (OSError, ValueError, KeyError) as exc:
            messagebox.showerror("開始できません", str(exc))
            return
        if not self.target_hwnd or not window_title(self.target_hwnd):
            if self.hook is not None:
                keyboard.remove_hotkey(self.hook)
                self.hook = None
            messagebox.showwarning("操作対象が未選択", "「再検索」でMinecraftのゲーム画面を選んでください。")
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
            hwnd = self.target_hwnd
            if not hwnd or not window_title(hwnd):
                self.events.put("操作対象が見つかりません。ゲーム画面を再選択してください")
                return
            if settings["focus_minecraft"]:
                ok, reason = activate_window(hwnd)
                if not ok:
                    self.events.put(reason)
                    return
                time.sleep(max(0.18, settings["delay_ms"] / 1000))
            elif not is_foreground(hwnd):
                self.events.put("操作対象が前面ではありません。画面切替をオンにしてください")
                return
            actions = {
                "click": [],
                "esc_click": ["esc"],
                "esc_esc_click": ["esc", "esc"],
            }[settings["mode"]]
            for key in actions:
                if not self.enabled or not is_foreground(hwnd):
                    self.events.put("対象ウィンドウからフォーカスが外れたため中断しました")
                    return
                pyautogui.press(key)
                time.sleep(settings["delay_ms"] / 1000)
            if self.enabled and is_foreground(hwnd):
                pyautogui.click(button="right")
                self.events.put("選択したウィンドウで操作を1回実行しました")
            else:
                self.events.put("対象ウィンドウからフォーカスが外れたため中断しました")
        except Exception as exc:
            self.events.put(f"操作に失敗：{exc}")
        finally:
            self.lock.release()

    def poll(self):
        if self.closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                if isinstance(event, tuple):
                    kind, detail = event
                    self.creating_pack = False
                    self.pack_button.configure(state="normal")
                    if kind == "pack_ready":
                        self.pack_status.set("作成完了：" + Path(detail).name)
                        messagebox.showinfo(
                            "リソースパック完成",
                            "音声入りリソースパックを保存しました。\\n" + detail +
                            "\\n\\nZIPのままMinecraftのresourcepacksに入れて有効にしてください。")
                    else:
                        self.pack_status.set("作成失敗")
                        messagebox.showerror("リソースパック作成失敗", detail)
                else:
                    self.status.set(event)
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
