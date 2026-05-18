from dataclasses import dataclass


@dataclass
class LogEntry:
    timestamp: str
    source: str
    level: str
    message: str
