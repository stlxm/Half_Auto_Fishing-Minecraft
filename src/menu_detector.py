"""Pause-menu visual calibration for the selected Minecraft window.

Only the interior of the top-center 'Back to Game' button is compared;
the moving blurred world behind the pause menu is deliberately excluded.
"""
import numpy as np
import pyautogui
from PIL import Image

from window_control import client_bounds


def menu_crop(image):
    """Stable top-center button region; requires unchanged UI scale/resolution."""
    width, height = image.size
    # Minecraft's first pause button is horizontally centered at about 27-33% height.
    left = round(width * 0.405)
    right = round(width * 0.595)
    top = round(height * 0.280)
    bottom = round(height * 0.326)
    if right <= left or bottom <= top:
        raise ValueError("ゲーム画面が小さすぎます")
    return image.crop((left, top, right, bottom)).convert("L").resize((120, 24))


def capture_game_region(hwnd):
    x, y, width, height = client_bounds(hwnd)
    return pyautogui.screenshot(region=(x, y, width, height))


def calibrate_pause_menu(hwnd):
    """Call when the user has opened Minecraft's pause menu."""
    template = np.asarray(menu_crop(capture_game_region(hwnd)), dtype=np.float32)
    if template.std() < 8:
        raise ValueError("メニューのボタンが写っていません。ゲームメニューを開いて再登録してください。")
    return template


def is_pause_menu(hwnd, template, threshold=0.87):
    """Conservative template matching; uncertain matches are NOT assumed paused."""
    if template is None:
        return False
    shot = np.asarray(menu_crop(capture_game_region(hwnd)), dtype=np.float32)
    a = template - template.mean()
    b = shot - shot.mean()
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom < 1:
        return False
    similarity = float(np.sum(a * b) / denom)
    return similarity >= threshold
