import base64
import hashlib
import os
from pathlib import Path
from typing import Dict, List, Tuple

from fastapi import HTTPException, UploadFile
from mutagen.id3 import (
    ID3,
    ID3NoHeaderError,
    TIT2,
    TALB,
    TPE1,
    TPE2,
    TRCK,
    TPOS,
    TDRC,
    TCON,
    COMM,
)


class TaggerService:
    """Backend-only MP3 tagger service (no subprocess usage)."""

    def __init__(
        self,
        out_dir: Path,
        tag_in_dir: Path,
        tag_tmp_dir: Path,
        max_upload_bytes: int = 250 * 1024 * 1024,
    ):
        self.out_dir = out_dir
        self.tag_in_dir = tag_in_dir
        self.tag_tmp_dir = tag_tmp_dir
        self.max_upload_bytes = max_upload_bytes
        for d in (self.out_dir, self.tag_in_dir, self.tag_tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.roots: Dict[str, Path] = {
            "out": self.out_dir,
            "tag": self.tag_in_dir,
        }
        self._index: Dict[str, Dict] = {}

    # ---------------------- indexing + path resolution ----------------------
    @staticmethod
    def _safe_rel(root: Path, candidate: Path) -> Path:
        resolved_root = root.resolve()
        resolved = candidate.resolve()
        if resolved_root == resolved:
            raise HTTPException(status_code=400, detail="invalid_path")
        if resolved_root not in resolved.parents:
            raise HTTPException(status_code=400, detail="invalid_path")
        return resolved.relative_to(resolved_root)

    @staticmethod
    def _make_id(root_key: str, relpath: Path, size: int, mtime: float) -> str:
        raw = f"{root_key}:{relpath.as_posix()}:{size}:{mtime}".encode("utf-8")
        digest = hashlib.sha256(raw).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _parse_badges(basename: str) -> Tuple[str, List[Dict]]:
        stem = Path(basename).stem
        ext = Path(basename).suffix.lower().lstrip(".")
        display_title = stem
        badges: List[Dict] = []
        if "__" not in stem:
            display_title = stem.replace("_", " ").strip() or stem
        else:
            title_part, suffix = stem.split("__", 1)
            display_title = title_part.replace("_", " ").strip() or title_part
            tokens = [t for t in suffix.split("_") if t]
            i = 0
            while i < len(tokens):
                t = tokens[i]
                # voicing / source
                if t.startswith("V_") or t == "source":
                    badges.append({"type": "voicing", "label": t})
                    i += 1
                    continue
                # strength
                if t.startswith("S") and t[1:].isdigit():
                    badges.append({"type": "param", "label": t})
                    i += 1
                    continue
                # preset/chain
                if t == "LMCustom":
                    badges.append({"type": "param", "label": t})
                    i += 1
                    continue
                # TI / TTP / W / GR
                if (t.startswith("TI-") and t[3:].replace(".","",1).replace("-","",1).lstrip("0").isdigit()) or t.startswith("TI-"):
                    badges.append({"type": "param", "label": t})
                    i += 1
                    continue
                if t.startswith("TTP-"):
                    badges.append({"type": "param", "label": t})
                    i += 1
                    continue
                if t.startswith("W") and t[1:]:
                    badges.append({"type": "param", "label": t})
                    i += 1
                    continue
                if t.startswith("GR") and t[2:]:
                    badges.append({"type": "param", "label": t})
                    i += 1
                    continue
                # sample/bit pair
                if t.upper().startswith("WAV") and t[3:-1].isdigit():
                    rate = t[3:].rstrip("kK")
                    bit = None
                    if (i + 1) < len(tokens) and tokens[i + 1].isdigit():
                        bit = tokens[i + 1]
                        i += 1  # consume bit token
                    lbl = f"{rate}k/{bit}" if bit else f"{rate}k"
                    badges.append({"type": "format", "label": lbl})
                    i += 1
                    continue
                # MP3 with bitrate
                if t == "MP3":
                    lbl = "MP3"
                    if (i + 1) < len(tokens) and tokens[i + 1].upper().startswith("CBR"):
                        br = tokens[i + 1].upper().replace("CBR", "")
                        lbl = f"MP3 {br}"
                        i += 1  # consume bitrate token
                    badges.append({"type": "format", "label": lbl})
                    i += 1
                    continue
                # AAC with bitrate
                if t.upper().startswith("AAC"):
                    lbl = "AAC"
                    nxt = tokens[i + 1] if (i + 1) < len(tokens) else ""
                    if "_" in t:
                        parts = t.split("_", 1)
                        if len(parts) == 2 and parts[1].isdigit():
                            lbl = f"AAC {parts[1]}"
                    elif nxt.isdigit():
                        lbl = f"AAC {nxt}"
                        i += 1
                    badges.append({"type": "format", "label": lbl})
                    i += 1
                    continue
                i += 1
        # container/ext (optional subtle)
        if ext:
            badges.append({"type": "container", "label": ext})
        return display_title or stem, badges

    def _scan(self) -> None:
        """Rebuild the in-memory index of available MP3 files."""
        self._index = {}
        for root_key, root_path in self.roots.items():
            if not root_path.exists():
                continue
            for p in root_path.rglob("*.mp3"):
                if not p.is_file():
                    continue
                try:
                    rel = self._safe_rel(root_path, p)
                    stat = p.stat()
                    fid = self._make_id(root_key, rel, stat.st_size, stat.st_mtime)
                    display_title, badges = self._parse_badges(p.name)
                    self._index[fid] = {
                        "id": fid,
                        "root": root_key,
                        "relpath": rel.as_posix(),
                        "basename": p.name,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime,
                        "display_title": display_title,
                        "badges": badges,
                    }
                except HTTPException:
                    continue
                except Exception:
                    continue

    def _ensure_index(self) -> None:
        if not self._index:
            self._scan()

    def list_mp3s(self, scope: str = "all") -> List[Dict]:
        scope = (scope or "all").lower()
        if scope not in {"all", "out", "tag"}:
            raise HTTPException(status_code=400, detail="invalid_scope")
        self._ensure_index()
        items = []
        for entry in self._index.values():
            if scope != "all" and entry["root"] != scope:
                continue
            items.append(
                {
                    "id": entry["id"],
                    "root": entry["root"],
                    "basename": entry["basename"],
                    "relpath": entry["relpath"],
                    "display_title": entry.get("display_title") or entry["basename"],
                    "badges": entry.get("badges") or [],
                    "full_name": entry["relpath"],
                }
            )
        items.sort(key=lambda e: (e["root"], e["relpath"]))
        return items

    def resolve_id(self, file_id: str) -> Tuple[Dict, Path]:
        self._ensure_index()
        entry = self._index.get(file_id)
        if not entry:
            # Rescan once in case the file is new
            self._scan()
            entry = self._index.get(file_id)
        if not entry:
            raise HTTPException(status_code=404, detail="file_not_found")
        root_path = self.roots.get(entry["root"])
        if not root_path:
            raise HTTPException(status_code=404, detail="file_not_found")
        try:
            rel = Path(entry["relpath"])
            path = (root_path / rel).resolve()
            _ = self._safe_rel(root_path, path)
        except HTTPException:
            raise HTTPException(status_code=404, detail="file_not_found")
        if not path.exists() or not path.is_file() or path.suffix.lower() != ".mp3":
            raise HTTPException(status_code=404, detail="file_not_found")
        return entry, path

    # ---------------------- tag read/write helpers ----------------------
    @staticmethod
    def _clean_str(val, max_len: int = 512) -> str | None:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            val = str(val)
        if not isinstance(val, str):
            raise HTTPException(status_code=400, detail="invalid_tag_value")
        val = val.strip()
        if not val:
            return None
        if len(val) > max_len:
            val = val[:max_len]
        return val

    def read_tags(self, path: Path) -> Dict:
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            return {
                "title": None,
                "artist": None,
                "album": None,
                "album_artist": None,
                "track": None,
                "disc": None,
                "year": None,
                "genre": None,
                "comment": None,
                "artwork": {"present": False},
            }
        def txt(frame_id: str) -> str | None:
            frame = id3.get(frame_id)
            if not frame:
                return None
            val = getattr(frame, "text", None)
            if isinstance(val, list) and val:
                return str(val[0])
            if isinstance(val, str):
                return val
            return None
        comment_frame = id3.get("COMM::'eng'") or id3.get("COMM::eng") or id3.get("COMM")
        comment = None
        if comment_frame and getattr(comment_frame, "text", None):
            txt_val = comment_frame.text
            if isinstance(txt_val, list) and txt_val:
                comment = str(txt_val[0])
            elif isinstance(txt_val, str):
                comment = txt_val
        artwork_present = any(k.startswith("APIC") for k in id3.keys())
        return {
            "title": txt("TIT2"),
            "artist": txt("TPE1"),
            "album": txt("TALB"),
            "album_artist": txt("TPE2"),
            "track": txt("TRCK"),
            "disc": txt("TPOS"),
            "year": txt("TDRC"),
            "genre": txt("TCON"),
            "comment": comment,
            "artwork": {"present": bool(artwork_present)},
        }

    def write_tags(self, path: Path, tags: Dict) -> Dict:
        if tags is None or not isinstance(tags, dict):
            raise HTTPException(status_code=400, detail="invalid_payload")
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
        fields = {
            "title": ("TIT2", TIT2),
            "artist": ("TPE1", TPE1),
            "album": ("TALB", TALB),
            "album_artist": ("TPE2", TPE2),
            "track": ("TRCK", TRCK),
            "disc": ("TPOS", TPOS),
            "year": ("TDRC", TDRC),
            "genre": ("TCON", TCON),
        }
        for key, (fid, ctor) in fields.items():
            if key not in tags:
                continue
            val = self._clean_str(tags.get(key))
            if val is None:
                try:
                    id3.delall(fid)
                except Exception:
                    pass
                continue
            try:
                id3.setall(fid, [ctor(encoding=3, text=[val])])
            except Exception:
                id3.setall(fid, [ctor(encoding=3, text=[val])])
        if "comment" in tags:
            val = self._clean_str(tags.get("comment"))
            if val is None:
                try:
                    id3.delall("COMM")
                except Exception:
                    pass
            else:
                id3.setall("COMM", [COMM(encoding=3, lang="eng", desc="", text=[val])])
        try:
            id3.save(path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed_to_write_tags: {exc}") from exc
        return self.read_tags(path)

    # ---------------------- uploads + downloads ----------------------
    def _safe_filename(self, name: str) -> str:
        if not name:
            raise HTTPException(status_code=400, detail="missing_filename")
        safe = Path(name).name
        if not safe.lower().endswith(".mp3"):
            raise HTTPException(status_code=400, detail="only_mp3_allowed")
        if not safe:
            raise HTTPException(status_code=400, detail="invalid_filename")
        if ".." in safe:
            raise HTTPException(status_code=400, detail="invalid_filename")
        return safe

    async def import_mp3(self, upload: UploadFile) -> Dict:
        if not upload or not upload.filename:
            raise HTTPException(status_code=400, detail="missing_file")
        safe_name = self._safe_filename(upload.filename)
        dest = (self.tag_in_dir / safe_name).resolve()
        if dest.exists():
            stem = dest.stem
            idx = 1
            while True:
                candidate = dest.with_name(f"{stem}-{idx}{dest.suffix}")
                if not candidate.exists():
                    dest = candidate
                    break
                idx += 1
        if self.tag_in_dir.resolve() not in dest.parents:
            raise HTTPException(status_code=400, detail="invalid_path")
        bytes_written = 0
        try:
            with dest.open("wb") as fh:
                while True:
                    chunk = await upload.read(1024 * 1024)
                    if not chunk:
                        break
                    bytes_written += len(chunk)
                    if bytes_written > self.max_upload_bytes:
                        raise HTTPException(status_code=413, detail="file_too_large")
                    fh.write(chunk)
        except HTTPException:
            if dest.exists():
                try:
                    dest.unlink()
                except Exception:
                    pass
            raise
        except Exception as exc:
            if dest.exists():
                try:
                    dest.unlink()
                except Exception:
                    pass
            raise HTTPException(status_code=500, detail=f"upload_failed: {exc}") from exc
        # refresh index and return entry
        self._scan()
        for entry in self._index.values():
            if entry["root"] == "tag" and entry["basename"] == dest.name:
                return {
                    "id": entry["id"],
                    "root": entry["root"],
                    "basename": entry["basename"],
                    "relpath": entry["relpath"],
                }
        raise HTTPException(status_code=500, detail="upload_not_indexed")

    # ---------------------- public API helpers ----------------------
    def get_file_payload(self, file_id: str) -> Dict:
        entry, path = self.resolve_id(file_id)
        tags = self.read_tags(path)
        return {
            "id": entry["id"],
            "root": entry["root"],
            "basename": entry["basename"],
            "relpath": entry["relpath"],
            "tags": tags,
        }

    def update_file_tags(self, file_id: str, tags: Dict) -> Dict:
        entry, path = self.resolve_id(file_id)
        updated = self.write_tags(path, tags)
        return {
            "id": entry["id"],
            "root": entry["root"],
            "basename": entry["basename"],
            "relpath": entry["relpath"],
            "tags": updated,
        }

    def download_file(self, file_id: str) -> Tuple[Path, str]:
        entry, path = self.resolve_id(file_id)
        return path, entry["basename"]
