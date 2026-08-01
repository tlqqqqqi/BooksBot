from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str
    bot_password: str = "secret"
    flibusta_base_url: str = "https://flibusta.is"
    # Чтение может занимать десятки секунд: flibusta конвертирует форматы на лету
    request_timeout: int = 60
    connect_timeout: int = 10
    user_agent: str = DEFAULT_USER_AGENT
    log_level: str = "INFO"
    max_file_size_mb: int = 50
    db_path: str = "flibusta_bot.db"
    watch_interval_hours: float = 3.0
    # Анонс новой версии уходит один раз, но только когда флаг включён:
    # деплой → потестить руками → SEND_ANNOUNCEMENT=true → рестарт
    send_announcement: bool = False

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024


settings = Settings()  # type: ignore[call-arg]
