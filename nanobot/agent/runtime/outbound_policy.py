"""Outbound safety and media context policy for agent loops."""

import mimetypes
from pathlib import Path
from typing import Any

from nanobot.bus.events import OutboundMessage
from nanobot.utils.redaction import SensitiveOutputRedactor


class OutboundPolicy:
    """Encapsulates redaction, media normalization, and image follow-up state."""

    def __init__(
        self,
        *,
        workspace: Path,
        redactor: SensitiveOutputRedactor,
        recent_image_meta_key: str,
        recent_image_followup_turns: int,
    ):
        self.workspace = workspace
        self.redactor = redactor
        self.recent_image_meta_key = recent_image_meta_key
        self.recent_image_followup_turns = recent_image_followup_turns

    def redact_text(self, content: str | None) -> str:
        """Apply configured redaction to a text payload."""
        return self.redactor.redact(content or "")

    def normalize_media_paths(self, media: list[str] | None) -> list[str]:
        """
        Normalize outbound media paths to absolute paths.

        Preference order for relative paths:
        1) Current process working directory
        2) Agent workspace path (with special handling for "workspace/..." prefix)
        """
        if not media:
            return []

        normalized: list[str] = []
        for raw_path in media:
            if not isinstance(raw_path, str):
                continue

            text = raw_path.strip()
            if not text:
                continue

            try:
                p = Path(text).expanduser()
                candidates: list[Path] = []

                if p.is_absolute():
                    candidates.append(p)
                else:
                    candidates.append(Path.cwd() / p)

                    normalized_text = text.replace("\\", "/")
                    if normalized_text.startswith("workspace/"):
                        rel = normalized_text[len("workspace/"):]
                        if rel:
                            candidates.append(self.workspace / rel)

                    candidates.append(self.workspace / p)

                chosen = next(
                    (candidate.resolve(strict=False) for candidate in candidates if candidate.exists() and candidate.is_file()),
                    None,
                )

                if chosen is None:
                    if not p.is_absolute():
                        normalized_text = text.replace("\\", "/")
                        if normalized_text.startswith("workspace/"):
                            rel = normalized_text[len("workspace/"):]
                            if rel:
                                chosen = (self.workspace / rel).resolve(strict=False)
                    if chosen is None:
                        chosen = (p if p.is_absolute() else (self.workspace / p)).resolve(strict=False)

                normalized.append(str(chosen))
            except Exception:
                # Keep original value if normalization fails unexpectedly.
                normalized.append(text)

        return normalized

    def redact_outbound(self, msg: OutboundMessage) -> OutboundMessage:
        """Return a redacted outbound message copy."""
        return OutboundMessage(
            channel=msg.channel,
            chat_id=msg.chat_id,
            content=self.redact_text(msg.content),
            reply_to=msg.reply_to,
            media=self.normalize_media_paths(msg.media),
            metadata=msg.metadata,
        )

    @staticmethod
    def _ensure_session_metadata(session: Any) -> dict[str, Any]:
        """Return mutable metadata dict for a session, creating it if needed."""
        metadata = getattr(session, "metadata", None)
        if isinstance(metadata, dict):
            return metadata
        metadata = {}
        try:
            session.metadata = metadata
        except Exception:
            # Fallback for custom session objects without writable metadata.
            pass
        return metadata

    @staticmethod
    def _is_image_file(path: str) -> bool:
        """Check whether path points to a readable image file."""
        p = Path(path)
        if not p.exists() or not p.is_file():
            return False
        mime, _ = mimetypes.guess_type(str(p))
        return bool(mime and mime.startswith("image/"))

    def extract_latest_image(self, media: list[str] | None) -> str | None:
        """Pick the latest image path from a media list."""
        if not media:
            return None
        for candidate in reversed(media):
            if not isinstance(candidate, str):
                continue
            text = candidate.strip()
            if not text:
                continue
            if self._is_image_file(text):
                return str(Path(text).resolve(strict=False))
        return None

    def remember_recent_image(self, session: Any, image_path: str) -> None:
        """Store latest image for short follow-up reuse."""
        metadata = self._ensure_session_metadata(session)
        metadata[self.recent_image_meta_key] = {
            "path": image_path,
            "turns_left": self.recent_image_followup_turns,
        }

    def consume_recent_image(self, session: Any) -> str | None:
        """Reuse recent image for one turn and decrement remaining turns."""
        metadata = self._ensure_session_metadata(session)
        raw = metadata.get(self.recent_image_meta_key)
        if not isinstance(raw, dict):
            metadata.pop(self.recent_image_meta_key, None)
            return None

        path = raw.get("path")
        turns_left = raw.get("turns_left")
        if not isinstance(path, str) or not isinstance(turns_left, int) or turns_left <= 0:
            metadata.pop(self.recent_image_meta_key, None)
            return None

        if not self._is_image_file(path):
            metadata.pop(self.recent_image_meta_key, None)
            return None

        turns_left -= 1
        if turns_left <= 0:
            metadata.pop(self.recent_image_meta_key, None)
        else:
            metadata[self.recent_image_meta_key] = {"path": path, "turns_left": turns_left}

        return path

