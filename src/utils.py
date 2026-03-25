import os
from datetime import datetime, timedelta
from pathlib import Path
from src.logger import logger

def get_max_filename_length(path: str = ".") -> int:
    try:
        return os.pathconf(path, "PC_NAME_MAX")
    except (AttributeError, OSError, ValueError):
        return 255

def sanitize_filename(name: str, target_path: str = ".", reserve_bytes: int = 60) -> str:
    invalid_chars = '<>:"/\\|?*'
    for char in invalid_chars:
        name = name.replace(char, "_")
    name = name.strip()

    max_bytes = get_max_filename_length(target_path) - reserve_bytes
    encoded = name.encode("utf-8")
    
    if len(encoded) > max_bytes:
        truncated = encoded[:max_bytes - 3]
        while truncated:
            try:
                name = truncated.decode("utf-8") + "..."
                break
            except UnicodeDecodeError:
                truncated = truncated[:-1]
    return name

def delete_old_recordings(base_path: str, retention_days: int) -> int:
    try:
        root = Path(base_path).expanduser()
        if not root.exists():
            return 0

        cutoff = datetime.now() - timedelta(days=retention_days)
        deleted = 0
        exts = {".mp4", ".mkv", ".ts", ".flv", ".avi", ".mov"}

        for f in root.rglob("*"):
            try:
                if f.is_file() and f.suffix.lower() in exts:
                    if datetime.fromtimestamp(f.stat().st_mtime) < cutoff:
                        f.unlink()
                        deleted += 1
                        logger.info(f"삭제됨: {f}")
            except Exception as e:
                logger.error(f"파일 처리 오류 {f}: {e}")
        return deleted
    except Exception as e:
        logger.error(f"삭제 작업 실패: {e}")
        return 0
