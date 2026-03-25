import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

import aiohttp

from src.logger import logger
from src.utils import sanitize_filename, delete_old_recordings


class ChzzkRecorder:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.cookies = config["cookies"]
        self.channels = config["channels"]
        self.output_config = config["output"]
        self.monitoring_config = config["monitoring"]
        self.session: Optional[aiohttp.ClientSession] = None
        self.channel_names: Dict[str, str] = {}

    async def start(self):
        logger.info("치지직 자동 녹화를 시작합니다.")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        self.session = aiohttp.ClientSession(headers=headers)

        try:
            self._cleanup_old_lockfiles()
            await self.validate_channels()

            tasks = [
                asyncio.create_task(self.monitor_channel(channel_id))
                for channel_id in self.channels
            ]

            auto_delete_config = self.config.get("auto_delete", {})
            if auto_delete_config.get("enabled", False):
                tasks.append(asyncio.create_task(self.auto_delete_task()))

            await asyncio.gather(*tasks)
        except Exception as e:
            logger.critical(f"오류 발생: {e}")
        finally:
            await self.session.close()

    async def validate_channels(self):
        logger.debug("채널 검증 중...")
        valid_channels = []

        if self.session is None:
            return

        for channel_id in self.channels:
            url = f"https://api.chzzk.naver.com/service/v1/channels/{channel_id}"
            try:
                async with self.session.get(url) as response:
                    if response.status == 404:
                        logger.error(f"[{channel_id}] 존재하지 않는 채널 ID")
                    elif response.status != 200:
                        logger.warning(f"[{channel_id}] API 응답 오류 (HTTP {response.status})")
                    else:
                        data = await response.json()
                        channel_name = data.get("content", {}).get("channelName", channel_id)
                        self.channel_names[channel_id] = channel_name
                        valid_channels.append(channel_id)
                        logger.debug(f"{channel_name} ({channel_id}) 검증 성공")
            except Exception as e:
                logger.error(f"[{channel_id}] 검증 실패: {e}")

        if not valid_channels:
            raise ValueError("유효한 채널 ID가 없습니다.")

        self.channels = valid_channels
        names = ", ".join([self.channel_names[ch_id] for ch_id in self.channels])
        logger.info(f"모니터링 채널 ({len(self.channels)}개): [{names}]")

    def _cleanup_old_lockfiles(self):
        base_path = self.output_config["path"].split("{")[0].rstrip("/")
        count = 0
        for channel_id in self.channels:
            lock_file = Path(base_path) / f"recorder_{channel_id}.lock"
            if lock_file.exists():
                lock_file.unlink(missing_ok=True)
                count += 1
        
        if count > 0:
            logger.info(f"기존 lockfile {count}개 정리")

    async def auto_delete_task(self):
        auto_delete_config = self.config.get("auto_delete", {})
        retention_days = auto_delete_config.get("retention_days", 30)
        check_interval = auto_delete_config.get("check_interval", 3600)

        while True:
            await asyncio.sleep(check_interval)
            try:
                base_path = self.output_config["path"].split("{")[0].rstrip("/")
                deleted_count = delete_old_recordings(base_path, retention_days)
                if deleted_count > 0:
                    logger.info(f"{deleted_count}개의 오래된 녹화본 삭제 완료")
            except Exception as e:
                logger.error(f"자동 삭제 작업 오류: {e}")

    async def monitor_channel(self, channel_id: str):
        channel_name = self.channel_names.get(channel_id, channel_id)
        logger.info(f"[{channel_name}] 모니터링 시작")

        while True:
            try:
                live_info = await self.check_live_status(channel_id)
                if live_info and live_info["status"] == "OPEN":
                    await self.start_recording(channel_id, live_info)
                await asyncio.sleep(self.monitoring_config["check_interval"])
            except Exception as e:
                logger.error(f"[{channel_name}] 모니터링 오류: {e}")
                await asyncio.sleep(self.monitoring_config["check_interval"])

    async def check_live_status(self, channel_id: str) -> Optional[Dict[str, Any]]:
        url = f"https://api.chzzk.naver.com/service/v3/channels/{channel_id}/live-detail"
        if self.session is None:
            return None

        try:
            async with self.session.get(url) as response:
                if response.status == 200:
                    data = await response.json()
                    content = data.get("content", {})
                    open_date_str = content.get("openDate")
                    open_date = None
                    if open_date_str:
                        try:
                            open_date = datetime.strptime(open_date_str, "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            pass

                    return {
                        "status": content.get("status"),
                        "liveTitle": content.get("liveTitle", "Unknown"),
                        "channelName": content.get("channel", {}).get("channelName", "Unknown"),
                        "liveId": content.get("liveId"),
                        "openDate": open_date,
                    }
        except Exception:
            pass
        return None

    async def start_recording(self, channel_id: str, live_info: Dict[str, Any]):
        channel_name = live_info["channelName"]
        title = live_info["liveTitle"]
        live_id = live_info["liveId"]
        open_date = live_info.get("openDate") or datetime.now()

        output_path, output_file = self._prepare_output_path(channel_name, title, open_date)
        base_path = Path(self.output_config["path"].split("{")[0].rstrip("/"))
        lock_file = base_path / f"recorder_{channel_id}.lock"
        
        if lock_file.exists():
            return

        try:
            lock_file.parent.mkdir(parents=True, exist_ok=True)
            lock_file.write_text(str(datetime.now()))
            logger.info(f"[{channel_name}] 방송 시작: {title}")

            temp_file = output_path / f"temp_{output_file}"
            final_file = output_path / output_file

            if temp_file.exists():
                temp_file.unlink()

            cmd = self._build_streamlink_command(channel_id, str(temp_file))
            logger.info(f"[{channel_name}] 녹화 시작")
            
            process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )

            async def log_stderr():
                if process.stderr:
                    async for line in process.stderr:
                        line_str = line.decode("utf-8", errors="ignore").strip()
                        if line_str:
                            logger.debug(f"[{channel_name}] streamlink: {line_str}")

            stderr_task = asyncio.create_task(log_stderr())
            await self._wait_for_stream_end(channel_id, live_id, process, channel_name)
            
            await process.wait()
            await stderr_task

            if process.returncode not in (0, 130):
                logger.error(f"[{channel_name}] streamlink 오류 종료 (code: {process.returncode})")

            if temp_file.exists():
                await self._fix_timestamps(temp_file, final_file, channel_name)
                if temp_file.exists():
                    temp_file.unlink()
            
            logger.info(f"[{channel_name}] 녹화 완료")
        except Exception as e:
            logger.error(f"[{channel_name}] 녹화 오류: {e}")
        finally:
            lock_file.unlink(missing_ok=True)

    def _prepare_output_path(self, author: str, title: str, time: datetime) -> tuple[Path, str]:
        path_str = self.output_config["path"].format(
            author=sanitize_filename(author), title=sanitize_filename(title), time=time
        )
        output_path = Path(path_str).expanduser()
        output_path.mkdir(parents=True, exist_ok=True)

        filename = self.output_config["filename"].format(
            author=sanitize_filename(author), title=sanitize_filename(title), time=time
        )
        return output_path, filename

    def _build_streamlink_command(self, channel_id: str, output_file: str) -> list[str]:
        url = f"https://chzzk.naver.com/live/{channel_id}"
        quality = self.output_config.get("quality", "best")
        return [
            "streamlink", "--output", output_file, "--progress", "no",
            "--ffmpeg-start-at-zero", "--ffmpeg-copyts",
            "--http-cookie", f"NID_AUT={self.cookies['NID_AUT']}",
            "--http-cookie", f"NID_SES={self.cookies['NID_SES']}",
            url, quality,
        ]

    async def _wait_for_stream_end(self, channel_id: str, live_id: str, process: asyncio.subprocess.Process, channel_name: str):
        check_interval = self.monitoring_config["stop_check_interval"]
        fail_count, max_fails = 0, 3

        await asyncio.sleep(check_interval)
        while True:
            if process.returncode is not None:
                break

            live_info = await self.check_live_status(channel_id)
            if not live_info:
                fail_count += 1
                if fail_count >= max_fails:
                    logger.info(f"[{channel_name}] 방송 상태 확인 실패 ({max_fails}회 연속)")
                    break
                await asyncio.sleep(check_interval)
                continue
            
            fail_count = 0
            if live_info["status"] != "OPEN" or live_info["liveId"] != live_id:
                logger.info(f"[{channel_name}] 방송 종료 감지")
                break
            await asyncio.sleep(check_interval)

        if process.returncode is None:
            try:
                process.terminate()
                await asyncio.sleep(5)
                if process.returncode is None:
                    process.kill()
            except Exception:
                pass

    async def _fix_timestamps(self, temp_file: Path, final_file: Path, channel_name: str):
        logger.info(f"[{channel_name}] 타임스탬프 재설정 중...")
        cmd = ["ffmpeg", "-i", str(temp_file), "-c", "copy", "-map", "0", "-reset_timestamps", "1", "-y", str(final_file)]
        process = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await process.wait()

        if process.returncode == 0:
            logger.info(f"[{channel_name}] 후처리 완료")
        else:
            logger.error(f"[{channel_name}] 후처리 실패 (원본 유지)")
            temp_file.rename(final_file)
