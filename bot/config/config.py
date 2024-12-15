from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Dict, Tuple
from enum import Enum


class TaskType(str, Enum):
    SUBSCRIBE_TELEGRAM = "subscribe_telegram"
    SOCIAL_NETWORK = "social_network"
    JOIN_CLAN = "join_clan"
    HOMESCREEN = "homescreen"
    STORY = "story"
    ACTIVATE_MINING_BOT = "activate_mining_bot"
    ADSGRAM = "adsgram"
    REFERRALS = "referrals"
    PROMOTE_BLOCKCHAIN = "promote_blockchain"


class TaskCategory(str, Enum):
    IN_GAME = "in_game"
    DAILY = "daily"
    PARTNER = "partner"
    REFERRALS = "referrals"


class TaskConfig:
    def __init__(self, attempts: int, delay: int, enabled: bool = True):
        self.attempts = attempts
        self.delay = delay
        self.enabled = enabled


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    ACTION_DELAY: Tuple[int, int] = (2, 10)
    SESSION_WAIT_DELAY: Tuple[int, int] = (1, 80)
    API_ID: int = None
    API_HASH: str = None
    GLOBAL_CONFIG_PATH: str = "TG_FARM"

    FIX_CERT: bool = False

    SESSION_START_DELAY: int = 360

    REF_ID: str = 'ref_MjI4NjE4Nzk5'
    SUBSCRIBE_TELEGRAM: bool = False
    SESSIONS_PER_PROXY: int = 1
    USE_PROXY: bool = True
    DISABLE_PROXY_REPLACE: bool = False

    DEVICE_PARAMS: bool = False

    DEBUG_LOGGING: bool = False

    AUTO_UPDATE: bool = True
    CHECK_UPDATE_INTERVAL: int = 300
    BLACKLISTED_SESSIONS: str = ""

    CLAN_NAME: str = "MAINE Crypto"

    DOWNLOAD_SPEED: Tuple[int, int] = (50, 250)
    UPLOAD_SPEED: Tuple[int, int] = (10, 50)

    ENABLE_VOUCHERS: bool = False
    VOUCHER_MIN_BALANCE: int = 10
    VOUCHER_PERCENT: float = 10.0
    VOUCHER_TARGET_SESSION: str = ""
    VOUCHER_STORAGE_FILE: str = "vouchers.json"

    DUROV_JUMP_SCORE: Tuple[int, int] = (300, 1556)
    DUROV_JUMP_DURATION: Tuple[int, int] = (60, 180)
    DUROV_JUMP_ENABLED: bool = False

    TASK_CONFIGS: Dict[str, TaskConfig] = {
        TaskType.SUBSCRIBE_TELEGRAM: TaskConfig(attempts=10, delay=5, enabled=False),
        TaskType.SOCIAL_NETWORK: TaskConfig(attempts=3, delay=2, enabled=True),
        TaskType.JOIN_CLAN: TaskConfig(attempts=3, delay=2, enabled=True),
        TaskType.HOMESCREEN: TaskConfig(attempts=4, delay=3, enabled=True),
        TaskType.STORY: TaskConfig(attempts=4, delay=3, enabled=True),
        TaskType.ACTIVATE_MINING_BOT: TaskConfig(attempts=4, delay=3, enabled=False),
        TaskType.ADSGRAM: TaskConfig(attempts=4, delay=3, enabled=False),
        TaskType.REFERRALS: TaskConfig(attempts=1, delay=1, enabled=True),
        TaskType.PROMOTE_BLOCKCHAIN: TaskConfig(attempts=1, delay=1, enabled=False)
    }

    @property
    def blacklisted_sessions(self) -> List[str]:
        return [s.strip() for s in self.BLACKLISTED_SESSIONS.split(',') if s.strip()]

    def get_task_config(self, task_type: str) -> TaskConfig:
        config = self.TASK_CONFIGS.get(task_type, TaskConfig(4, 3, False))
        if task_type == TaskType.SUBSCRIBE_TELEGRAM:
            config.enabled = self.SUBSCRIBE_TELEGRAM
        return config


settings = Settings()
