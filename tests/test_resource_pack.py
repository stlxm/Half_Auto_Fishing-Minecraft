import json
import math
import struct
import unittest
import wave
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from resource_pack import SOUND_ENTRY, create_resource_pack


class ResourcePackTests(unittest.TestCase):
    def test_builds_complete_zip_from_wav(self):
        with TemporaryDirectory() as folder:
            wav = Path(folder) / "sample.wav"
            result = Path(folder) / "Fishing_SE_Custom_26_2.zip"
            with wave.open(str(wav), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                samples = [int(6000 * math.sin(2 * math.pi * 440 * n / 16000))
                           for n in range(1600)]
                audio.writeframes(struct.pack("<" + "h" * len(samples), *samples))
            create_resource_pack(str(wav), str(result))
            with zipfile.ZipFile(result) as pack:
                self.assertIsNone(pack.testzip())
                self.assertEqual(set(pack.namelist()), {
                    "pack.mcmeta", "assets/minecraft/sounds.json", SOUND_ENTRY
                })
                self.assertEqual(pack.read(SOUND_ENTRY)[:4], b"OggS")
                meta = json.loads(pack.read("pack.mcmeta"))
                self.assertEqual(meta["pack"]["pack_format"], 88)
                sounds = json.loads(pack.read("assets/minecraft/sounds.json"))
                self.assertTrue(sounds["entity.fishing_bobber.splash"]["replace"])

    def test_amplifies_ogg_input_and_leaves_original_unchanged(self):
        # Every supported input, including already-OGG files, must be
        # processed with the same amplification rather than copied verbatim.
        with TemporaryDirectory() as folder:
            wav = Path(folder) / "original.wav"
            original_ogg = Path(folder) / "original.ogg"
            output = Path(folder) / "pack.zip"
            import subprocess
            import imageio_ffmpeg

            with wave.open(str(wav), "wb") as audio:
                audio.setnchannels(1)
                audio.setsampwidth(2)
                audio.setframerate(16000)
                samples = [int(1800 * math.sin(2 * math.pi * 440 * n / 16000))
                           for n in range(16000)]
                audio.writeframes(struct.pack("<" + "h" * len(samples), *samples))
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error",
                            "-i", str(wav), "-c:a", "libvorbis", str(original_ogg)],
                           check=True)
            before = original_ogg.read_bytes()
            create_resource_pack(str(original_ogg), str(output))
            self.assertEqual(original_ogg.read_bytes(), before)
            with zipfile.ZipFile(output) as pack:
                result = pack.read(SOUND_ENTRY)
            self.assertTrue(result.startswith(b"OggS"))
            self.assertNotEqual(result, before)

    def test_rejects_non_ogg_data_in_ogg_file(self):
        with TemporaryDirectory() as folder:
            fake = Path(folder) / "fake.ogg"
            fake.write_bytes(b"not ogg")
            with self.assertRaises(ValueError):
                create_resource_pack(str(fake), str(Path(folder) / "out.zip"))


if __name__ == "__main__":
    unittest.main()
