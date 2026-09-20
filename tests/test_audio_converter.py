import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from audio_converter import convert_to_ogg


class AudioConverterTests(unittest.TestCase):
    def test_rejects_other_input_formats(self):
        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "bad.txt"
            source.write_text("not audio", encoding="utf-8")
            with self.assertRaises(ValueError):
                convert_to_ogg(str(source), str(Path(tmp) / "out.ogg"))

    def test_missing_input(self):
        with TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                convert_to_ogg(str(Path(tmp) / "missing.mp3"), str(Path(tmp) / "out.ogg"))

    def test_converts_wav_to_ogg(self):
        import math
        import struct
        import wave

        with TemporaryDirectory() as tmp:
            source = Path(tmp) / "test.wav"
            destination = Path(tmp) / "test.ogg"
            with wave.open(str(source), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16000)
                samples = [int(6000 * math.sin(2 * math.pi * 440 * i / 16000))
                           for i in range(1600)]
                output.writeframes(struct.pack("<" + "h" * len(samples), *samples))
            convert_to_ogg(str(source), str(destination))
            self.assertTrue(destination.read_bytes().startswith(b"OggS"))


if __name__ == "__main__":
    unittest.main()
