"""Local per-process Windows audio matching of a chosen fishing SE.

Matches short time/frequency patterns, trying independent time and pitch
variations. This is an experimental approximate recognizer, not a guarantee
of distinguishing Minecraft sound events from other desktop audio.
"""
import subprocess
import time
from pathlib import Path

import imageio_ffmpeg
import numpy as np
from scipy.ndimage import zoom
from scipy.signal import stft

SAMPLE_RATE = 16000
HOP = 320
BANDS = 72
FREQUENCIES = np.geomspace(170, 6800, BANDS)
# Minecraft can change playback speed AND pitch together, or independently.
# Keep the search bounded: expanding this too far costs CPU and false positives.
TEMPOS = (0.65, 0.76, 0.86, 0.94, 1.0, 1.07, 1.16, 1.30, 1.50)
PITCHES = (-7, -5, -3, -1, 0, 1, 3, 5, 7)



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
    # Include a distinct phrase of the SE while keeping capture latency short.
    # 0.55 s improves discrimination for short multi-tone signature sounds.
    duration = min(len(samples), int(0.55 * SAMPLE_RATE))
    if duration < int(0.25 * SAMPLE_RATE):
        raise ValueError("検出音は0.25秒以上必要です")
    # Use cumulative energy to avoid a quadratic-time convolution on long files.
    squared = np.square(samples, dtype=np.float64)
    cumulative = np.concatenate(([0.0], np.cumsum(squared)))
    window_energy = cumulative[duration:] - cumulative[:-duration]
    start = int(np.argmax(window_energy))
    segment = samples[start:start + duration]
    if np.sqrt(np.mean(segment ** 2)) < 0.0005:
        raise ValueError("検出音が無音または小さすぎます")
    original = fingerprint(segment)
    templates = []
    steps_per_semitone = (BANDS - 1) / (12 * np.log2(FREQUENCIES[-1] / FREQUENCIES[0]))
    for tempo in TEMPOS:
        # Speed changes shift timing. The pitch variants below cover the
        # accompanying shift in spectral bands as well as independent pitch.
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
        # Search the entire recent buffer. Limiting this to the last ~0.4 s
        # missed a bite while the recognizer was still processing prior audio.
        latest = signal.shape[1] - length
        for offset in range(0, latest + 1, 2):
            candidate = signal[:, offset:offset + length]
            candidate = candidate - candidate.mean()
            energy = float(np.linalg.norm(candidate))
            if energy > 0:
                score = float(np.sum(template * candidate) / (norm * energy))
                best = max(best, score)
    return best


def convert_process_pcm(pcm):
    """ProcTap Windows output: interleaved float32, stereo, 48 kHz."""
    from scipy.signal import resample_poly

    interleaved = np.frombuffer(pcm, dtype="<f4")
    if interleaved.size < 2:
        return np.empty(0, dtype=np.float32)
    stereo = interleaved[:interleaved.size - interleaved.size % 2].reshape(-1, 2)
    # Do not include browser audio: only these bytes come from the target PID.
    mono = stereo.mean(axis=1, dtype=np.float32)
    return resample_poly(mono, 1, 3).astype(np.float32)


def monitor_sound(path, target_pid, should_stop, on_match, on_status, threshold=0.70):
    """Capture only selected game process audio; NEVER fall back to system-wide capture."""
    from proctap import ProcessAudioCapture

    if not isinstance(target_pid, int) or target_pid <= 0:
        raise ValueError("MinecraftのプロセスIDを取得できません。ウィンドウを再選択してください。")
    on_status(f"検出音を解析中… Minecraft PID {target_pid}")
    templates = make_templates(decode_audio(path))
    buffer = np.empty(0, dtype=np.float32)
    last_match = 0.0
    last_status = time.monotonic()
    on_status(f"Minecraftの音声だけを監視中（PID {target_pid}）。YouTube音声は対象外です")
    tap = ProcessAudioCapture(pid=target_pid)
    try:
        tap.start()
        while not should_stop.is_set():
            pcm = tap.read(timeout=0.3)
            if not pcm:
                continue
            mono = convert_process_pcm(pcm)
            if mono.size == 0:
                continue
            buffer = np.concatenate((buffer, mono))[-int(1.5 * SAMPLE_RATE):]
            if buffer.size < int(0.50 * SAMPLE_RATE):
                continue
            score = match_score(buffer, templates)
            now = time.monotonic()
            if now - last_status >= 3.0:
                on_status(f"Minecraft音声のみ（PID {target_pid}）：類似度 {score:.2f}／基準 {threshold:.2f}")
                last_status = now
            if score >= threshold and now - last_match >= 4.0 and not should_stop.is_set():
                last_match = now
                on_status(f"Minecraftで登録音を検出（類似度 {score:.2f}）")
                on_match()
    finally:
        tap.close()
        on_status("Minecraftの音声監視を停止しました")
