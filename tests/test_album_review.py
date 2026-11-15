from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from sonustemper import album_workflow as workflow


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class AlbumReviewTests(unittest.TestCase):
    def test_review_reports_peak_limit_and_publishes_exactly_the_files_reviewed(self):
        with tempfile.TemporaryDirectory() as directory:
            album = Path(directory)
            (album / "raw").mkdir()
            for name, signal in (
                ("01. Steady.wav", "0.04*sin(2*PI*440*t)"),
                ("02. Peaky.wav", r"(0.05+0.86*between(t\,3\,3.03))*sin(2*PI*660*t)"),
            ):
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                                f"aevalsrc={signal}:s=44100:d=8", "-ac", "2",
                                "-c:a", "pcm_s24le", str(album / "raw" / name)], check=True)
            tracks = workflow.wavs(album, "raw")
            original = {track: workflow.digest(track) for track in tracks}
            settings = {"source": "raw", "balance": "album", "lufs": -14, "peak": -1.5, "year": 2026}
            state = {}
            outputs = workflow.render_album(album, tracks, settings, lambda **changes: state.update(changes),
                                            review=True)
            work = album / state["work"]
            self.assertEqual(state["status"], "ready")
            self.assertFalse((album / "normalized").exists())
            self.assertEqual(outputs[0]["gain_db"], outputs[1]["gain_db"])
            self.assertLess(state["loudness"]["achieved_lufs"], -20)
            limit, = state["loudness"]["limits"]
            self.assertEqual(limit["track"], tracks[1].name)
            self.assertAlmostEqual(limit["time"], 3, delta=.12)
            self.assertGreater(limit["gain_shortfall_db"], 8)
            self.assertAlmostEqual(outputs[1]["true_peak"], -1.5, delta=.1)

            def approve():
                workflow.confirm_review(album, tracks, settings, work, state["review_assets"],
                                        outputs, lambda **changes: state.update(changes), {})

            lyrics = album / "lyrics" / f"{tracks[0].stem}.txt"
            workflow.write_track_text(album, tracks[0].stem, "lyrics", "Edited after review")
            with self.assertRaisesRegex(ValueError, "lyrics changed"):
                approve()
            lyrics.unlink()
            self.assertFalse((album / "normalized").exists())
            with tracks[0].open("ab") as handle:
                handle.write(b"changed")
            with self.assertRaisesRegex(ValueError, "Steady.wav changed"):
                approve()
            with tracks[0].open("r+b") as handle:
                handle.truncate(tracks[0].stat().st_size - len(b"changed"))

            reviewed = {relative: workflow.digest(work / relative)
                        for row in outputs for relative in row["files"]}
            approve()
            self.assertEqual(state["status"], "complete")
            self.assertEqual(reviewed, {relative: workflow.digest(album / relative) for relative in reviewed})
            self.assertEqual(original, {track: workflow.digest(track) for track in tracks})

    def test_ready_review_can_only_be_claimed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "job.db"
            workflow.record(db, "test", status="ready")
            self.assertEqual(workflow.record(db, "test", expected_status="ready", status="queued")["status"], "queued")
            self.assertFalse(workflow.record(db, "test", expected_status="ready", status="queued"))
