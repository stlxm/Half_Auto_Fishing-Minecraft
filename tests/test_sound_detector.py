"""Offline recognition checks; these tests never access an audio device."""
import unittest

import numpy as np
from scipy.signal import chirp, resample_poly

from sound_detector import (HOP, PITCHES, SAMPLE_RATE, TEMPOS, fingerprint,
                            make_templates, match_score, convert_process_pcm)


class SoundDetectorTests(unittest.TestCase):
    @staticmethod
    def sample():
        length = int(0.55 * SAMPLE_RATE)
        t = np.arange(length) / SAMPLE_RATE
        envelope = np.sin(np.pi * np.arange(length) / length) ** 2
        return (0.22 * envelope *
                (chirp(t, f0=440, f1=1900, t1=t[-1], method="linear") +
                 0.5 * chirp(t, f0=650, f1=3200, t1=t[-1], method="quadratic"))
                ).astype(np.float32)

    def test_short_sound_matches_itself(self):
        source = self.sample()
        templates = make_templates(source)
        score = match_score(np.concatenate([np.zeros(7000, np.float32), source]), templates)
        self.assertGreater(score, 0.65)

    def test_pitch_and_time_variations_present(self):
        templates = make_templates(self.sample())
        self.assertEqual(len(templates), len(TEMPOS) * len(PITCHES))
        lengths = {matrix.shape[1] for matrix, _ in templates}
        self.assertGreater(len(lengths), 3)

    def test_proc_tap_stereo_pcm_downsamples_without_other_sources(self):
        t = np.arange(48000, dtype=np.float32) / 48000
        minecraft = np.sin(2 * np.pi * 440 * t).astype(np.float32)
        pcm = np.column_stack((minecraft, minecraft)).astype("<f4").tobytes()
        downsampled = convert_process_pcm(pcm)
        self.assertEqual(downsampled.shape, (SAMPLE_RATE,))
        self.assertGreater(float(np.sqrt(np.mean(downsampled ** 2))), 0.3)

    def test_low_amplitude_and_silence_do_not_trigger(self):
        templates = make_templates(self.sample())
        self.assertEqual(match_score(np.zeros(SAMPLE_RATE, np.float32), templates), 0.0)

    def test_speed_changed_sound_is_considered(self):
        source = self.sample()
        altered = resample_poly(source, 5, 6).astype(np.float32)
        score = match_score(np.concatenate([np.zeros(6000, np.float32), altered]), make_templates(source))
        self.assertGreater(score, 0.40)


if __name__ == "__main__":
    unittest.main()
