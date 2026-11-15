from array import array
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sonustemper.album_cleanup import analyze, repair_filter
from sonustemper.album_workflow import compare_repair, process_album_track, render_album, wavs


def amplitude(path, start, frequency):
    raw = subprocess.check_output([
        "ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path),
        "-t", "0.5", "-ac", "1", "-ar", "48000", "-f", "f32le", "-",
    ])
    samples = array("f")
    samples.frombytes(raw)
    return abs(sum(sample * math.sin(2 * math.pi * frequency * i / 48000) for i, sample in enumerate(samples)))


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class AlbumCleanupTests(unittest.TestCase):
    def test_full_analysis_finds_late_band(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "song.wav"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                r"aevalsrc=0.13*sin(2*PI*400*t)+if(gte(t\,4)\,0.15\,0)*sin(2*PI*3500*t):s=48000:d=7.25",
                "-c:a", "pcm_s24le", str(path),
            ], check=True)
            report = analyze(path, 48000, 7.25)
            self.assertEqual(report["windows"], 15)
            findings = [item for item in report["findings"] if item["kind"] == "presence"]
            self.assertEqual([(item["start"], item["end"]) for item in findings], [(4, 7.25)])
            self.assertFalse(any(item["kind"] in {"peak", "reverb"} for item in report["findings"]))

    def test_regional_repair_and_tone_reach_the_final_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            album = Path(directory)
            (album / "raw").mkdir()
            track = album / "raw" / "Song.wav"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "aevalsrc=0.13*sin(2*PI*400*t)+0.13*sin(2*PI*3500*t):s=48000:d=6",
                "-c:a", "pcm_s24le", str(track),
            ], check=True)
            source = track.read_bytes()
            fix = {"id":"one", "kind":"presence", "frequency":3500, "amount":8, "start":2, "end":4}
            settings = {"source":"raw", "balance":"album", "lufs":-19, "peak":-1.5, "year":2026,
                        "repair_chain":{"global":"", "tone":"equalizer=f=3500:t=q:w=1.4:g=3",
                                        "fixes":[{**fix, "filter":repair_filter(fix)}]}}
            comparison = album / "without-repair.wav"
            process_album_track(track, comparison, settings, skip_fix="one")
            result, = render_album(album, wavs(album, "raw"), settings)
            final = album / "normalized" / result["wav"]

            def ratio(path, position):
                return amplitude(path, position, 3500) / amplitude(path, position, 400)

            self.assertAlmostEqual(ratio(final, .75), ratio(comparison, .75), places=3)
            self.assertAlmostEqual(ratio(final, 4.5), ratio(comparison, 4.5), places=3)
            self.assertLess(ratio(final, 2.5), .5 * ratio(comparison, 2.5))
            self.assertGreater(ratio(final, .75), ratio(track, .75) * 1.25)
            before, after = compare_repair(track, settings, "one", 1, 4)
            self.assertTrue(before.startswith(b"RIFF") and after.startswith(b"RIFF"))
            self.assertEqual(len(before), len(after))
            self.assertNotEqual(before, after)
            self.assertEqual(track.read_bytes(), source)
