from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import wave

import numpy as np

from sonustemper.album_cleanup import analyze
from sonustemper.album_workflow import compare_noise, render_album
from sonustemper.spectral_cleanup import apply_regions, detect, detect_regions

RATE = 24000


def tone(hz, seconds, amplitude):
    return amplitude * np.sin(2 * np.pi * hz * np.arange(round(RATE * seconds)) / RATE)


def demonstration():
    rng = np.random.default_rng(12)
    music = tone(420, 2, .16) + tone(840, 2, .055)
    quiet = rng.normal(0, .0015, RATE * 2)
    whistle = tone(3120, 2, .011) * (np.arange(RATE * 2) / RATE % 2 > .1)
    track = np.r_[music, quiet, music, np.zeros(RATE), whistle, music]
    return np.stack((track, np.r_[music, quiet, music, np.zeros(RATE), -whistle, music]), axis=1).astype("<f4")


def save_wave(path, audio):
    with wave.open(str(path), "wb") as output:
        output.setparams((audio.shape[1], 2, RATE, 0, "NONE", "not compressed"))
        output.writeframes((audio * 32767).astype("<i2").tobytes())


def read_audio(path):
    return np.frombuffer(subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path),
                                                 "-ar", str(RATE), "-ac", "2", "-f", "f32le", "-"]), "<f4").reshape(-1, 2)


def region(start, end, low, high, method="spectral", depth=12):
    return {"t0_sec": start, "t1_sec": end, "f_low": low, "f_high": high, "method": method,
            "apply_scope": "selection", "band_depth_db": depth}


def edit(audio, regions):
    output = BytesIO()
    apply_regions(audio, RATE, regions, output)
    return np.frombuffer(output.getvalue(), "<f4").reshape(audio.shape)


class SpectralMathTests(unittest.TestCase):
    def test_detector_sees_stereo_whistle_and_quiet_fuzz_but_not_silence(self):
        audio = demonstration()
        found = detect_regions(audio, RATE, 2048)
        quiet, whistle = found
        self.assertEqual(quiet["kind"], "quiet_fuzz")
        self.assertTrue(2 <= quiet["start"] < 2.4 and 3.6 < quiet["end"] <= 4)
        self.assertEqual(whistle["kind"], "narrow_tone")
        self.assertLess(whistle["f_low"], 3120)
        self.assertGreater(whistle["f_high"], 3120)
        self.assertTrue(7 <= whistle["start"] < 7.6 and 8.6 < whistle["end"] <= 9.2)

    def test_learned_fuzz_finds_other_gap_not_the_reference_or_silence(self):
        audio = demonstration()
        new = np.concatenate((audio, audio[2*RATE:4*RATE] * .35))
        found = detect_regions(new, RATE, 2048, (2.3, 3.7))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["kind"], "quiet_fuzz")
        self.assertGreater(found[0]["start"], 11)
        with self.assertRaisesRegex(ValueError, "steady, quiet fuzz"):
            detect_regions(audio, RATE, 2048, (.3, 1.4))

    def test_spectral_boxes_preserve_outside_samples_and_unselected_tones(self):
        bass, whistle = tone(410, 5, .18), tone(3120, 5, .05)
        raw = np.stack((bass + whistle, bass - whistle), axis=1).astype("<f4")
        result = edit(raw, [region(1.3, 3.3, 3100, 3140), region(3.8, 4.2, 390, 430)])
        for a, b in [(0, 1.3), (3.3, 3.8), (4.2, 5)]:
            np.testing.assert_array_equal(result[round(a*RATE):round(b*RATE)], raw[round(a*RATE):round(b*RATE)])
        for index, allowed in enumerate((1, -1)):
            sl = slice(2*RATE, 3*RATE)
            retained = abs(np.dot(result[sl, index], bass[sl]) / np.dot(raw[sl, index], bass[sl]))
            reduced = abs(np.dot(result[sl, index], whistle[sl]) / np.dot(raw[sl, index], whistle[sl]))
            self.assertAlmostEqual(retained, 1, places=4)
            self.assertAlmostEqual(reduced, .25, delta=.015)
            self.assertEqual(np.sign(np.dot(result[sl, index], whistle[sl])), allowed)

    def test_profile_reduces_fuzz_without_touching_neighboring_music(self):
        audio = demonstration()
        after = edit(audio, [region(2, 4, 20, 12000, "profile", 8)])
        compare = slice(round(2.3 * RATE), round(3.7 * RATE))
        self.assertLess(np.linalg.norm(after[compare]), np.linalg.norm(audio[compare]) * .85)
        np.testing.assert_array_equal(after[:2*RATE], audio[:2*RATE])
        np.testing.assert_array_equal(after[4*RATE:], audio[4*RATE:])


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class SpectralWorkflowTests(unittest.TestCase):
    def test_analysis_comparison_and_final_wav_and_mp3_use_the_same_cut(self):
        with tempfile.TemporaryDirectory() as directory:
            album = Path(directory)
            track = album / "raw" / "Spectral QA.wav"
            track.parent.mkdir()
            save_wave(track, demonstration())
            original = track.read_bytes()
            report = analyze(track, RATE, 11)
            self.assertEqual(report["windows"], 22)
            self.assertEqual([item["kind"] for item in detect(track)], ["quiet_fuzz", "narrow_tone"])
            suggestion = next(item for item in report["findings"] if item["kind"] == "narrow_tone")
            base = {"source":"raw", "balance":"album", "lufs":-20, "peak":-1.5, "year":2026,
                    "repair_chain":{"global":"", "tone":"", "fixes":[]}}
            settings = {**base, "track_spectral_noise": {track.name: [
                region(suggestion["start"], suggestion["end"], suggestion["f_low"], suggestion["f_high"])
            ]}}
            compare_noise(track, base, settings, album)
            before, after, removed = [read_audio(album / f"{side}.wav") for side in ("before", "after", "removed")]
            np.testing.assert_array_equal(removed, before - after)
            np.testing.assert_array_equal(removed[:7*RATE], np.zeros((7*RATE, 2)))
            self.assertLess(np.linalg.norm(after[8*RATE:round(8.5*RATE)]),
                            np.linalg.norm(before[8*RATE:round(8.5*RATE)]) * .4)
            result, = render_album(album, [track], settings)
            for path in (album / "normalized").glob("*.wav"):
                final = read_audio(path)
                self.assertLess(np.linalg.norm(final[8*RATE:round(8.5*RATE)]) / np.linalg.norm(final[:RATE//2]), .04)
            self.assertTrue((album / "normalized" / result["wav"]).is_file())
            mp3, = (album / "mp3s").glob("*.mp3")
            final_mp3 = read_audio(mp3)
            self.assertLess(np.linalg.norm(final_mp3[8*RATE:round(8.5*RATE)]) / np.linalg.norm(final_mp3[:RATE//2]), .04)
            self.assertEqual(track.read_bytes(), original)
