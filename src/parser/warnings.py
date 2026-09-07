from dataclasses import dataclass, field
from typing import List, Optional

@dataclass
class ParserWarning:
    fid: Optional[int]
    field: str
    message: str
    snippet: Optional[str] = None

class WarningCollector:
    def __init__(self):
        self.warnings: List[ParserWarning] = []

    def warn(self, fid: Optional[int], field: str, message: str, snippet: Optional[str] = None):
        self.warnings.append(ParserWarning(fid=fid, field=field, message=message, snippet=snippet))

    def clear(self):
        self.warnings.clear()
