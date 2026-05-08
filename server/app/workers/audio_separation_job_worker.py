from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import logging
import mimetypes
import re
import shlex
import shutil
import subprocess
import tempfile

from app.core.config import Settings
from app.core.errors import DownloadAppError
from app.domain.models import ArtifactRole, JobStatus
from app.services.repositories import ArtifactRepository, JobRepository

logger = logging.getLogger(__name__)

_ARTIFACT_INVALID_CHARS_RE = re.compile(r"[\x00-\x1f\\/:*?\"<>|]+")
_ARTIFACT_WHITESPACE_RE = re.compile(r"\s+")
_ALLOWED_AUDIO_OUTPUT_EXTENSIONS = frozenset({"mp3", "wav", "m4a", "aac", "flac"})


class AudioSeparationEngine:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def separate(self, *, input_path: Path, output_dir: Path) -> tuple[Path, Path]:
        args = self._build_args(input_path=input_path, output_dir=output_dir)
        subprocess.run(args, check=True, timeout=self._settings.audio_separation_timeout_seconds)
        vocals = self._find_output(output_dir, "vocals", excluded_keywords={"no_vocals"})
        accompaniment = self._find_output(output_dir, "accompaniment", "no_vocals")
        if vocals is None or accompaniment is None:
            raise DownloadAppError("audio separation outputs were not produced", "音频分离没有生成完整结果。")
        return vocals, accompaniment

    def _build_args(self, *, input_path: Path, output_dir: Path) -> list[str]:
        command = self._settings.audio_separation_command.strip()
        if not command:
            raise DownloadAppError("audio separation command is not configured", "未配置音频分离工具。")
        replacements = {
            "{input}": str(input_path),
            "{output_dir}": str(output_dir),
            "{input:q}": shlex.quote(str(input_path)),
            "{output_dir:q}": shlex.quote(str(output_dir)),
        }
        for placeholder, value in replacements.items():
            command = command.replace(placeholder, value)
        return shlex.split(command)

    def _find_output(self, output_dir: Path, *keywords: str, excluded_keywords: set[str] | None = None) -> Path | None:
        excluded_keywords = excluded_keywords or set()
        for path in output_dir.rglob("*"):
            stem = path.stem.lower()
            if path.is_file() and any(keyword in stem for keyword in keywords) and not any(keyword in stem for keyword in excluded_keywords):
                return path
        return None


class AudioSeparationJobWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        jobs: JobRepository,
        artifacts: ArtifactRepository,
        engine: AudioSeparationEngine | None = None,
    ) -> None:
        self._settings = settings
        self._jobs = jobs
        self._artifacts = artifacts
        self._engine = engine or AudioSeparationEngine(settings)

    def run(self, job_id: str) -> None:
        job = self._jobs.get(job_id)
        if job is None:
            return
        input_path = Path(job.normalized_url.removeprefix("file:"))
        created_artifact_ids: list[str] = []
        should_cleanup_input = False
        try:
            if not self._transition_status_with_event(
                job_id,
                from_statuses={JobStatus.QUEUED},
                to_status=JobStatus.RESOLVING,
                progress=5,
                message="开始读取音频文件",
            ):
                return
            if not input_path.exists() or not input_path.is_file():
                raise DownloadAppError("audio input file is missing", "音频输入文件不存在。")
            if not self._transition_status_with_event(
                job_id,
                from_statuses={JobStatus.RESOLVING},
                to_status=JobStatus.MUXING,
                progress=35,
                provider="audio_separation",
                message="开始拆分人声和伴奏",
            ):
                return
            with tempfile.TemporaryDirectory() as temp_dir:
                vocals_source, accompaniment_source = self._engine.separate(input_path=input_path, output_dir=Path(temp_dir))
                if not self._transition_status_with_event(
                    job_id,
                    from_statuses={JobStatus.MUXING},
                    to_status=JobStatus.STORING,
                    progress=90,
                    provider="audio_separation",
                    message="正在保存拆分结果",
                ):
                    return
                vocals = self._store_output(job_id, job.media_title or job_id, ArtifactRole.VOCALS, vocals_source)
                accompaniment = self._store_output(job_id, job.media_title or job_id, ArtifactRole.ACCOMPANIMENT, accompaniment_source)
                created_artifact_ids.extend([vocals.id, accompaniment.id])
                completed_size = vocals.file_size + accompaniment.file_size
                if self._transition_status_with_event(
                    job_id,
                    from_statuses={JobStatus.STORING},
                    to_status=JobStatus.COMPLETED,
                    progress=100,
                    provider="audio_separation",
                    downloaded_bytes=completed_size,
                    total_bytes=completed_size,
                    artifact_id=accompaniment.id,
                    finished_at=datetime.now(tz=UTC),
                    message="人声和伴奏已保存",
                ):
                    should_cleanup_input = True
        except DownloadAppError as exc:
            self._mark_failed(job_id, exc.message, exc.user_message)
            for artifact_id in created_artifact_ids:
                self._delete_artifact(artifact_id)
        except Exception:
            logger.exception("audio separation job failed unexpectedly job_id=%s", job_id)
            self._mark_failed(job_id, "unexpected audio separation error", "音频分离失败，请稍后重试。")
            for artifact_id in created_artifact_ids:
                self._delete_artifact(artifact_id)
        finally:
            current = self._jobs.get(job_id)
            if should_cleanup_input or current is not None and current.status == JobStatus.CANCELED:
                self._cleanup_file(input_path)

    def _transition_status_with_event(self, job_id: str, *, message: str, **kwargs) -> bool:
        changed = self._jobs.transition_status(job_id, **kwargs)
        if changed:
            self._jobs.create_event(job_id, level="info", event_type=kwargs["to_status"].value, message=message)
        return changed

    def _store_output(self, job_id: str, title: str, role: ArtifactRole, source: Path):
        ext = self._normalize_audio_output_extension(source.suffix.lstrip(".") or "wav")
        output_path = self._allocate_artifact_path(
            stem=f"{self._safe_artifact_stem(title, fallback=job_id)}.{role.value}",
            ext=ext,
            directory=self._role_output_dir(role),
        )
        shutil.move(str(source), output_path)
        return self._artifacts.create(
            job_id=job_id,
            file_name=output_path.name,
            mime_type=mimetypes.guess_type(output_path.name)[0] or "audio/wav",
            storage_path=str(output_path),
            file_size=output_path.stat().st_size,
            role=role,
        )

    def _safe_artifact_stem(self, title: str | None, *, fallback: str) -> str:
        cleaned = _ARTIFACT_INVALID_CHARS_RE.sub(" ", title or "")
        cleaned = _ARTIFACT_WHITESPACE_RE.sub(" ", cleaned).strip(" .")
        if not cleaned:
            return fallback
        return cleaned[:120].rstrip(" .") or fallback

    def _safe_child_dir(self, *parts: str) -> Path:
        root = self._settings.artifacts_dir.resolve()
        directory = self._settings.artifacts_dir.joinpath(*parts)
        directory.mkdir(parents=True, exist_ok=True)
        resolved = directory.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise DownloadAppError("artifact output directory is unsafe", "文件目录配置异常。") from exc
        return resolved

    def _role_output_dir(self, role: ArtifactRole) -> Path:
        name = "Vocals" if role == ArtifactRole.VOCALS else "Accompaniment"
        return self._safe_child_dir("Separated", name)

    def _allocate_artifact_path(self, *, stem: str, ext: str, directory: Path | None = None) -> Path:
        output_dir = directory or self._settings.artifacts_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        index = 0
        while True:
            suffix = "" if index == 0 else f" ({index})"
            candidate = output_dir / f"{stem}{suffix}.{ext}"
            try:
                candidate.touch(exist_ok=False)
                return candidate
            except FileExistsError:
                index += 1

    def _normalize_audio_output_extension(self, ext: str) -> str:
        normalized = ext.lower().strip(".")
        if normalized in _ALLOWED_AUDIO_OUTPUT_EXTENSIONS:
            return normalized
        return "wav"

    def _delete_artifact(self, artifact_id: str) -> None:
        artifact = self._artifacts.get(artifact_id)
        if artifact is not None:
            self._cleanup_file(Path(artifact.storage_path))
        self._artifacts.delete(artifact_id)

    def _cleanup_file(self, path: Path) -> None:
        artifacts_root = self._settings.artifacts_dir.resolve()
        try:
            resolved_path = path.resolve()
            resolved_path.relative_to(artifacts_root)
        except ValueError:
            return
        if resolved_path.exists() and resolved_path.is_file():
            resolved_path.unlink()

    def _mark_failed(self, job_id: str, error_message: str, user_message: str) -> None:
        current = self._jobs.get(job_id)
        if current is None or current.status == JobStatus.CANCELED:
            return
        self._jobs.create_event(job_id, level="error", event_type="failed", message=user_message)
        self._jobs.update_status(
            job_id,
            status=JobStatus.FAILED,
            progress=100,
            error_code="download_error",
            error_message=error_message,
            user_message=user_message,
            finished_at=datetime.now(tz=UTC),
        )
