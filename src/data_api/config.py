import os
from dataclasses import dataclass

MAX_BYTES = 1_048_576
MAX_ROWS = 10_000
MAX_COLUMNS = 100
MAX_CELL_BYTES = 4096
MAX_COLUMN_BYTES = 128
MAX_RESULT_BYTES = 8_388_608
PARSE_SECONDS = 10
UPLOAD_SECONDS = 300
DOWNLOAD_SECONDS = 300
LEASE_SECONDS = 60  # Longer than the 30-second Lambda timeout.
JOB_SECONDS = 1800  # Upload + 15-minute async event age + recovery margin.
RETENTION_SECONDS = 7 * 86400
CONTENT_TYPES = {"csv": "text/csv", "json": "application/json"}


@dataclass(frozen=True)
class Settings:
    table: str
    uploads: str
    results: str

    @classmethod
    def from_env(cls):
        return cls(
            os.environ["TABLE_NAME"], os.environ["UPLOAD_BUCKET"], os.environ["RESULT_BUCKET"]
        )
