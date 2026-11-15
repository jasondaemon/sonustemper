import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from sonustemper.album_workflow import render_album, wavs


def riff_chunk(kind, payload):
    return struct.pack("<4sI", kind, len(payload)) + payload + b"\0" * (len(payload) % 2)


def riff_chunks(wav):
    if wav[:4] != b"RIFF" or wav[8:12] != b"WAVE":
        raise ValueError("Expected a RIFF WAV")
    chunks = []
    offset = 12
    while offset < len(wav):
        kind, length = struct.unpack_from("<4sI", wav, offset)
        chunks.append(kind)
        offset += 8 + length + length % 2
    return chunks


@unittest.skipUnless(shutil.which("ffmpeg"), "FFmpeg is required")
class AlbumWavContainerTests(unittest.TestCase):
    def test_final_wav_does_not_inherit_source_metadata_chunks(self):
        with tempfile.TemporaryDirectory() as directory:
            album = Path(directory)
            source = album / "Song.wav"
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "sine=d=1:r=48000",
                "-ac", "2", "-c:a", "pcm_s24le", "-metadata", "comment=Suno fixture",
                "-metadata", "description=Source metadata", "-write_bext", "1", str(source),
            ], check=True, capture_output=True)
            content = source.read_bytes()
            chunks = riff_chunk(b"iXML", b"<SOURCE>Suno</SOURCE>") + riff_chunk(b"suno", b"private fixture data")
            raw = b"RIFF" + struct.pack("<I", len(content) + len(chunks) - 8) + content[8:12] + chunks + content[12:]
            source.write_bytes(raw)
            self.assertTrue({b"LIST", b"bext", b"iXML", b"suno"}.issubset(riff_chunks(raw)))

            result, = render_album(album, wavs(album, "raw"), {
                "source": "raw", "filters": "volume=-3dB", "balance": "album",
                "lufs": -19, "peak": -1.5, "year": 2026,
            })
            final = (album / "03 - normalized" / result["wav"]).read_bytes()
            self.assertEqual(riff_chunks(final), [b"fmt ", b"data"])
            self.assertEqual(result["lufs"], -19)
            self.assertFalse(source.exists())
            self.assertEqual((album / "00 - raw" / source.name).read_bytes(), raw)
