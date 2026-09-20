"""Convert user-selected audio into Minecraft-compatible OGG Vorbis."""
import subprocess
from pathlib import Path

import imageio_ffmpeg


def convert_to_ogg(source: str, destination: str, gain: float = 1.0) -> None:
    source_path = Path(source)
    output_path = Path(destination)
    if source_path.suffix.lower() not in {".mp3", ".wav", ".ogg"}:
        raise ValueError("MP3・WAV・OGGファイルを選択してください。")
    if not source_path.is_file():
        raise FileNotFoundError("変換元のファイルが見つかりません。")
    if source_path.resolve() == output_path.resolve():
        raise ValueError("変換元とは異なる保存先を指定してください。")

    if not 0 < gain <= 5:
        raise ValueError("音量倍率は0より大きく5以下にしてください。")

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    args = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
        "-y", "-i", str(source_path), "-map", "0:a:0", "-vn",
        "-af", f"volume={gain},alimiter=limit=0.95:level=0",
        "-c:a", "libvorbis", "-q:a", "5", "-f", "ogg",
        str(output_path),
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(args, capture_output=True, text=True,
                            creationflags=flags, timeout=300, check=False)
    if result.returncode != 0 or not output_path.is_file() or output_path.stat().st_size == 0:
        if output_path.exists():
            output_path.unlink()
        raise RuntimeError(
            "OGGへの変換に失敗しました。\n" +
            (result.stderr.strip()[-900:] or "音声ファイルを確認してください。")
        )
