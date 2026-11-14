import tempfile
import unittest
from pathlib import Path

from sonustemper.album_workflow import lyrics_draft, read_track_text, track_text_status, write_track_text


class AlbumTrackTextTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.album = Path(temporary.name)

    def test_shared_file_preserves_legacy_style_and_other_section(self):
        style_file = self.album / "styles" / "Song.md"
        style_file.parent.mkdir()
        original = "# Existing style\n\nVintage synths\n"
        style_file.write_text(original, encoding="utf-8")

        self.assertEqual(read_track_text(self.album, "Song")["style"], original)
        write_track_text(self.album, "Song", "negative", "Avoid clipping")
        self.assertEqual(style_file.read_text(encoding="utf-8"), original + "\n\n## Negative Prompt\nAvoid clipping")
        self.assertFalse((self.album / "negative-prompts").exists())
        self.assertEqual(track_text_status(self.album, ["Song.wav"])["Song.wav"],
                         {"style": True, "negative": True, "prompt": False, "lyrics": False})

        write_track_text(self.album, "Song", "style", "Acoustic guitar")
        self.assertEqual(read_track_text(self.album, "Song")["negative"], "Avoid clipping")
        write_track_text(self.album, "Song", "style", "")
        self.assertEqual(style_file.read_text(encoding="utf-8"), "## Negative Prompt\nAvoid clipping")
        write_track_text(self.album, "Song", "style", "Acoustic guitar")
        write_track_text(self.album, "Song", "negative", "")
        self.assertEqual(style_file.read_text(encoding="utf-8"), "Acoustic guitar")
        self.assertFalse(track_text_status(self.album, ["Song.wav"])["Song.wav"]["negative"])

    def test_lyric_prompt_and_reviewed_text_are_independent(self):
        source = "**Artist:** Artist\n## Lyrics - Suno v6\n[Verse 1]\n**turn** 2 times,\n[brief silence]\n[Final Chorus]\nhold on!\n(hold on)\n## Notes\nNot a lyric"
        draft = "Turn two times\n\nHold on!\n(Hold on)"
        self.assertEqual(lyrics_draft(source), draft)
        self.assertEqual(lyrics_draft(draft), draft)
        write_track_text(self.album, "Song", "lyrics", draft)
        write_track_text(self.album, "Song", "prompt", source)
        fields = read_track_text(self.album, "Song")
        self.assertEqual(fields["prompt"], source)
        self.assertEqual(fields["lyrics"], draft + "\n")
