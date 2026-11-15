from pathlib import Path
import tempfile
import unittest

from sonustemper import album_layout as layout, album_workflow as workflow


class AlbumLayoutTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.album = Path(folder.name)

    def make(self, name, data=b"fixture"):
        path = self.album / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_flat_raw_is_readable_without_writes_and_can_be_rolled_back(self):
        source = self.make("Song.WAV")
        self.make("@eaDir/Hidden.wav")
        self.make("Stems/Voice.wav")
        self.make("cover.png")
        self.assertEqual(workflow.wavs(self.album, "raw"), [source])
        self.assertFalse((self.album / "00 - raw").exists())
        moves = layout.layout_plan(self.album)
        self.assertEqual(moves, [
            {"source": None, "target": "00 - raw"},
            {"source": "Song.WAV", "target": "00 - raw/Song.WAV"},
        ])
        inode = source.stat().st_ino
        layout.apply_layout(self.album, moves)
        organized, = workflow.wavs(self.album, "raw")
        self.assertEqual(organized.stat().st_ino, inode)
        self.assertEqual(organized.read_bytes(), b"fixture")
        self.assertEqual(layout.layout_plan(self.album), [])
        layout.rollback_layout(self.album, moves)
        self.assertEqual(workflow.wavs(self.album, "raw"), [source])
        self.assertEqual(source.stat().st_ino, inode)

    def test_mixed_layout_preserves_prior_logical_job_paths(self):
        self.make("raw/Song.wav")
        self.make("03 - normalized/Song.wav")
        self.make("mp3s/Song.mp3")
        self.make("lyrics/Song.txt")
        logical = "mp3s/Song.mp3"
        old = workflow.publication_path(self.album, logical)
        self.assertEqual(old.parent.name, "mp3s")
        stages = workflow.album_stages(self.album)
        self.assertEqual(stages["raw"], ["Song.wav"])
        self.assertTrue(workflow.delivery_complete(workflow.track_delivery(stages)["Song.wav"]))
        moves = layout.layout_plan(self.album)
        layout.apply_layout(self.album, moves)
        new = workflow.publication_path(self.album, logical)
        self.assertEqual(new.parent.name, "04 - mp3s")
        self.assertEqual(new.read_bytes(), b"fixture")
        self.assertFalse(old.exists())
        self.assertTrue((self.album / "lyrics" / "Song.txt").is_file())

    def test_collision_leaves_both_folders_intact_and_rolls_back_earlier_moves(self):
        raw = self.make("raw/Song.wav")
        clean = self.make("clean/Song.wav")
        moves = layout.layout_plan(self.album)
        different = self.make("02 - clean/Song.wav", b"different")
        with self.assertRaisesRegex(ValueError, "Both clean and 02 - clean exist"):
            layout.layout_plan(self.album)
        with self.assertRaises(FileExistsError):
            layout.apply_layout(self.album, moves)
        self.assertEqual(raw.read_bytes(), b"fixture")
        self.assertEqual(clean.read_bytes(), b"fixture")
        self.assertEqual(different.read_bytes(), b"different")
        self.assertFalse((self.album / "00 - raw").exists())
