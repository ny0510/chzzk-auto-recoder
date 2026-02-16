import os
from datetime import datetime, timedelta
from pathlib import Path

from src.logger import logger


def get_max_filename_length(path: str = ".") -> int:
    """파일시스템의 최대 파일명 길이 반환 (바이트)"""
    try:
        # Unix/Linux/macOS에서 사용 가능
        return os.pathconf(path, "PC_NAME_MAX")
    except (AttributeError, OSError, ValueError):
        # Windows 또는 지원하지 않는 경우 기본값 반환
        # Windows NTFS: 255, macOS APFS/HFS+: 255, Linux ext4: 255
        return 255


def sanitize_filename(
    name: str, target_path: str = ".", reserve_bytes: int = 50
) -> str:
    """파일명에 사용할 수 없는 문자 제거 및 길이 제한"""
    # 잘못된 문자 제거
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, "_")

    name = name.strip()

    # 파일시스템의 최대 파일명 길이 가져오기
    max_length = get_max_filename_length(target_path)
    # 확장자 등을 위한 여유 공간 확보
    max_bytes = max_length - reserve_bytes

    # 바이트 길이 제한 (UTF-8 기준)
    encoded = name.encode("utf-8")
    if len(encoded) > max_bytes:
        # 바이트 단위로 자르되, 유효한 UTF-8 문자열 유지
        truncated = encoded[:max_bytes]
        # 마지막 불완전한 멀티바이트 문자 제거
        while truncated:
            try:
                name = truncated.decode("utf-8")
                # 말줄임표 추가
                if len(name) > 3:
                    name = name[:-3] + "..."
                break
            except UnicodeDecodeError:
                truncated = truncated[:-1]

    return name


def cleanup_lockfiles(base_path: str) -> int:
    try:
        recordings_path = Path(base_path).expanduser()

        if not recordings_path.exists():
            return 0

        lock_files = list(recordings_path.rglob("*.lock"))
        for lock_file in lock_files:
            lock_file.unlink(missing_ok=True)

        return len(lock_files)
    except Exception:
        return 0


def delete_old_recordings(base_path: str, retention_days: int) -> int:
    try:
        recordings_path = Path(base_path).expanduser()

        if not recordings_path.exists():
            return 0

        cutoff_time = datetime.now() - timedelta(days=retention_days)
        deleted_count = 0

        video_extensions = {".mp4", ".mkv", ".ts", ".flv", ".avi", ".mov"}

        for file_path in recordings_path.rglob("*"):
            if not file_path.is_file():
                continue

            if file_path.suffix.lower() not in video_extensions:
                continue

            file_mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

            if file_mtime < cutoff_time:
                try:
                    file_path.unlink()
                    deleted_count += 1
                    logger.info(f"오래된 녹화본 삭제: {file_path}")
                except Exception as e:
                    logger.error(f"파일 삭제 실패 {file_path}: {e}")

        return deleted_count
    except Exception as e:
        logger.error(f"녹화본 삭제 작업 실패: {e}")
        return 0
