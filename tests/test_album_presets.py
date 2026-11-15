from array import array
from importlib import import_module
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from sonustemper import album_api, album_workflow


def setUpModule():
    # Load the actual filter builders without requiring deployment credentials.
    with patch.dict(os.environ, {"SONUSTEMPER_DESKTOP": "1", "SONUSTEMPER_STRICT_CONFIG": "0"}):
        import_module("sonustemper.server")


def settings(**updates):
    return album_api.Settings(location="test", artist_folder="Artist", album_folder="Album",
                              tracks=["Song.wav"], cleanup_version=2, **updates)


class AlbumPresetTests(unittest.TestCase):
    def test_catalog_keeps_original_ids_and_user_presets_resolvable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "voicings").mkdir()
            custom = {"meta": {"title": "Custom import"}, "chain": {"stereo": {"width": 1.15}}}
            (root / "voicings" / "reference.json").write_text(json.dumps(custom))
            with patch.object(album_api, "PRESETS_DIR", root):
                catalog = album_api.get_presets()
                self.assertEqual([item["title"] for item in catalog if item["group"] == "curated"],
                                 ["Clean", "Clarity", "Punch", "Warm", "Wide"])
                self.assertEqual(sum(item["group"] == "legacy" for item in catalog), 18)
                self.assertEqual([item["id"] for item in catalog if item["group"] == "user"],
                                 ["voicing/reference"])
                imported = album_api.resolved_settings(settings(preset="voicing/reference"))
                self.assertIn("slev=1.150", imported["repair_chain"]["tone"])

    def test_new_strength_scales_width_and_zero_bypasses_but_legacy_is_unchanged(self):
        def chain(preset, strength=100, **updates):
            return album_api.resolved_settings(settings(preset=preset, strength=strength,
                                                       **updates))["repair_chain"]["tone"]

        self.assertIn("slev=1.280", chain("album-v1/wide"))
        self.assertIn("slev=1.140", chain("album-v1/wide", 50))
        self.assertIn("slev=1.500", chain("album-v1/wide", 200))
        self.assertIn("slev=0.900", chain("album-v1/wide", 50, stereo_width=.8))
        self.assertIn("acompressor=", chain("album-v1/punch"))
        self.assertIn("treble=f=8500:t=q:w=0.7:g=2.4", chain("album-v1/clarity"))
        self.assertIn("treble=f=8500:t=q:w=0.7:g=4.8", chain("album-v1/clarity", 200))
        self.assertIn("ratio=1.960", chain("album-v1/punch"))
        self.assertIn("ratio=2.400", chain("album-v1/punch", 200))
        self.assertIn("ratio=2.920", chain("album-v1/punch", 200, guardrails=False))
        for name in album_api.ALBUM_VOICINGS:
            self.assertEqual(chain(f"album-v1/{name}", 0, stereo_width=1.4), "")
        self.assertEqual(chain("album-v1/clean"), "highpass=f=25:t=q:w=0.707")
        self.assertIn("acompressor=", chain("builtin/clean"))
        self.assertIn("highpass=f=28", chain("builtin/clean", 0))
        self.assertIn("slev=1.120", chain("builtin/wide", 50))
        self.assertEqual(chain("builtin/wide", 200), chain("builtin/wide"))


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class PresetComparisonTests(unittest.TestCase):
    def test_level_matched_full_preset_pair_keeps_cleanup_and_switches_width(self):
        with tempfile.TemporaryDirectory() as directory:
            album = Path(directory)
            (album / "raw").mkdir()
            track = album / "raw" / "Song.wav"
            mid = "0.1*sin(2*PI*1000*t)+0.1*sin(2*PI*3500*t)"
            side = "0.09*sin(2*PI*7000*t)"
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                            f"aevalsrc={mid}+{side}|{mid}-{side}:s=48000:d=6",
                            "-c:a", "pcm_s24le", str(track)], check=True)
            original = track.read_bytes()
            previews = album / "previews"
            with patch.object(album_api, "resolve_album", return_value=album), \
                    patch.object(album_api, "PREVIEWS_DIR", previews):
                pair = album_api.preset_comparison(settings(preset="album-v1/clean", repairs=[
                    album_api.Repair(id="local", kind="presence", frequency=3500, amount=8, start=2, end=4),
                ], track_overrides={"Song.wav": {"preset": "album-v1/wide"}}))
            token = pair["before"].split("/")[-2]
            before, after = (previews / "album-comparisons" / token / f"{name}.wav"
                             for name in ("before", "after"))
            lufs_before, _ = album_workflow.metrics(before)
            lufs_after, peak_after = album_workflow.metrics(after)
            self.assertAlmostEqual(lufs_before, lufs_after, delta=.15)
            self.assertAlmostEqual(lufs_after, pair["matched_lufs"], delta=.15)
            self.assertLessEqual(peak_after, -1.9)

            def left_amplitudes(path, start):
                raw = subprocess.check_output(["ffmpeg", "-v", "error", "-ss", str(start), "-i", str(path),
                                               "-t", "0.5", "-af", "pan=mono|c0=c0", "-f", "f32le", "-"])
                samples = array("f")
                samples.frombytes(raw)
                return [abs(sum(sample * math.sin(2 * math.pi * hz * i / 48000)
                                for i, sample in enumerate(samples))) for hz in (1000, 3500, 7000)]

            ratios = []
            for version in (before, after):
                base, presence, side_level = left_amplitudes(version, .5)
                repaired_base, repaired_presence, _ = left_amplitudes(version, 2.5)
                self.assertLess(repaired_presence / repaired_base, (presence / base) * .5)
                ratios.append(side_level / base)
            self.assertGreater(ratios[1], ratios[0] * 1.26)
            self.assertEqual(track.read_bytes(), original)
