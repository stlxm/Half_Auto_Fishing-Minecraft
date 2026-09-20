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
from menu_detector import calibrate_pause_menu, is_pause_menu
from sound_detector import monitor_sound
from window_control import (list_windows, default_minecraft_window,
                            activate_window, is_foreground, window_title, client_center,
                            window_pid, restore_same_process_window)

import keyboard
import pyautogui

APP_NAME = "Minecraft 半自動釣り"
MODES = {
    "右クリックのみ": "click",
    "Esc → 右クリック": "esc_click",
    "Esc → Esc → 右クリック": "esc_esc_click",
    "メニューならEsc→右クリック／通常時は右クリック": "smart",
}

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
        if not isinstance(data["hotkey"], str) or data["mode"] not in MODES.values():
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
        self.root.geometry("625x900")
        self.root.resizable(True, True)
        self.data = load_settings()
        self.hotkey_var = tk.StringVar(value=self.data["hotkey"])
        self.mode_var = tk.StringVar(value=next(
            k for k, v in MODES.items() if v == self.data["mode"]))
        self.delay_var = tk.StringVar(value=str(self.data["delay_ms"]))
        self.cooldown_var = tk.StringVar(value=str(self.data["cooldown_ms"]))
        self.only_var = tk.BooleanVar(value=self.data["minecraft_only"])
        self.focus_var = tk.BooleanVar(value=self.data["focus_minecraft"])
        self.target_hwnd = None
        self.target_pid = 0
        self.capture_hook = None
        self.capturing = False
        self.capture_keys = set()
        self.window_choices = {}
        self.window_var = tk.StringVar(value="ウィンドウを選択してください")
        self.volume_var = tk.DoubleVar(value=self.data["volume_gain"])
        self.status = tk.StringVar(value="停止中")
        self.enabled = False
        self.hook = None
        self.lock = threading.Lock()
        self.last_start = 0.0
        self.hotkey_count = 0
        self.events = queue.Queue()
        self.closing = False
        self.creating_pack = False
        self.pause_template = None
        self.audio_path = self.data.get("detect_audio_path", "")
        self.audio_stop = threading.Event()
        self.auto_running = False
        self.auto_thread = None
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
        ttk.Entry(row, textvariable=self.hotkey_var, width=19).pack(side="left")
        self.capture_button = ttk.Button(row, text="キーを登録", command=self.capture_hotkey)
        self.capture_button.pack(side="left", padx=(8, 0))
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
        ttk.Checkbutton(box, text="操作キーで選択したウィンドウに切り替えてから操作する",
                        variable=self.focus_var).pack(anchor="w", pady=(9, 4))
        ttk.Label(box, text="対象を選択すると、ほかのアプリへの誤入力を防止します。",
                  foreground="#666666").pack(anchor="w")
        ttk.Label(box, text="メニュー判定には事前にポーズメニューの登録が必要です。",
                  foreground="#8a5200").pack(anchor="w")
        ttk.Button(box, text="3秒後にポーズメニューを登録", command=self.schedule_menu_calibration).pack(fill="x", pady=(4, 2))
        self.menu_status = tk.StringVar(value="ポーズメニューは未登録")
        ttk.Label(box, textvariable=self.menu_status).pack(anchor="w")
        self.button = ttk.Button(box, text="保存して開始", command=self.toggle)
        self.button.pack(fill="x", pady=(12, 6))
        self.rearm_button = ttk.Button(box, text="キー検知を再登録", command=self.rearm_hotkey)
        self.rearm_button.pack(fill="x", pady=(0, 5))
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
        ttk.Separator(box, orient="horizontal").pack(fill="x", pady=10)
        ttk.Label(box, text="効果音による自動操作（初期状態：オフ）",
                  font=("Yu Gothic UI", 11, "bold")).pack(anchor="w")
        ttk.Label(box, text="Windows既定の再生デバイスから音を検出します。").pack(anchor="w")
        self.audio_label = tk.StringVar(value=Path(self.audio_path).name if self.audio_path else "検出音は未選択")
        ttk.Label(box, textvariable=self.audio_label).pack(anchor="w", pady=(3, 2))
        ttk.Button(box, text="検出するMP3・WAV・OGGを選択",
                   command=self.choose_detection_sound).pack(fill="x")
        self.auto_button = ttk.Button(box, text="自動検出を開始", command=self.toggle_auto)
        self.auto_button.pack(fill="x", pady=(6, 2))
        self.auto_status = tk.StringVar(value="音声監視は停止中")
        ttk.Label(box, textvariable=self.auto_status, wraplength=570).pack(anchor="w")

    def schedule_menu_calibration(self):
        if not self.target_hwnd:
            messagebox.showwarning("ゲーム画面未選択", "先にMinecraftを操作対象に選択してください。")
            return
        self.menu_status.set("3秒以内にMinecraftのポーズメニューを開いてください…")
        self.root.after(3000, self.finish_menu_calibration)

    def finish_menu_calibration(self):
        try:
            hwnd, reason = restore_same_process_window(self.target_hwnd, self.target_pid)
            if not hwnd:
                raise RuntimeError(reason)
            if not is_foreground(hwnd):
                raise RuntimeError("Minecraftが前面になっていません。ゲームメニューを開いて再登録してください。")
            self.pause_template = calibrate_pause_menu(hwnd)
            self.menu_status.set("ポーズメニューを登録しました（解像度やGUI倍率を変えたら再登録）")
        except Exception as exc:
            self.pause_template = None
            self.menu_status.set("メニュー登録失敗：" + str(exc))

    def choose_detection_sound(self):
        if self.auto_running:
            messagebox.showinfo("音声監視中", "先に自動検出を停止してください。")
            return
        path = filedialog.askopenfilename(
            parent=self.root, title="検出したい効果音を選択",
            filetypes=[("音声ファイル", "*.mp3 *.wav *.ogg"), ("すべてのファイル", "*.*")]
        )
        if path:
            self.audio_path = path
            self.audio_label.set(Path(path).name)
            self.save_audio_path()

    def save_audio_path(self):
        try:
            updated = load_settings()
            updated["detect_audio_path"] = self.audio_path
            config_path().write_text(
                json.dumps(updated, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as exc:
            self.auto_status.set(f"検出音を保存できません：{exc}")

    def toggle_auto(self):
        if self.auto_running:
            self.stop_auto()
            return
        if not self.target_hwnd or not window_title(self.target_hwnd):
            messagebox.showwarning("ゲーム画面未選択", "Minecraftのウィンドウを選択してください。")
            return
        if not self.audio_path or not Path(self.audio_path).is_file():
            messagebox.showwarning("検出音未選択", "リソースパックで使った音声を選択してください。")
            return
        if self.mode_var.get() not in MODES:
            messagebox.showwarning("操作未設定", "操作内容を選択してください。")
            return
        # Audio-only operation shares the same action settings as the manual key.
        try:
            if self.enabled:
                self.data.update(self.validated())
            else:
                self.data = self.validated()
                self.data["volume_gain"] = round(self.volume_var.get(), 1)
                self.data["detect_audio_path"] = self.audio_path
        except ValueError as exc:
            messagebox.showerror("自動検出を開始できません", str(exc))
            return
        self.target_pid = window_pid(self.target_hwnd)
        self.audio_stop = threading.Event()
        self.auto_running = True
        self.auto_button.configure(text="自動検出を停止")
        self.auto_status.set("Windowsの再生音を監視する準備をしています…")
        self.auto_thread = threading.Thread(
            target=self.auto_worker, args=(self.audio_path, self.audio_stop), daemon=True
        )
        self.auto_thread.start()

    def auto_worker(self, path, stop_event):
        try:
            monitor_sound(path, stop_event, self.on_auto_match,
                          lambda msg: self.events.put(("audio_status", msg)))
        except Exception as exc:
            self.events.put(("audio_error", str(exc)))
        finally:
            self.events.put(("audio_stopped", stop_event))

    def on_auto_match(self):
        if self.auto_running and not self.audio_stop.is_set():
            self.queue_action("音声")

    def stop_auto(self):
        if not self.auto_running:
            return
        self.auto_running = False
        self.audio_stop.set()
        self.auto_button.configure(text="自動検出を開始")
        self.auto_status.set("音声監視を停止しています…")

    def capture_hotkey(self):
        """Register a hotkey directly from a physical keypress, including combos."""
        if self.enabled:
            messagebox.showinfo("先に停止", "キーを変更する前にマクロを停止してください。")
            return
        if self.capturing:
            return
        self.capturing = True
        self.capture_keys = set()
        self.capture_pending = False
        self.capture_button.configure(text="キーを押してください", state="disabled")
        self.status.set("新しい操作キーを押してください（Escでキャンセル）")
        self.capture_hook = keyboard.hook(self.on_capture_event, suppress=False)

    def on_capture_event(self, event):
        if not self.capturing or self.capture_pending:
            return
        name = event.name
        modifiers = {"ctrl", "shift", "alt", "windows"}
        alias = {"left ctrl": "ctrl", "right ctrl": "ctrl",
                 "left shift": "shift", "right shift": "shift",
                 "left alt": "alt", "right alt": "alt",
                 "left windows": "windows", "right windows": "windows"}
        name = alias.get(name, name)
        if event.event_type == "down" and name in modifiers:
            self.capture_keys.add(name)
            return
        if event.event_type != "down" or name in modifiers:
            return
        if name == "esc":
            self.capture_pending = True
            self.events.put(("captured", None))
            return
        combo = "+".join(sorted(self.capture_keys) + [name])
        self.capture_pending = True
        self.events.put(("captured", combo))

    def finish_capture(self, combo):
        self.capturing = False
        self.capture_pending = False
        if self.capture_hook is not None:
            keyboard.unhook(self.capture_hook)
            self.capture_hook = None
        self.capture_button.configure(text="キーを登録", state="normal")
        if combo:
            self.hotkey_var.set(combo)
            self.status.set(f"操作キーを登録：{combo}（保存して開始で反映）")
        else:
            self.status.set("キー登録をキャンセルしました")

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
            self.target_pid = window_pid(self.target_hwnd)
            self.status.set("操作対象：" + window_title(self.target_hwnd)[:55])
        else:
            self.target_pid = 0
            self.status.set("ゲーム画面を選択してください")

    def select_window(self, event=None):
        self.target_hwnd = self.window_choices.get(self.window_var.get())
        if self.target_hwnd:
            self.target_pid = window_pid(self.target_hwnd)
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
            self.events.put(("pack_ready", (target, source)))
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
            if self.capturing:
                raise ValueError("キーの登録を完了してから開始してください。")
            try:
                keyboard.parse_hotkey(new["hotkey"])
            except (ValueError, KeyError) as exc:
                raise ValueError("登録できないキーです。F8 または ctrl+shift+f9 のように指定してください。") from exc
            new["volume_gain"] = round(self.volume_var.get(), 1)
            new["detect_audio_path"] = self.audio_path
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
        self.target_pid = window_pid(self.target_hwnd)
        self.enabled = True
        self.button.configure(text="停止")
        self.status.set(f"キー待機中：{self.data['hotkey'].upper()}（受信数 {self.hotkey_count}）")

    def rearm_hotkey(self):
        """Recreate a stalled global hook without restarting the entire application."""
        if not self.enabled:
            self.status.set("先に「保存して開始」を押してください")
            return
        hotkey = self.data["hotkey"]
        try:
            if self.hook is not None:
                keyboard.remove_hotkey(self.hook)
                self.hook = None
            self.hook = keyboard.add_hotkey(
                hotkey, self.on_hotkey, suppress=False, trigger_on_release=False)
            self.status.set(f"キー検知を再登録しました：{hotkey.upper()}")
        except Exception as exc:
            self.enabled = False
            self.button.configure(text="保存して開始")
            self.status.set(f"キー検知の再登録に失敗：{exc}")

    def on_hotkey(self):
        if not self.enabled:
            return
        self.hotkey_count += 1
        count = self.hotkey_count
        self.events.put(f"操作キーを受信しました（{count}回目）")
        self.queue_action("手動")

    def queue_action(self, source):
        if not self.lock.acquire(blocking=False):
            self.events.put(f"{source}トリガー受信：前の操作を実行中")
            return
        now = time.monotonic()
        if now - self.last_start < max(self.data["cooldown_ms"] / 1000, 4.0 if source == "音声" else 0.0):
            self.lock.release()
            self.events.put("連続実行防止中です。少し待って押してください")
            return
        self.last_start = now
        try:
            threading.Thread(target=self.execute, daemon=True).start()
        except Exception as exc:
            self.lock.release()
            self.events.put(f"操作スレッドの開始に失敗：{exc}")

    def execute(self):
        try:
            settings = self.data.copy()
            hwnd, recovery = restore_same_process_window(self.target_hwnd, self.target_pid)
            if not hwnd:
                self.events.put(recovery)
                return
            if hwnd != self.target_hwnd:
                self.target_hwnd = hwnd
                self.events.put("ゲームウィンドウを再検出しました")
            if settings["focus_minecraft"]:
                self.events.put("操作キー受信：ゲーム画面への切り替え中…")
                ok, reason = activate_window(hwnd)
                if not ok:
                    self.events.put(reason)
                    return
                time.sleep(max(0.18, settings["delay_ms"] / 1000))
            elif not is_foreground(hwnd):
                self.events.put("操作対象が前面ではありません。画面切替をオンにしてください")
                return
            mode = settings["mode"]
            if mode == "smart":
                if self.pause_template is None:
                    self.events.put("メニュー未登録：3秒後にポーズメニューを登録してください")
                    return
                paused = is_pause_menu(hwnd, self.pause_template)
                self.events.put("ポーズメニューを検出" if paused else "通常画面を検出")
                actions = ["esc"] if paused else []
            else:
                actions = {
                    "click": [],
                    "esc_click": ["esc"],
                    "esc_esc_click": ["esc", "esc"],
                }[mode]
            for key in actions:
                if not (self.enabled or self.auto_running) or not is_foreground(hwnd):
                    self.events.put("対象ウィンドウからフォーカスが外れたため中断しました")
                    return
                pyautogui.press(key)
                time.sleep(settings["delay_ms"] / 1000)
            if (self.enabled or self.auto_running) and is_foreground(hwnd):
                x, y = client_center(hwnd)
                pyautogui.click(x=x, y=y, button="right")
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
                    if kind == "captured":
                        self.finish_capture(detail)
                        continue
                    if kind == "audio_status":
                        if self.auto_running:
                            self.auto_status.set(detail)
                        continue
                    if kind == "audio_error":
                        self.auto_running = False
                        self.audio_stop.set()
                        self.auto_button.configure(text="自動検出を開始")
                        self.auto_status.set("音声監視エラー：" + detail)
                        continue
                    if kind == "audio_stopped":
                        if self.audio_stop is detail and not self.auto_running:
                            self.auto_status.set("音声監視は停止中")
                        continue
                    self.creating_pack = False
                    self.pack_button.configure(state="normal")
                    if kind == "pack_ready":
                        zip_path, audio_source = detail
                        self.pack_status.set("作成完了：" + Path(zip_path).name)
                        if not self.auto_running:
                            self.audio_path = audio_source
                            self.audio_label.set(Path(audio_source).name)
                            self.save_audio_path()
                        messagebox.showinfo(
                            "リソースパック完成",
                            "音声入りリソースパックを保存しました。\\n" + zip_path +
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
        self.stop_auto()
        if self.capturing:
            self.finish_capture(None)
        self.stop()
        self.closing = True
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
