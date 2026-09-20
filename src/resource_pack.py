"""Create a ready-to-install Minecraft resource pack containing user-selected audio."""
import json
import tempfile
import zipfile
from pathlib import Path

from audio_converter import convert_to_ogg

# Minecraft Java Edition 26.2. Update this value if the game's pack format changes.
PACK_FORMAT = 88
SOUND_ENTRY = "assets/minecraft/sounds/custom/fishing_alert.ogg"
SOUNDS_JSON = {
    "entity.fishing_bobber.splash": {
        "replace": True,
        "sounds": [{"name": "custom/fishing_alert", "volume": 1.0}]
    }
}


def create_resource_pack(source: str, destination: str) -> None:
    """Convert and amplify MP3/WAV/OGG, then write a complete resource-pack ZIP."""
    source_path = Path(source)
    zip_path = Path(destination)
    if source_path.suffix.lower() not in {".mp3", ".wav", ".ogg"}:
        raise ValueError("MP3・WAV・OGGのいずれかを選択してください。")
    if not source_path.is_file():
        raise FileNotFoundError("選択した音声ファイルが見つかりません。")
    if zip_path.suffix.lower() != ".zip":
        raise ValueError("保存先はZIP形式にしてください。")
    if source_path.resolve() == zip_path.resolve():
        raise ValueError("元の音声ファイルと異なる保存先を指定してください。")

    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as directory:
        ogg_file = Path(directory) / "fishing_alert.ogg"
        if source_path.suffix.lower() == ".ogg":
            with source_path.open("rb") as audio:
                if audio.read(4) != b"OggS":
                    raise ValueError("OGG音声として読み込めません。")
        # Re-encode OGG inputs too, so every supported input receives the same
        # threefold gain and peak limiting; the original file is never changed.
        convert_to_ogg(str(source_path), str(ogg_file), gain=3.0)

        draft_zip = Path(directory) / "pack.zip"
        pack_meta = {
            "pack": {
                "pack_format": PACK_FORMAT,
                "description": "Half Auto Fishing - Custom Fishing Splash SE (Java 26.2)"
            }
        }
        with zipfile.ZipFile(draft_zip, mode="w",
                             compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("pack.mcmeta",
                             json.dumps(pack_meta, ensure_ascii=False, indent=2))
            archive.writestr("assets/minecraft/sounds.json",
                             json.dumps(SOUNDS_JSON, ensure_ascii=False, indent=2))
            archive.write(ogg_file, SOUND_ENTRY)

        # Move the fully assembled ZIP into place only after successful conversion.
        # os.replace supports replacing an existing destination on Windows.
        import os
        os.replace(draft_zip, zip_path)
