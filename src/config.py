import sys
from typing import Dict, Any
import yaml
from src.logger import logger

def load_config(config_path: str = "config.yaml") -> Dict[str, Any]:
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        for key in ["cookies", "channels", "output", "monitoring"]:
            if key not in config:
                raise ValueError(f"누락된 설정 항목: {key}")

        if "auto_delete" in config:
            ad = config["auto_delete"]
            ad.setdefault("enabled", False)
            ad.setdefault("retention_days", 30)
            ad.setdefault("check_interval", 3600)

            if ad["enabled"] and ad["retention_days"] <= 0:
                raise ValueError("retention_days는 0보다 커야 합니다")

        return config
    except FileNotFoundError:
        logger.error(f"설정 파일을 찾을 수 없습니다: {config_path}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"설정 로드 실패: {e}")
        sys.exit(1)
