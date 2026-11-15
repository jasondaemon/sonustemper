from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from mutagen.id3 import ID3

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
            lyric = "Hold on\n\nLook ahead"
            workflow.write_track_text(album, tracks[0].stem, "lyrics", lyric)
            workflow.write_track_text(album, tracks[1].stem, "prompt", "[Verse]\nNot reviewed")
            old_companion = album / "lyrics" / f"{tracks[0].stem}.lrc"
            old_companion.unlink()  # Legacy albums have reviewed text but no companion yet.
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
            first = work / "mp3s" / outputs[0]["mp3"]
            second = work / "mp3s" / outputs[1]["mp3"]
            self.assertEqual(first.with_suffix(".lrc").read_text(), lyric + "\n")
            self.assertEqual(ID3(first).getall("USLT")[0].text, lyric)
            self.assertFalse(old_companion.exists())
            self.assertEqual((work / "lyrics" / old_companion.name).read_text(), lyric + "\n")
            self.assertFalse(second.with_suffix(".lrc").exists())
            self.assertFalse(ID3(second).getall("USLT"))

            def approve():
                workflow.confirm_review(album, tracks, settings, work, state["review_assets"],
                                        outputs, lambda **changes: state.update(changes), {})

            workflow.write_track_text(album, tracks[0].stem, "lyrics", "Edited after review")
            with self.assertRaisesRegex(ValueError, "lyrics changed"):
                approve()
            workflow.write_track_text(album, tracks[0].stem, "lyrics", lyric)
            old_companion.unlink()
            self.assertFalse((album / "normalized").exists())
            with tracks[0].open("ab") as handle:
                handle.write(b"changed")
            with self.assertRaisesRegex(ValueError, "Steady.wav changed"):
                approve()
            with tracks[0].open("r+b") as handle:
                handle.truncate(tracks[0].stat().st_size - len(b"changed"))

            reviewed = {relative: workflow.digest(work / relative)
                        for row in outputs for relative in row["files"]}
            foreign = album / "mp3s" / first.with_suffix(".lrc").name
            foreign.parent.mkdir()
            foreign.write_text("[00:01.00]Hold on\n")
            with self.assertRaisesRegex(FileExistsError, "unmanaged lyrics"):
                approve()
            self.assertEqual(foreign.read_text(), "[00:01.00]Hold on\n")
            foreign.unlink()
            approve()
            self.assertEqual(state["status"], "complete")
            self.assertEqual(old_companion.read_text(), lyric + "\n")
            self.assertEqual(reviewed, {relative: workflow.digest(album / relative) for relative in reviewed})
            self.assertEqual(original, {track: workflow.digest(track) for track in tracks})

            workflow.write_track_text(album, tracks[0].stem, "lyrics", "")
            workflow.write_track_text(album, tracks[1].stem, "lyrics", "New reviewed lyrics")
            new_companion = album / "lyrics" / f"{tracks[1].stem}.lrc"
            new_companion.unlink()
            workflow.render_album(album, tracks, {**settings, "metadata_only": True},
                                  previous={row["track"]: row for row in outputs})
            self.assertFalse(foreign.exists())
            self.assertEqual((album / "mp3s" / second.with_suffix(".lrc").name).read_text(), "New reviewed lyrics\n")
            self.assertEqual(new_companion.read_text(), "New reviewed lyrics\n")

    def test_ready_review_can_only_be_claimed_once(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "job.db"
            workflow.record(db, "test", status="ready")
            self.assertEqual(workflow.record(db, "test", expected_status="ready", status="queued")["status"], "queued")
            self.assertFalse(workflow.record(db, "test", expected_status="ready", status="queued"))
