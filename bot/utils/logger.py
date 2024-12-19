from rich.console import Console
from rich.theme import Theme
from rich.markup import escape
from datetime import datetime
from bot.config import settings

custom_theme = Theme({
    'info': 'cyan',
    'warning': 'yellow',
    'error': 'red',
    'critical': 'red reverse',
    'success': 'green',
    'timestamp': 'white',
    'ly': 'yellow',
    'y': 'yellow',
    'g': 'green',
    'r': 'red',
    'c': 'cyan'
})

console = Console(theme=custom_theme)

class Logger:
    @staticmethod
    def _get_timestamp() -> str:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    def _convert_tags(self, message: str) -> str:
        """Конвертирует HTML-подобные теги в формат rich."""
        replacements = {
            '<ly>': '[ly]',
            '</ly>': '[/ly]',
            '<y>': '[y]',
            '</y>': '[/y]',
            '<g>': '[g]',
            '</g>': '[/g]',
            '<r>': '[r]',
            '</r>': '[/r]',
            '<c>': '[c]',
            '</c>': '[/c]'
        }
        
        for old, new in replacements.items():
            message = message.replace(old, new)
            
        return message

    def debug(self, message: str) -> None:
        """Логирование отладочных сообщений."""
        if settings.DEBUG_LOGGING:
            message = self._convert_tags(message)
            console.print(
                f"[timestamp]{self._get_timestamp()}[/timestamp]"
                f" | [info]DEBUG[/info]    | {message}"
            )

    def info(self, message: str) -> None:
        message = self._convert_tags(message)
        console.print(
            f"[timestamp]{self._get_timestamp()}[/timestamp]"
            f" | [info]INFO[/info]     | {message}"
        )

    def warning(self, message: str) -> None:
        message = self._convert_tags(message)
        console.print(
            f"[timestamp]{self._get_timestamp()}[/timestamp]"
            f" | [warning]WARNING[/warning]  | {message}"
        )

    def error(self, message: str) -> None:
        message = self._convert_tags(message)
        console.print(
            f"[timestamp]{self._get_timestamp()}[/timestamp]"
            f" | [error]ERROR[/error]    | {message}"
        )

    def critical(self, message: str) -> None:
        message = self._convert_tags(message)
        console.print(
            f"[timestamp]{self._get_timestamp()}[/timestamp]"
            f" | [critical]CRITICAL[/critical] | {message}"
        )

    def success(self, message: str) -> None:
        message = self._convert_tags(message)
        console.print(
            f"[timestamp]{self._get_timestamp()}[/timestamp]"
            f" | [success]SUCCESS[/success]  | {message}"
        )

    def trace(self, message: str) -> None:
        if settings.DEBUG_LOGGING:
            with open(f"logs/err_tracebacks_{datetime.now().date()}.txt", 'a') as f:
                f.write(f"{self._get_timestamp()} | TRACE | {message}\n")

logger = Logger()

def log_error(text: str) -> None:
    """
    Логирование ошибок с трейсбеком в файл при включенном DEBUG_LOGGING.
    
    Args:
        text: Текст ошибки
    """
    if settings.DEBUG_LOGGING:
        logger.trace(text)
    logger.error(text)
