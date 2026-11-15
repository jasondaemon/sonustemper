from array import array
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sonustemper.album_cleanup import analyze, combine_noise_chains, repair_filter
from sonustemper.album_workflow import (
    compare_repair, compare_song_repairs, compare_tone, delivery_complete, metrics, process_album_track, render_album,
    render_cleanup_audio, repair_batches, track_delivery, track_loudness, wavs,
)
from sonustemper.master_pack import _eq_filter_for_band


class AlbumDeliveryStatusTests(unittest.TestCase):
    def test_completed_status_requires_matching_final_pair_not_clean_intermediate(self):
        suffix = " (Integrated -16.0 LUFS, True Peak -1.5 dB, Channel STEREO)"
        stages = {
            "raw": ["Complete.wav", "WAV only.wav", "MP3 only.wav", "Mismatch.wav"],
            "mastered": [], "clean": ["WAV only.wav"],
            "normalized": [f"{title}{suffix}.wav" for title in ("Complete", "WAV only", "Mismatch")],
            "mp3s": [f"Complete{suffix}.mp3", "MP3 only.mp3", "Mismatch.mp3"],
        }
        delivery = track_delivery(stages)
        self.assertEqual(delivery["Complete.wav"], ["Clean WAV"])
        self.assertEqual([name for name, missing in delivery.items() if delivery_complete(missing)], ["Complete.wav"])


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
    def test_full_song_loudness_reads_input_and_final_and_remeasures_replaced_audio(self):
        with tempfile.TemporaryDirectory() as directory:
            album = Path(directory)
            (album / "raw").mkdir()
            (album / "normalized").mkdir()
            track = album / "raw" / "Song.wav"
            final = album / "normalized" / "Song (Integrated -40 LUFS, True Peak -20 dB, Channel STEREO).wav"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                r"aevalsrc=0.08*sin(2*PI*1000*t)*if(lt(t\,8)\,1\,3):s=48000:d=16",
                "-ac", "2", "-c:a", "pcm_s24le", str(track),
            ], check=True)
            self.assertIsNone(track_loudness(album, track)["final"])

            def deliver(gain):
                subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(track), "-af",
                                f"volume={gain}dB", "-c:a", "pcm_s24le", str(final)], check=True)
                return track_loudness(album, track)

            original = track.read_bytes()
            a, b = deliver(3), deliver(6)
            self.assertGreater(a["source"]["lra"], 7)
            self.assertAlmostEqual(a["final"]["lufs"]-a["source"]["lufs"], 3, delta=.15)
            self.assertAlmostEqual(b["final"]["lufs"]-a["final"]["lufs"], 3, delta=.15)
            self.assertAlmostEqual(b["final"]["lra"], a["source"]["lra"], delta=.15)
            self.assertEqual(track.read_bytes(), original)
            shutil.copyfile(final, album / "normalized" / "Song.wav")
            self.assertEqual(track_loudness(album, track)["final_status"], "Multiple final WAVs")

    def test_eq_ab_matches_level_keeps_cleanup_and_renders_notch(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            track = folder / "song.wav"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "aevalsrc=0.14*sin(2*PI*400*t)+0.10*sin(2*PI*1500*t)+0.07*sin(2*PI*3500*t):s=44100:d=6",
                "-c:a", "pcm_s24le", str(track),
            ], check=True)
            original = track.read_bytes()
            notch = _eq_filter_for_band({"type":"notch", "freq_hz":1500, "q":2}, 1)
            self.assertTrue(notch.startswith("bandreject="))
            dynamics = "acompressor=threshold=0.18:ratio=1.5:attack=10:release=100"
            common = {"global":"", "fixes":[{"id":"repair","start":2,"end":4,
                                            "filter":"equalizer=f=3500:t=q:w=2:g=-8"}]}
            off = {"repair_chain":{**common,"tone":dynamics}}
            on = {"repair_chain":{**common,"tone":f"{notch},{dynamics}"}}
            levels = compare_tone(track, off, on, folder)
            before, after = folder / "before.wav", folder / "after.wav"
            self.assertLessEqual(abs(metrics(before)[0]-metrics(after)[0]), .2)
            self.assertLessEqual(abs(metrics(after)[0]-levels["matched_lufs"]), .2)
            self.assertLessEqual(metrics(after)[1], -1.9)
            for version in (before, after):
                outside = amplitude(version,.5,3500)/amplitude(version,.5,400)
                inside = amplitude(version,2.5,3500)/amplitude(version,2.5,400)
                self.assertLess(inside, outside * .5)
            self.assertLess(amplitude(after,.5,1500), amplitude(before,.5,1500)*.01)
            full_render = folder / "tone.wav"
            process_album_track(track, full_render, on)
            self.assertAlmostEqual(levels["on_peak"], metrics(full_render)[1], places=1)
            self.assertAlmostEqual(amplitude(full_render,2.5,3500)/amplitude(full_render,2.5,400),
                                   amplitude(after,2.5,3500)/amplitude(after,2.5,400), places=3)
            self.assertEqual(track.read_bytes(), original)

    def test_full_song_before_after_comparison_bypasses_only_requested_fixes(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            track = folder / "song.wav"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "aevalsrc=0.13*sin(2*PI*400*t)+0.13*sin(2*PI*3500*t):s=48000:d=7",
                "-c:a", "pcm_s24le", str(track),
            ], check=True)
            original = track.read_bytes()
            fixes = [
                {"id": "manual", "start": 0, "end": None, "filter": "equalizer=f=3500:t=q:w=2:g=3"},
                {"id": "early", "start": 1, "end": 3, "filter": "equalizer=f=3500:t=q:w=2:g=-8"},
                {"id": "late", "start": 4, "end": 6, "filter": "equalizer=f=400:t=q:w=2:g=-8"},
            ]
            settings = {"repair_chain": {"global": "", "tone": "", "fixes": fixes}}
            compare_song_repairs(track, settings, {"early", "late"}, folder)
            before, after = folder / "before.wav", folder / "after.wav"

            def ratio(path, position):
                return amplitude(path, position, 3500) / amplitude(path, position, 400)

            self.assertAlmostEqual(ratio(before, .25), ratio(after, .25), places=2)
            self.assertGreater(ratio(before, .25), ratio(track, .25) * 1.25)
            self.assertLess(ratio(after, 1.5), ratio(before, 1.5) * .5)
            self.assertGreater(ratio(after, 4.5), ratio(before, 4.5) * 2)
            self.assertLessEqual(abs(metrics(before)[0] - metrics(after)[0]), .2)
            self.assertEqual(track.read_bytes(), original)

    def test_batch_reuses_identical_filters_and_can_skip_one_fix(self):
        fixes = [{"id":str(index), "start":index * 2, "end":index * 2 + 1, "filter":"volume=-6dB"}
                 for index in range(35)]
        self.assertEqual([len(batch) for batch in repair_batches(fixes, "1")], [24, 10])
        with tempfile.TemporaryDirectory() as directory:
            track, target = (Path(directory) / name for name in ("source.wav", "filtered.wav"))
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=f=400:r=48000:d=6",
                            "-c:a", "pcm_s24le", str(track)], check=True)
            settings = {"repair_chain":{"global":"", "tone":"", "fixes":fixes}}
            process_album_track(track, target, settings, skip_fix="1")
            self.assertAlmostEqual(amplitude(target, .25, 400) / amplitude(track, .25, 400), .501, places=2)
            self.assertAlmostEqual(amplitude(target, 2.25, 400), amplitude(track, 2.25, 400), places=3)
            self.assertAlmostEqual(amplitude(target, 4.25, 400) / amplitude(track, 4.25, 400), .501, places=2)

    def test_noise_stack_keeps_both_bounded_regions(self):
        def window(start, end, frequency):
            wet = f"between(t,{start},{end})"
            return (f"[0:a]asplit[dry][wet];[wet]equalizer=f={frequency}:t=q:w=2:g=-16,"
                    f"volume='{wet}':eval=frame[w];[dry]volume='1-{wet}':eval=frame[d];"
                    "[w][d]amix=inputs=2:normalize=0[out]", True)

        with tempfile.TemporaryDirectory() as directory:
            track, target = (Path(directory) / name for name in ("source.wav", "filtered.wav"))
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                "aevalsrc=0.13*sin(2*PI*400*t)+0.13*sin(2*PI*3500*t):s=48000:d=5",
                "-c:a", "pcm_s24le", str(track),
            ], check=True)
            stack = combine_noise_chains([window(1, 2, 400), window(3, 4, 3500)])
            process_album_track(track, target, {"noise_chain": stack})
            for hz in (400, 3500):
                self.assertAlmostEqual(amplitude(track, 4.25, hz), amplitude(target, 4.25, hz), places=3)
            self.assertLess(amplitude(target, 1.25, 400), amplitude(track, 1.25, 400) * .25)
            self.assertLess(amplitude(target, 3.25, 3500), amplitude(track, 3.25, 3500) * .25)

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
            cleanup = album / "cleanup-playback.wav"
            render_cleanup_audio(track, cleanup, settings)
            result, = render_album(album, wavs(album, "raw"), settings)
            final = album / "03 - normalized" / result["wav"]

            def ratio(path, position):
                return amplitude(path, position, 3500) / amplitude(path, position, 400)

            self.assertAlmostEqual(ratio(final, .75), ratio(comparison, .75), places=3)
            self.assertAlmostEqual(ratio(final, 4.5), ratio(comparison, 4.5), places=3)
            self.assertLess(ratio(final, 2.5), .5 * ratio(comparison, 2.5))
            self.assertGreater(ratio(final, .75), ratio(track, .75) * 1.25)
            self.assertAlmostEqual(ratio(cleanup, .75), ratio(track, .75), places=3)
            self.assertLess(ratio(cleanup, 2.5), .5 * ratio(track, 2.5))
            before, after = compare_repair(track, settings, "one", 1, 4)
            self.assertTrue(before.startswith(b"RIFF") and after.startswith(b"RIFF"))
            self.assertEqual(len(before), len(after))
            self.assertNotEqual(before, after)
            self.assertEqual(track.read_bytes(), source)
