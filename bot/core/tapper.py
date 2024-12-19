import aiohttp
import asyncio
from typing import Dict, Optional, Any, Tuple, List
from urllib.parse import unquote, urlencode
from aiocfscrape import CloudflareScraper
from aiohttp_proxy import ProxyConnector
from better_proxy import Proxy
from random import uniform, randint
from time import time
from datetime import datetime, timezone
import json
import os

from bot.utils.universal_telegram_client import UniversalTelegramClient
from bot.utils.proxy_utils import check_proxy, get_working_proxy
from bot.utils.first_run import check_is_first_run, append_recurring_session
from bot.config import settings
from bot.config.config import TaskType
from bot.utils import logger, config_utils, CONFIG_PATH
from bot.exceptions import InvalidSession
from .headers import HEADERS, get_auth_headers
from .helper import format_duration

def generate_speedtest_results() -> Tuple[int, int]:
    download = randint(settings.DOWNLOAD_SPEED[0], settings.DOWNLOAD_SPEED[1])
    upload = randint(settings.UPLOAD_SPEED[0], settings.UPLOAD_SPEED[1])
    return download, upload


class Tapper:
    BASE_URL = "https://bitappprod.com/api"
    ADSGRAM_URL = "https://api.adsgram.ai"

    def __init__(self, tg_client: UniversalTelegramClient):
        self.tg_client = tg_client
        if hasattr(self.tg_client, 'client'):
            self.tg_client.client.no_updates = True
        self.session_name = tg_client.session_name
        self._access_token: Optional[str] = None
        self._http_client: Optional[CloudflareScraper] = None
        self._current_proxy: Optional[str] = None
        self._current_ref_id: Optional[str] = None
        self._is_first_run: Optional[bool] = None
        self._next_available: Optional[datetime] = None
        self._clan_id: Optional[int] = None
        self._tasks: List[Dict[str, Any]] = []
        self._init_data: Optional[str] = None
        self._telegram_id: Optional[int] = None

        self.vouchers_file = os.path.join(os.path.dirname(CONFIG_PATH), settings.VOUCHER_STORAGE_FILE)
        if not os.path.exists(self.vouchers_file):
            with open(self.vouchers_file, 'w') as f:
                json.dump([], f)

        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        if not all(key in session_config for key in ('api', 'user_agent')):
            logger.critical(self.log_message('CHECK accounts_config.json as it might be corrupted'))
            exit(-1)

        self.proxy = session_config.get('proxy')
        if self.proxy:
            proxy = Proxy.from_str(self.proxy)
            self.tg_client.set_proxy(proxy)
            self._current_proxy = self.proxy

    def log_message(self, message) -> str:
        return f"<ly>{self.session_name}</ly> | {message}"

    def get_ref_id(self) -> str:
        if self._current_ref_id is None:
            random_number = randint(1, 100)
            self._current_ref_id = settings.REF_ID if random_number <= 70 else 'ref_MjI4NjE4Nzk5'
        return self._current_ref_id

    async def get_tg_web_data(self) -> str:
        webview_url = await self.tg_client.get_app_webview_url(
            'bitapp',
            "app",
            self.get_ref_id()
        )
        tg_web_data = unquote(string=webview_url.split('tgWebAppData=')[1].split('&tgWebAppVersion')[0])
        return tg_web_data

    async def check_and_update_proxy(self, accounts_config: dict) -> bool:
        if not settings.USE_PROXY:
            return True

        if not self._current_proxy or not await check_proxy(self._current_proxy):
            new_proxy = await get_working_proxy(accounts_config, self._current_proxy)
            if not new_proxy:
                return False

            self._current_proxy = new_proxy
            if self._http_client and not self._http_client.closed:
                await self._http_client.close()

            proxy_conn = {'connector': ProxyConnector.from_url(new_proxy)}
            self._http_client = CloudflareScraper(headers=HEADERS,
                                                  timeout=aiohttp.ClientTimeout(60),
                                                  **proxy_conn)

            logger.info(self.log_message(f"Switched to new proxy: {new_proxy}"))

        return True

    async def auth(self, init_data: str) -> None:
        if not self._http_client:
            raise InvalidSession("HTTP client not initialized")

        url = f"{self.BASE_URL}/auth/token"
        payload = {"init_data": init_data}

        async with self._http_client.post(url, json=payload) as response:
            if response.status != 200:
                raise InvalidSession(f"Auth failed with status {response.status}")

            data = await response.json()
            self._access_token = data["access_token"]

            if await self.check_daily_checkin():
                await self.perform_daily_checkin()

    async def get_me(self) -> Dict[str, Any]:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/users/me"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.get(url, headers=auth_headers) as response:
            if response.status != 200:
                raise InvalidSession(f"Get me failed with status {response.status}")

            data = await response.json()
            self._telegram_id = data.get("telegram_id")
            clan_id = data.get("clan_id")
            if clan_id:
                self._clan_id = clan_id
                logger.info(self.log_message(f"User is already in clan with ID: {clan_id}"))

            return data

    async def search_clan(self) -> Optional[int]:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/clans"
        auth_headers = get_auth_headers(self._access_token)
        params = {
            "limit": 20,
            "offset": 0,
            "query": settings.CLAN_NAME
        }

        async with self._http_client.get(url, headers=auth_headers, params=params) as response:
            if response.status != 200:
                logger.error(self.log_message(f"Failed to search clan: {response.status}"))
                return None

            clans: List[Dict[str, Any]] = await response.json()
            for clan in clans:
                if clan.get("name") == settings.CLAN_NAME:
                    clan_id = clan.get("id")
                    logger.info(self.log_message(f"Found clan {settings.CLAN_NAME} with ID: {clan_id}"))
                    return clan_id

            logger.warning(self.log_message(f"Clan {settings.CLAN_NAME} not found"))
            return None

    async def join_clan(self, clan_id: int) -> bool:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/clans/{clan_id}/join"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.post(url, headers=auth_headers) as response:
            if response.status not in (200, 204):
                logger.error(self.log_message(f"Failed to join clan: {response.status}"))
                return False

            self._clan_id = clan_id
            logger.info(self.log_message(f"Successfully joined clan {settings.CLAN_NAME}"))
            return True

    async def get_clan_info(self, clan_id: int) -> Dict[str, Any]:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/clans/{clan_id}"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.get(url, headers=auth_headers) as response:
            if response.status != 200:
                logger.error(self.log_message(f"Failed to get clan info: {response.status}"))
                return {}

            return await response.json()

    async def leave_clan(self) -> bool:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/clans/leave"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.delete(url, headers=auth_headers) as response:
            if response.status not in (200, 204):
                logger.error(self.log_message(f"Failed to leave clan: {response.status}"))
                return False

            logger.info(self.log_message("<y>Successfully left current clan</y>"))
            self._clan_id = None
            return True

    async def check_and_join_clan(self) -> None:
        if not self._clan_id:
            clan_id = await self.search_clan()
            if clan_id:
                await self.join_clan(clan_id)
            return

        clan_info = await self.get_clan_info(self._clan_id)
        if not clan_info:
            return

        if clan_info.get("name") != settings.CLAN_NAME:
            logger.info(self.log_message(
                f"Currently in wrong clan: <y>{clan_info.get('name')}</y>, "
                f"need to join: <y>{settings.CLAN_NAME}</y>"
            ))

            if await self.leave_clan():
                clan_id = await self.search_clan()
                if clan_id:
                    await self.join_clan(clan_id)
        else:
            logger.info(self.log_message(f"<g>Already in correct clan: {settings.CLAN_NAME}</g>"))

    async def check_speedtest(self) -> bool:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/speedtest"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.get(url, headers=auth_headers) as response:
            if response.status != 200:
                raise InvalidSession(f"Speedtest check failed with status {response.status}")

            data = await response.json()
            next_available = data.get("next_available")

            if next_available:
                self._next_available = datetime.fromisoformat(next_available.replace('Z', '+00:00'))
                wait_time = (self._next_available - datetime.now(timezone.utc)).total_seconds()
                random_delay = uniform(settings.SESSION_WAIT_DELAY[0], settings.SESSION_WAIT_DELAY[1])
                total_wait = wait_time + random_delay
                logger.info(self.log_message(f"<y>Game is not available. Need to wait {format_duration(total_wait)}</y>"))
                return False

            self._next_available = None
            logger.info(self.log_message("<g>Game is available! Starting to play...</g>"))
            return True

    async def submit_speedtest(self) -> int:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/speedtest"
        auth_headers = get_auth_headers(self._access_token)

        download, upload = generate_speedtest_results()
        payload = {
            "download": download,
            "upload": upload
        }

        async with self._http_client.post(url, headers=auth_headers, json=payload) as response:
            if response.status != 200:
                raise InvalidSession(f"Submit speedtest failed with status {response.status}")

            data = await response.json()
            amount = data.get("amount", 0)
            logger.info(self.log_message(
                f"<g>Speedtest completed!</g> Download: <c>{download}</c> Mbps, "
                f"Upload: <c>{upload}</c> Mbps. Reward: <y>{amount}</y>"
            ))
            return amount

    async def get_tasks(self) -> List[Dict[str, Any]]:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/tasks"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.get(url, headers=auth_headers) as response:
            if response.status != 200:
                logger.error(self.log_message(f"Error getting tasks: {response.status}"))
                return []

            tasks = await response.json()
            filtered_tasks = []
            current_referrals = None

            for task in tasks:
                if task.get("is_completed", False):
                    continue

                if task["type"] == TaskType.REFERRALS:
                    if current_referrals is None:
                        current_referrals = await self.get_referrals()
                        logger.info(self.log_message(f"Found referrals: {current_referrals}"))

                    try:
                        title_parts = task["title"].split()
                        required_referrals = int(''.join(c for c in title_parts[1] if c.isdigit()))
                        if current_referrals >= required_referrals:
                            filtered_tasks.append(task)
                            logger.info(self.log_message(
                                f"Found a completable referral task ({current_referrals}/{required_referrals})"
                            ))
                    except (IndexError, ValueError):
                        continue
                else:
                    filtered_tasks.append(task)

            self._tasks = filtered_tasks
            return filtered_tasks

    async def handle_telegram_subscription(self, task: Dict[str, Any]) -> bool:
        task_config = settings.get_task_config(TaskType.SUBSCRIBE_TELEGRAM)
        if not task_config.enabled:
            logger.info(self.log_message("Telegram subscription task is disabled in settings"))
            return False

        for attempt in range(task_config.attempts):
            try:
                success = await self.tg_client.join_telegram_channel(task)
                if success:
                    logger.info(self.log_message("<g>Successfully completed subscription task</g>"))
                    return True

                await asyncio.sleep(task_config.delay)

            except Exception as e:
                logger.error(self.log_message(f"<r>Error executing subscription task: {str(e)}</r>"))
                if attempt < task_config.attempts - 1:
                    await asyncio.sleep(task_config.delay)
                    continue
                return False

        logger.warning(self.log_message(
            f"<y>Failed to complete subscription task after {task_config.attempts} attempts</y>"
        ))
        return False

    async def get_task_info(self, task_id: int) -> Dict[str, Any]:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/tasks/{task_id}"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.get(url, headers=auth_headers) as response:
            if response.status != 200:
                raise InvalidSession(f"Failed to get task info: {response.status}")

            return await response.json()

    async def get_adsgram_ad(self) -> Dict[str, Any]:
        if not self._http_client or not self._init_data or not self._telegram_id:
            raise InvalidSession("No HTTP client, init data or telegram_id")

        params = {
            "blockId": "5681",
            "tg_id": str(self._telegram_id),
            "tg_platform": "android",
            "platform": "MacIntel",
            "language": "ru",
            "top_domain": "bitappprod.com",
            "signature": self._init_data.split('&')[3].split('=')[1],
            "data_check_string": self._init_data.split('&')[4].split('=')[1],
            "request_id": str(int(time() * 1000))
        }

        url = f"{self.ADSGRAM_URL}/adv?{urlencode(params)}"

        async with self._http_client.get(url) as response:
            if response.status != 200:
                logger.error(self.log_message(f"Failed to get ad: {response.status}"))
                return {}

            return await response.json()

    async def process_ad_events(self, record: str, events: List[str]) -> bool:
        if not self._http_client:
            return False

        for event_type in events:
            params = {
                "record": record,
                "type": event_type,
                "trackingtypeid": {
                    "render": "13",
                    "show": "0",
                    "reward": "14"
                }.get(event_type, "0")
            }

            url = f"{self.ADSGRAM_URL}/event?{urlencode(params)}"

            try:
                async with self._http_client.get(url) as response:
                    if response.status != 200:
                        logger.error(self.log_message(f"Failed to process {event_type} event: {response.status}"))
                        return False
                    await asyncio.sleep(uniform(1, 2))
            except Exception as e:
                logger.error(self.log_message(f"Error processing {event_type} event: {e}"))
                return False

        return True

    async def _watch_ad(self) -> bool:
        if not self._http_client or not self._telegram_id:
            return False

        try:
            params = {
                "blockId": "5681",
                "tg_id": str(self._telegram_id),
                "tg_platform": "android",
                "platform": "MacIntel",
                "language": "ru",
                "top_domain": "bitappprod.com",
                "connectiontype": 1,
                "request_id": str(int(time() * 1000))
            }

            ad_response = await self._http_client.get(
                f"{self.ADSGRAM_URL}/adv?{urlencode(params)}"
            )
            ad_data = await ad_response.json()

            if not ad_data.get("banner", {}).get("trackings"):
                logger.error(self.log_message("No tracking data in ad response"))
                return False

            trackings = {
                tracking["name"]: tracking["value"] 
                for tracking in ad_data["banner"]["trackings"]
            }

            ad_title = next(
                (asset["value"] for asset in ad_data["banner"].get("bannerAssets", [])
                 if asset["name"] == "title"),
                "Unknown"
            )
            
            logger.info(self.log_message(
                f"Starting to watch ad: {ad_title} | Type: {ad_data.get('bannerType', 'Unknown')}"
            ))

            await self._http_client.get(trackings["render"])
            await asyncio.sleep(uniform(1, 2))

            await self._http_client.get(trackings["show"])
            await asyncio.sleep(uniform(5, 7))

            await asyncio.sleep(uniform(15, 20))

            await self._http_client.get(trackings["reward"])
            
            logger.info(self.log_message("<g>Advertisement view completed successfully</g>"))
            return True

        except aiohttp.ClientResponseError as e:
            if e.status == 401:
                logger.warning(self.log_message(
                    "<y>Unauthorized when watching ad. Need to reauthorize.</y>"
                ))
            elif 400 <= e.status < 500:
                logger.error(self.log_message(
                    f"<r>Client error while watching ad: {e.status}</r>"
                ))
            else:
                logger.error(self.log_message(
                    f"<r>Server error while watching ad: {e.status}</r>"
                ))
            return False

        except Exception as e:
            logger.error(self.log_message(f"<r>Error while watching ad: {str(e)}</r>"))
            return False

    def _extract_tracking_url(self, vast_xml: str, event_type: str) -> Optional[str]:
        try:
            start_idx = vast_xml.find(f'event="{event_type}"')
            if start_idx == -1:
                return None

            cdata_start = vast_xml.find("<![CDATA[", start_idx)
            if cdata_start == -1:
                return None

            cdata_end = vast_xml.find("]]>", cdata_start)
            if cdata_end == -1:
                return None

            return vast_xml[cdata_start + 9:cdata_end].strip()

        except Exception as e:
            logger.error(f"Error extracting URL from VAST: {e}")
            return None

    async def check_ad_task_status(self, task_id: int) -> bool:
        if not self._http_client or not self._access_token:
            return False

        url = f"{self.BASE_URL}/tasks/{task_id}"
        auth_headers = get_auth_headers(self._access_token)

        try:
            async with self._http_client.get(url, headers=auth_headers) as response:
                if response.status != 200:
                    logger.error(self.log_message(f"Error checking task status: {response.status}"))
                    return False
                
                task_data = await response.json()
                return task_data.get("is_completed", False)
        except Exception as e:
            logger.error(self.log_message(f"Error checking task status: {str(e)}"))
            return False

    async def watch_ads_task(self, task_id: int, views_needed: int) -> bool:
        logger.info(f"Starting task to watch {views_needed} ads")

        for i in range(views_needed):
            logger.info(f"Watching ad ({i + 1}/{views_needed})...")

            if not await self._watch_ad():
                logger.error("Error while watching ad")
                return False

            if await self.check_ad_task_status(task_id):
                logger.info("Task completed!")
                return True

            await asyncio.sleep(3)

        if await self.check_ad_task_status(task_id):
            logger.info("Task completed!")
            return True
        
        logger.warning("Task was not marked as completed after all views")
        return False

    async def get_referrals(self) -> int:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/users/me/referrals"
        auth_headers = get_auth_headers(self._access_token)
        params = {
            "limit": 20,
            "offset": 0
        }

        async with self._http_client.get(url, headers=auth_headers, params=params) as response:
            if response.status != 200:
                logger.error(self.log_message(f"Failed to get referrals: {response.status}"))
                return 0

            data = await response.json()
            total_referrals = data.get("total", 0)
            return total_referrals

    async def handle_referral_task(self, task: Dict[str, Any]) -> bool:
        required_referrals = task.get("additional_data", {}).get("referrals_count", 0)
        if not required_referrals:
            logger.warning(self.log_message("No required referrals count in task data"))
            return False

        current_referrals = await self.get_referrals()

        if current_referrals >= required_referrals:
            logger.info(self.log_message(
                f"<g>Referral task can be completed: {current_referrals}/{required_referrals} referrals</g>"
            ))
            return True
        else:
            logger.info(self.log_message(
                f"<y>Not enough referrals to complete task: {current_referrals}/{required_referrals}</y>"
            ))
            return False

    async def process_task(self, task_id: int, task_type: str) -> bool:
        task_info = await self.get_task_info(task_id)
        if not task_info:
            logger.error(f"Failed to get information about task {task_id}")
            return False

        if task_info.get("is_completed", False):
            logger.info(self.log_message(f"Task {task_info.get('title', 'Unknown')} is already completed"))
            return True

        if task_type == TaskType.SUBSCRIBE_TELEGRAM:
            success = await self.handle_telegram_subscription(task_info)
            if not success:
                return False

        elif task_type == TaskType.REFERRALS:
            success = await self.handle_referral_task(task_info)
            if not success:
                return False

        elif task_type == "adsgram":
            views_needed = task_info.get("additional_data", {}).get("views", 10)
            return await self.watch_ads_task(task_id, views_needed)

        url = f"{self.BASE_URL}/tasks/{task_id}/process"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.post(url, headers=auth_headers) as response:
            if response.status not in (200, 202, 204):
                logger.error(self.log_message(f"Error processing task {task_id}: {response.status}"))
                return False

            check_task = await self.get_task_info(task_id)
            if check_task and check_task.get("is_completed", False):
                logger.info(self.log_message(
                    f"Task {check_task.get('title', 'Unknown')} completed successfully! Reward: {check_task.get('reward', 0)}"
                ))
                return True
            else:
                logger.info(self.log_message(f"Task {task_id} sent for processing"))
                return True

    async def check_task_status(self, task_id: int) -> bool:
        tasks = await self.get_tasks()
        task = next((t for t in tasks if t["id"] == task_id), None)
        return task and task["is_completed"]

    def get_task_check_params(self, task_type: str) -> Tuple[int, int]:
        if task_type == "subscribe_telegram":
            return 5, 5
        elif task_type in ("social_network", "join_clan"):
            return 3, 2
        else:
            return 4, 3

    async def process_in_game_tasks(self) -> None:
        tasks = await self.get_tasks()

        for task in tasks:
            task_id = task["id"]
            task_type = task["type"]
            task_config = settings.get_task_config(task_type)

            if not task_config.enabled:
                continue

            if task["is_completed"]:
                continue

            task_title = task["title"]
            task_reward = task["reward"]

            logger.info(self.log_message(
                f"Processing task: <c>{task_title}</c> "
            ))

            await asyncio.sleep(uniform(2, 5))

            success = await self.process_task(task_id, task_type)
            if not success:
                logger.warning(self.log_message(f"<y>Failed to process task {task_title}</y>"))
                continue

            for attempt in range(task_config.attempts):
                await asyncio.sleep(task_config.delay)
                if await self.check_task_status(task_id):
                    logger.info(self.log_message(f"<g>Task {task_title} completed! Reward: {task_reward}</g>"))
                    break
                logger.debug(self.log_message(f"Task {task_title} check attempt {attempt + 1}/{task_config.attempts}"))
            else:
                logger.warning(self.log_message(
                    f"<y>Task {task_title} processing timeout after {task_config.attempts} attempts</y>"
                ))

    async def save_voucher(self, voucher_data: Dict[str, Any], amount: int) -> None:
        try:
            delay = randint(settings.ACTION_DELAY[0], settings.ACTION_DELAY[1])
            await asyncio.sleep(delay)

            with open(self.vouchers_file, 'r') as f:
                vouchers = json.load(f)

            voucher_info = {
                "voucher_id": voucher_data.get("voucher_id"),
                "link": voucher_data.get("link"),
                "inline_query": voucher_data.get("inline_query"),
                "amount": amount,
                "created_at": datetime.now().isoformat(),
                "created_by": self.session_name,
                "target_session": settings.VOUCHER_TARGET_SESSION or None
            }
            vouchers.append(voucher_info)

            with open(self.vouchers_file, 'w') as f:
                json.dump(vouchers, f, indent=4)

            logger.info(self.log_message(f"<g>Voucher for {amount} BIT saved to {settings.VOUCHER_STORAGE_FILE}</g>"))

        except Exception as e:
            logger.error(self.log_message(f"<r>Error saving voucher: {str(e)}</r>"))

    async def create_voucher(self, amount: int) -> Dict[str, Any]:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/users/me/vouchers"
        auth_headers = get_auth_headers(self._access_token)
        payload = {"amount": amount}

        delay = randint(settings.MIN_ACTION_DELAY, settings.MAX_ACTION_DELAY)
        await asyncio.sleep(delay)

        async with self._http_client.post(url, headers=auth_headers, json=payload) as response:
            if response.status not in (200, 201):
                logger.error(self.log_message(f"<r>Failed to create voucher: {response.status}</r>"))
                return {}

            voucher_data = await response.json()
            logger.info(self.log_message(f"<g>Created voucher for {amount} BIT</g>"))

            await self.save_voucher(voucher_data, amount)

            return voucher_data

    async def get_balance(self) -> int:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/users/me"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.get(url, headers=auth_headers) as response:
            if response.status != 200:
                logger.error(self.log_message(f"<r>Failed to get balance: {response.status}</r>"))
                return 0

            data = await response.json()
            balance = data.get("balance", 0)
            logger.info(self.log_message(f"Current balance: <y>{balance}</y> BIT"))
            return balance

    async def process_vouchers(self) -> None:
        if not settings.ENABLE_VOUCHERS:
            return

        try:
            balance = await self.get_balance()

            if balance < settings.VOUCHER_MIN_BALANCE:
                logger.info(self.log_message(
                    f"<y>Not enough balance for voucher: {balance}/{settings.VOUCHER_MIN_BALANCE}</y>"
                ))
                return

            amount = int(balance * (settings.VOUCHER_PERCENT / 100))
            if amount <= 0:
                logger.warning(self.log_message(
                    f"<y>Calculated voucher amount is too small: {amount} BIT</y>"
                ))
                return

            voucher = await self.create_voucher(amount)
            if not voucher:
                return

            if settings.VOUCHER_TARGET_SESSION:
                logger.info(self.log_message(
                    f"Voucher ready for transfer to session <c>{settings.VOUCHER_TARGET_SESSION}</c>"
                ))

        except Exception as e:
            logger.error(self.log_message(f"<r>Error processing vouchers: {str(e)}</r>"))

    async def run(self) -> None:
        self._is_first_run = await check_is_first_run(self.session_name)
        if self._is_first_run:
            logger.info(self.log_message("<y>First run detected</y>"))
            await append_recurring_session(self.session_name)

        random_delay = uniform(1, settings.SESSION_START_DELAY)
        logger.info(self.log_message(f"Bot will start in <ly>{int(random_delay)}s</ly>"))
        await asyncio.sleep(random_delay)

        access_token_created_time = 0
        init_data = None

        proxy_conn = {'connector': ProxyConnector.from_url(self._current_proxy)} if self._current_proxy else {}
        async with CloudflareScraper(headers=HEADERS, timeout=aiohttp.ClientTimeout(60), **proxy_conn) as http_client:
            self._http_client = http_client

            while True:
                session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
                if not await self.check_and_update_proxy(session_config):
                    logger.warning(self.log_message('<y>Failed to find working proxy. Sleep 5 minutes.</y>'))
                    await asyncio.sleep(300)
                    continue

                token_live_time = 7200
                try:
                    if time() - access_token_created_time >= token_live_time or not init_data:
                        init_data = await self.get_tg_web_data()
                        self._init_data = init_data

                        if not init_data:
                            logger.warning(self.log_message('<y>Failed to get webview URL. Retrying in 5 minutes</y>'))
                            await asyncio.sleep(300)
                            continue

                        await self.auth(init_data)
                        user_info = await self.get_me()
                        logger.info(self.log_message(f"<g>Logged in as {user_info.get('username', 'None')}</g>"))
                        await self.check_and_join_clan()

                        access_token_created_time = time()

                    if await self.check_daily_checkin():
                        await self.perform_daily_checkin()
                        await asyncio.sleep(uniform(2, 5))

                    await self.process_in_game_tasks()
                    await asyncio.sleep(uniform(2, 5))

                    if not await self.check_speedtest():
                        if self._next_available:
                            if settings.DUROV_JUMP_ENABLED:
                                tickets = await self.check_tickets()
                                if tickets > 0:
                                    logger.info(self.log_message(f"<ly>Found {tickets} tickets for Durov Jump</ly>"))
                                    while tickets > 0:
                                        await self.play_durov_jump()
                                        await asyncio.sleep(uniform(2, 5))
                                        tickets = await self.check_tickets()

                            if settings.ENABLE_VOUCHERS:
                                await self.process_vouchers()

                            wait_time = (self._next_available - datetime.now(timezone.utc)).total_seconds()
                            logger.info(self.log_message(f"<y>Waiting {format_duration(wait_time)} before next attempt...</y>"))
                            await asyncio.sleep(wait_time + uniform(1, 30))
                            continue
                    else:
                        wait_time = uniform(40, 60)
                        logger.info(self.log_message(f"Waiting <ly>{int(wait_time)}</ly> seconds before submitting results..."))
                        await asyncio.sleep(wait_time)

                        await self.submit_speedtest()

                except InvalidSession as e:
                    raise

                except Exception as error:
                    sleep_duration = uniform(60, 120)
                    logger.error(self.log_message(f"<r>Unknown error: {error}. Sleeping for {int(sleep_duration)}</r>"))
                    await asyncio.sleep(sleep_duration)

    async def check_daily_checkin(self) -> bool:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/users/me/check-ins/available"
        auth_headers = get_auth_headers(self._access_token)
        
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        user_agent = session_config.get('user_agent', '')
        
        auth_headers.update({
            'X-Device-Platform': session_config.get('api', {}).get('device_platform', 'ios'),
            'X-Device-Model': user_agent
        })

        async with self._http_client.get(url, headers=auth_headers) as response:
            if response.status != 200:
                logger.error(self.log_message(f"Failed to check daily checkin: {response.status}"))
                return False

            data = await response.json()
            return data.get("next_available_at") is None

    async def perform_daily_checkin(self) -> bool:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/users/me/check-ins"
        auth_headers = get_auth_headers(self._access_token)
        
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        user_agent = session_config.get('user_agent', '')
        
        auth_headers.update({
            'X-Device-Platform': session_config.get('api', {}).get('device_platform', 'ios'),
            'X-Device-Model': user_agent
        })

        async with self._http_client.post(url, headers=auth_headers) as response:
            if response.status in (200, 204):
                logger.info(self.log_message("<g>Daily check-in completed successfully!</g>"))
                return True
            else:
                logger.error(self.log_message(f"Failed to perform daily checkin: {response.status}"))
                return False

    async def check_tickets(self) -> int:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        url = f"{self.BASE_URL}/users/me"
        auth_headers = get_auth_headers(self._access_token)

        async with self._http_client.get(url, headers=auth_headers) as response:
            if response.status != 200:
                logger.error(self.log_message(f"Failed to check tickets: {response.status}"))
                return 0

            data = await response.json()
            return data.get("tickets", 0)

    async def play_durov_jump(self) -> bool:
        if not self._http_client or not self._access_token:
            raise InvalidSession("No access token or HTTP client not initialized")

        if not settings.DUROV_JUMP_ENABLED:
            return False

        tickets = await self.check_tickets()
        url = f"{self.BASE_URL}/durov-jump"
        
        session_config = config_utils.get_session_config(self.session_name, CONFIG_PATH)
        user_agent = session_config.get('user_agent', '')
        device_platform = session_config.get('api', {}).get('device_platform', 'ios')

        auth_headers = {
            'Authorization': f'Bearer {self._access_token}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Accept-Language': 'ru',
            'X-Device-Platform': device_platform,
            'X-Device-Model': user_agent,
            'User-Agent': user_agent,
            'Origin': 'https://bitappprod.com',
            'Referer': 'https://bitappprod.com/durov-jump',
            'lang': 'en'
        }

        logger.info(self.log_message(f"<ly>Starting Durov Jump game... Available tickets: {tickets}</ly>"))
        
        game_duration = uniform(settings.DUROV_JUMP_DURATION[0], settings.DUROV_JUMP_DURATION[1])
        start_time = datetime.now(timezone.utc)
        await asyncio.sleep(game_duration)
        end_time = datetime.now(timezone.utc)
        payload = {
            "score": randint(settings.DUROV_JUMP_SCORE[0], settings.DUROV_JUMP_SCORE[1]),
            "start_at": start_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "end_at": end_time.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        }

        try:
            async with self._http_client.post(url, headers=auth_headers, json=payload) as response:
                if response.status == 200:
                    data = await response.json()
                    reward = data.get("amount", 0)
                    if reward > 0:
                        logger.info(self.log_message(
                            f"<g>Durov Jump completed! Score: {payload['score']}, "
                            f"Duration: {int(game_duration)}s, Reward: {reward}</g>"
                        ))
                        return True
                    else:
                        logger.warning(self.log_message("<y>Durov Jump completed but received no reward!</y>"))
                else:
                    logger.error(self.log_message(f"Failed to submit Durov Jump score: {response.status}"))
                    if response.status == 422:
                        error_data = await response.json()
                        logger.error(self.log_message(f"Error details: {error_data}"))
                return False
        except Exception as e:
            logger.error(self.log_message(f"Error in Durov Jump: {str(e)}"))
            return False

async def run_tapper(tg_client: UniversalTelegramClient):
    runner = Tapper(tg_client=tg_client)
    try:
        await runner.run()
    except InvalidSession as e:
        logger.error(runner.log_message(f"Invalid Session: {e}"))
