"""Local Windows speaker-loopback matching of a chosen fishing SE.

Matches short time/frequency patterns, trying independent time and pitch
variations. This is an experimental approximate recognizer, not a guarantee
of distinguishing Minecraft sound events from other desktop audio.
"""
import subprocess
import threading
import time
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from scipy.ndimage import zoom
from scipy.signal import stft

SAMPLE_RATE = 16000
HOP = 160
BANDS = 72
FREQUENCIES = np.geomspace(170, 6800, BANDS)
TEMPOS = (0.70, 0.82, 0.92, 1.0, 1.09, 1.20, 1.36)
PITCHES = (-5, -3, -2, -1, 0, 1, 2, 3, 5)


def decode_audio(path):
    """Decode up to twelve seconds of MP3/WAV/OGG to mono float32 PCM."""
    file = Path(path)
    if file.suffix.lower() not in {".mp3", ".wav", ".ogg"} or not file.is_file():
        raise ValueError("検出音のMP3・WAV・OGGファイルを選択してください。")
    args = [imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-nostdin",
            "-i", str(file), "-t", "12", "-map", "0:a:0", "-vn",
            "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "pipe:1"]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=flags, timeout=40, check=False)
    if result.returncode or len(result.stdout) < SAMPLE_RATE // 4 * 4:
        raise ValueError("音声を解析できませんでした。0.25秒以上の音声を選択してください。")
    return np.frombuffer(result.stdout, dtype="<f4").copy()


def fingerprint(samples):
    """72 log-frequency bands x time. Normalize loudness per frame."""
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    _, ts, spectrum = stft(samples, fs=SAMPLE_RATE, nperseg=512,
                            noverlap=512 - HOP, boundary=None, padded=False)
    if not len(ts):
        raise ValueError("短すぎる音声は検出できません")
    freqs = np.fft.rfftfreq(512, 1 / SAMPLE_RATE)
    magnitude = np.abs(spectrum)
    bands = np.vstack([np.interp(FREQUENCIES, freqs, magnitude[:, t])
                       for t in range(magnitude.shape[1])]).T
    log_bands = np.log1p(bands * 250)
    # Suppress broadband loudness changes and fixed equalization.
    log_bands -= np.median(log_bands, axis=0, keepdims=True)
    frame_norms = np.linalg.norm(log_bands, axis=0, keepdims=True)
    return log_bands / np.maximum(frame_norms, 1e-6)


def make_templates(samples):
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    # Use the loudest ~0.8s rather than assuming the beginning is not silent.
    duration = min(len(samples), int(0.8 * SAMPLE_RATE))
    if duration < int(0.25 * SAMPLE_RATE):
        raise ValueError("検出音は0.25秒以上必要です")
    power = np.convolve(samples * samples,
                        np.ones(duration // 8, dtype=np.float32) / (duration // 8),
                        mode="valid")
    peak = int(np.argmax(power))
    start = max(0, min(len(samples) - duration, peak - duration // 2))
    segment = samples[start:start + duration]
    if np.sqrt(np.mean(segment ** 2)) < 0.0005:
        raise ValueError("検出音が無音または小さすぎます")
    original = fingerprint(segment)
    templates = []
    steps_per_semitone = (BANDS - 1) / (12 * np.log2(FREQUENCIES[-1] / FREQUENCIES[0]))
    for tempo in TEMPOS:
        stretched = zoom(original, (1, 1 / tempo), order=1)
        for pitch in PITCHES:
            # Moving log-frequency bins and resizing time independently allows
            # both game pitch changes and independent changes in playback speed.
            shift = int(round(pitch * steps_per_semitone))
            shifted = np.roll(stretched, shift, axis=0)
            if shift > 0:
                shifted[:shift, :] = 0
            elif shift < 0:
                shifted[shift:, :] = 0
            centered = shifted - shifted.mean()
            magnitude = float(np.linalg.norm(centered))
            if magnitude > 1e-4:
                templates.append((centered, magnitude))
    return templates


def match_score(recent_pcm, templates):
    """Best normalized correlation over bounded tempo/pitch and time offsets."""
    samples = np.asarray(recent_pcm, dtype=np.float32)
    if len(samples) < SAMPLE_RATE // 3 or np.sqrt(np.mean(samples ** 2)) < 0.001:
        return 0.0
    signal = fingerprint(samples)
    best = 0.0
    for template, norm in templates:
        length = template.shape[1]
        if length > signal.shape[1]:
            continue
        # Compare a few nearby offsets: the device capture is not frame aligned.
        latest = signal.shape[1] - length
        for offset in range(max(0, latest - 50), latest + 1, 5):
            candidate = signal[:, offset:offset + length]
            candidate = candidate - candidate.mean()
            energy = float(np.linalg.norm(candidate))
            if energy > 0:
                score = float(np.sum(template * candidate) / (norm * energy))
                best = max(best, score)
    return best


def monitor_sound(path, should_stop, on_match, on_status, threshold=0.78):
    """Blocking loop for a background thread. Records default speaker output."""
    import soundcard as sc

    on_status("検出音を解析しています…")
    templates = make_templates(decode_audio(path))
    speaker = sc.default_speaker()
    if speaker is None:
        raise RuntimeError("Windowsの既定の再生デバイスが見つかりません")
    loopback = sc.get_microphone(id=speaker.id, include_loopback=True)
    if loopback is None:
        raise RuntimeError("再生音のループバック録音を開始できません")
    on_status("音声監視中：" + speaker.name + "（ほかのアプリの音にも反応する場合があります）")
    buffer = np.empty(0, dtype=np.float32)
    last_match = 0.0
    with loopback.recorder(samplerate=SAMPLE_RATE, blocksize=4096) as recorder:
        while not should_stop.is_set():
            frames = recorder.record(numframes=4096)
            if frames.size == 0:
                continue
            mono = np.asarray(frames, dtype=np.float32).mean(axis=1)
            buffer = np.concatenate((buffer, mono))[-int(2.5 * SAMPLE_RATE):]
            if len(buffer) < SAMPLE_RATE:
                continue
            score = match_score(buffer, templates)
            now = time.monotonic()
            if score >= threshold and now - last_match >= 4.0:
                last_match = now
                on_status(f"登録音を検出（類似度 {score:.2f}）")
                on_match()
    on_status("音声監視を停止しました")
