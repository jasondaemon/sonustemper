import base64
import hashlib
import os
import time
import uuid
import mimetypes
import re
from pathlib import Path
from typing import Dict, List, Tuple, Optional
import zipfile

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
    APIC,
)


class TaggerService:
    """Backend-only MP3 tagger service (no subprocess usage)."""
    VOICING_TITLE_MAP = {
        "universal": "Voicing: Universal",
        "airlift": "Voicing: Airlift",
        "ember": "Voicing: Ember",
        "detail": "Voicing: Detail",
        "glue": "Voicing: Glue",
        "wide": "Voicing: Wide",
        "cinematic": "Voicing: Cinematic",
        "punch": "Voicing: Punch",
        "warm": "Voicing: Warm",
        "modern": "Voicing: Modern",
        "clean": "Voicing: Clean",
        "rock": "Voicing: Rock",
        "acoustic": "Voicing: Acoustic",
    }

    def __init__(
        self,
        out_dir: Path,
        tag_in_dir: Path,
        tag_tmp_dir: Path,
        max_upload_bytes: int = 250 * 1024 * 1024,
        max_artwork_bytes: int = 10 * 1024 * 1024,
    ):
        self.out_dir = out_dir
        self.tag_in_dir = tag_in_dir
        self.tag_tmp_dir = tag_tmp_dir
        self.max_upload_bytes = max_upload_bytes
        self.max_artwork_bytes = max_artwork_bytes
        for d in (self.out_dir, self.tag_in_dir, self.tag_tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
        # subdirs for uploads/temps
        self.artwork_tmp_dir = self.tag_tmp_dir / "artwork"
        self.zip_tmp_dir = self.tag_tmp_dir / "zip"
        for d in (self.artwork_tmp_dir, self.zip_tmp_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.roots: Dict[str, Path] = {
            "out": self.out_dir,
            "tag": self.tag_in_dir,
        }
        self._index: Dict[str, Dict] = {}
        self._upload_cache: Dict[str, Dict] = {}

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

    def _parse_badges(self, basename: str, root: str | None = None) -> Tuple[str, List[Dict]]:
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
                # voicing split form: "V", "<name>"
                if t == "V" and (i + 1) < len(tokens):
                    vname = tokens[i + 1]
                    lbl = f"V_{vname}"
                    slug = vname.lower()
                    title = self.VOICING_TITLE_MAP.get(slug, f"Voicing: {vname.replace('_',' ').title()}")
                    badges.append({"type": "voicing", "label": lbl, "title": title})
                    i += 2
                    continue
                # voicing / source
                if t.startswith("V_") or t == "source":
                    lbl = t
                    if t == "source":
                        title = "Source"
                    else:
                        slug = t[2:].lower()
                        title = self.VOICING_TITLE_MAP.get(slug, f"Voicing: {slug.replace('_',' ').title()}")
                    badges.append({"type": "voicing", "label": lbl, "title": title})
                    i += 1
                    continue
                # strength
                if t.startswith("S") and t[1:].isdigit():
                    badges.append({"type": "param", "label": t, "title": f"Strength: {t[1:]}"})
                    i += 1
                    continue
                # preset/chain
                if t == "LMCustom":
                    badges.append({"type": "preset", "label": t, "title": f"Preset: {t}"})
                    i += 1
                    continue
                # TI / TTP / W / GR
                if (t.startswith("TI-") and t[3:].replace(".","",1).replace("-","",1).lstrip("0").isdigit()) or t.startswith("TI-"):
                    val = t[3:] if t.startswith("TI-") else t
                    badges.append({"type": "param", "label": t, "title": f"Time/Intensity: {val}"})
                    i += 1
                    continue
                if t.startswith("TTP-"):
                    badges.append({"type": "param", "label": t, "title": f"True Peak Target: {t.replace('TTP-','-')} dBTP"})
                    i += 1
                    continue
                if t.startswith("W") and t[1:]:
                    badges.append({"type": "param", "label": t, "title": f"Weight: {t[1:]}"} )
                    i += 1
                    continue
                if t.startswith("GR") and t[2:]:
                    badges.append({"type": "param", "label": t, "title": f"Gain Reduction: {t[2:]}"})
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
                    title = f"Source Format: {rate} kHz" + (f" / {bit}-bit" if bit else "")
                    badges.append({"type": "format", "label": lbl, "title": title})
                    i += 1
                    continue
                # MP3 with bitrate
                if t == "MP3":
                    lbl = "MP3"
                    if (i + 1) < len(tokens) and tokens[i + 1].upper().startswith("CBR"):
                        br = tokens[i + 1].upper().replace("CBR", "")
                        lbl = f"MP3 {br}"
                        i += 1  # consume bitrate token
                    title = f"Output Format: {lbl} kbps (CBR)" if " " in lbl else f"Output Format: {lbl}"
                    badges.append({"type": "format", "label": lbl, "title": title})
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
                    title = f"Output Format: {lbl}"
                    badges.append({"type": "format", "label": lbl, "title": title})
                    i += 1
                    continue
                i += 1
        # container/ext (optional subtle)
        if ext:
            badges.append({"type": "container", "label": ext, "title": f"Container: {ext}"})
        # Ensure at least one voicing/preset badge exists (fallback by root)
        if not any(b.get("type") in ("voicing", "preset") for b in badges):
            if root == "out":
                badges.insert(0, {"type": "preset", "label": "Mastered", "title": "Mastered output"})
            elif root == "tag":
                badges.insert(0, {"type": "preset", "label": "Imported", "title": "Imported MP3"})
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
                    display_title, badges = self._parse_badges(p.name, root_key)
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
        # Always rescan so display_title/badges stay fresh
        self._scan()
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

    # ---------------------- artwork helpers ----------------------
    @staticmethod
    def _infer_mime(data: bytes, fallback: Optional[str] = None) -> str:
        if data.startswith(b"\x89PNG\r\n\x1a\n"):
            return "image/png"
        if data[0:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if fallback and fallback.lower() in {"image/png", "image/jpeg", "image/jpg"}:
            return fallback.lower()
        raise HTTPException(status_code=400, detail="unsupported_artwork_format")

    def _read_artwork_bytes(self, path: Path) -> Tuple[bool, Optional[bytes], Optional[str]]:
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            return False, None, None
        apics = [f for k, f in id3.items() if k.startswith("APIC")]
        if not apics:
            return False, None, None
        frame: APIC = apics[0]
        return True, frame.data, frame.mime or None

    def read_artwork_info(self, file_id: str) -> Dict:
        _, path = self.resolve_id(file_id)
        present, _, mime = self._read_artwork_bytes(path)
        return {"present": bool(present), "mime": mime}

    def get_artwork(self, file_id: str) -> Tuple[bytes, str]:
        _, path = self.resolve_id(file_id)
        present, data, mime = self._read_artwork_bytes(path)
        if not present or data is None:
            raise HTTPException(status_code=404, detail="artwork_not_found")
        mime = mime or self._infer_mime(data)
        return data, mime

    def set_artwork(self, file_id: str, data: bytes, mime: Optional[str]) -> Dict:
        if not data:
            raise HTTPException(status_code=400, detail="missing_artwork")
        if len(data) > self.max_artwork_bytes:
            raise HTTPException(status_code=413, detail="artwork_too_large")
        mime = self._infer_mime(data, mime)
        entry, path = self.resolve_id(file_id)
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            id3 = ID3()
        # remove old APIC frames
        try:
            id3.delall("APIC")
        except Exception:
            for k in list(id3.keys()):
                if k.startswith("APIC"):
                    try:
                        id3.pop(k, None)
                    except Exception:
                        pass
        apic = APIC(
            encoding=3,
            mime=mime,
            type=3,
            desc="Cover",
            data=data,
        )
        id3.add(apic)
        try:
            id3.save(path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed_to_write_artwork: {exc}") from exc
        return {
            "id": entry["id"],
            "artwork": {"present": True, "mime": mime},
        }

    def clear_artwork(self, file_id: str) -> Dict:
        entry, path = self.resolve_id(file_id)
        try:
            id3 = ID3(path)
        except ID3NoHeaderError:
            return {"id": entry["id"], "artwork": {"present": False}}
        try:
            id3.delall("APIC")
        except Exception:
            for k in list(id3.keys()):
                if k.startswith("APIC"):
                    try:
                        id3.pop(k, None)
                    except Exception:
                        pass
        try:
            id3.save(path)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"failed_to_clear_artwork: {exc}") from exc
        return {"id": entry["id"], "artwork": {"present": False}}

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

    # ---------------------- artwork uploads + album helpers ----------------------
    def _cleanup_tmp(self, ttl_sec: int = 3600) -> None:
        cutoff = time.time() - ttl_sec
        for directory in (self.artwork_tmp_dir, self.zip_tmp_dir):
            if not directory.exists():
                continue
            for p in directory.iterdir():
                try:
                    if p.is_file() and p.stat().st_mtime < cutoff:
                        p.unlink()
                except Exception:
                    continue
        # purge stale upload cache
        for k, meta in list(self._upload_cache.items()):
            if meta.get("ts", 0) < cutoff:
                self._upload_cache.pop(k, None)

    async def upload_artwork(self, upload: UploadFile) -> Dict:
        if not upload or not upload.filename:
            raise HTTPException(status_code=400, detail="missing_file")
        data = await upload.read(self.max_artwork_bytes + 1)
        if len(data) > self.max_artwork_bytes:
            raise HTTPException(status_code=413, detail="artwork_too_large")
        mime = self._infer_mime(data, upload.content_type)
        uid = uuid.uuid4().hex
        dest = self.artwork_tmp_dir / f"{uid}.bin"
        try:
            dest.write_bytes(data)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"artwork_write_failed: {exc}") from exc
        self._upload_cache[uid] = {"path": dest, "mime": mime, "ts": time.time()}
        self._cleanup_tmp()
        return {"upload_id": uid, "mime": mime, "size": len(data)}

    def _load_artwork_upload(self, upload_id: str) -> Tuple[bytes, str]:
        if not upload_id:
            raise HTTPException(status_code=400, detail="missing_artwork_upload")
        meta = self._upload_cache.get(upload_id)
        if not meta:
            candidate = self.artwork_tmp_dir / f"{upload_id}.bin"
            if candidate.exists():
                meta = {"path": candidate, "mime": None, "ts": candidate.stat().st_mtime}
                self._upload_cache[upload_id] = meta
        if not meta:
            raise HTTPException(status_code=404, detail="artwork_upload_not_found")
        try:
            data = Path(meta["path"]).read_bytes()
        except Exception as exc:
            raise HTTPException(status_code=404, detail="artwork_upload_not_found") from exc
        mime = meta.get("mime") or self._infer_mime(data)
        return data, mime

    @staticmethod
    def _validate_trackdisc(val: Optional[str], field: str) -> Optional[str]:
        if val is None:
            return None
        if isinstance(val, (int, float)):
            val = str(val)
        if not isinstance(val, str):
            raise HTTPException(status_code=400, detail=f"invalid_{field}")
        val = val.strip()
        if not val:
            return None
        if not re.match(r"^[0-9]+(\/[0-9]+)?$", val):
            raise HTTPException(status_code=400, detail=f"invalid_{field}")
        return val

    def apply_album(
        self,
        file_ids: List[str],
        shared: Dict,
        tracks: List[Dict],
        artwork_mode: str = "keep",
        artwork_upload_id: Optional[str] = None,
    ) -> Dict:
        if not file_ids:
            raise HTTPException(status_code=400, detail="no_files_selected")
        if len(file_ids) > 200:
            raise HTTPException(status_code=400, detail="too_many_files")
        # map track overrides
        track_map = {t.get("id"): t for t in (tracks or []) if t.get("id")}
        # optional artwork
        art_bytes = None
        art_mime = None
        if artwork_mode == "apply":
            art_bytes, art_mime = self._load_artwork_upload(artwork_upload_id) if artwork_upload_id else (None, None)
            if not art_bytes:
                raise HTTPException(status_code=400, detail="artwork_missing")

        updated = []
        errors = []
        for fid in file_ids:
            try:
                entry, path = self.resolve_id(fid)
                tag_payload: Dict[str, str] = {}
                for key in ("album", "album_artist", "artist", "year", "genre", "comment"):
                    if shared and key in shared:
                        tag_payload[key] = self._clean_str(shared.get(key))
                if shared.get("disc"):
                    tag_payload["disc"] = self._validate_trackdisc(shared.get("disc"), "disc")
                per_track = track_map.get(fid, {})
                if "title" in per_track:
                    tag_payload["title"] = self._clean_str(per_track.get("title"))
                if "track" in per_track:
                    tag_payload["track"] = self._validate_trackdisc(per_track.get("track"), "track")
                if per_track.get("artist"):
                    tag_payload["artist"] = self._clean_str(per_track["artist"])
                if per_track.get("disc"):
                    tag_payload["disc"] = self._validate_trackdisc(per_track.get("disc"), "disc")
                # write tags
                tags = self.write_tags(path, tag_payload)
                # artwork handling
                if artwork_mode == "clear":
                    art_info = self.clear_artwork(fid)
                elif artwork_mode == "apply" and art_bytes:
                    art_info = self.set_artwork(fid, art_bytes, art_mime)
                else:
                    art_info = {"artwork": {"present": tags.get("artwork", {}).get("present", False)}}
                updated.append(
                    {
                        "id": fid,
                        "basename": entry["basename"],
                        "tags": tags,
                        "artwork": art_info.get("artwork", {"present": False}),
                    }
                )
            except HTTPException as exc:
                errors.append({"id": fid, "error": exc.detail})
            except Exception as exc:
                errors.append({"id": fid, "error": str(exc)})
        self._cleanup_tmp()
        return {"updated": updated, "errors": errors}

    def album_download(self, file_ids: List[str], album_name: str | None = None) -> Path:
        if not file_ids:
            raise HTTPException(status_code=400, detail="no_files_selected")
        if len(file_ids) > 200:
            raise HTTPException(status_code=400, detail="too_many_files")
        # build zip
        safe_album = (album_name or "album").strip() or "album"
        safe_album = re.sub(r"[^A-Za-z0-9 _.-]+", "", safe_album)[:100]
        zip_path = self.zip_tmp_dir / f"{uuid.uuid4().hex}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for fid in file_ids:
                    try:
                        entry, path = self.resolve_id(fid)
                        tags = self.read_tags(path)
                        base = entry["basename"]
                        track = tags.get("track") or ""
                        title = tags.get("title") or Path(base).stem
                        title = re.sub(r"[^A-Za-z0-9 _.-]+", "", title)[:128]
                        fname = f"{title}.mp3"
                        if "/" in track or track.isdigit():
                            num = track.split("/")[0] if "/" in track else track
                            if num.isdigit():
                                fname = f"{int(num):02d} - {title}.mp3"
                        zf.write(path, arcname=fname)
                    except Exception:
                        continue
        except Exception as exc:
            if zip_path.exists():
                try:
                    zip_path.unlink()
                except Exception:
                    pass
            raise HTTPException(status_code=500, detail=f\"zip_failed: {exc}\") from exc
        self._cleanup_tmp()
        return zip_path
