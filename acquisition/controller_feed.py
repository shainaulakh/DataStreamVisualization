import time
from pathlib import Path

import pandas as pd

JOINTS = [f"j{n}" for n in range(1, 9)]
JOINT_LABELS = {joint: f"Axis {joint[1:]}" for joint in JOINTS}
SOURCE_COLUMNS = {"Time": "sampled_at", "Trait": "trait"} | {f"Axis #{n}": f"j{n}" for n in range(1, 9)}


def tidy(chunk: pd.DataFrame) -> pd.DataFrame:
    frame = chunk.rename(columns=SOURCE_COLUMNS)[["sampled_at", "trait", *JOINTS]].copy()
    frame["sampled_at"] = pd.to_datetime(frame["sampled_at"], utc=True)
    frame[JOINTS] = frame[JOINTS].astype("float64")
    moving = frame[JOINTS].ne(0).any(axis=1)
    frame["state"] = moving.map({True: "RUNNING", False: "IDLE"})
    return frame


def read_shift(csv_path: str | Path, chunk_rows: int = 5_000) -> pd.DataFrame:
    chunks = pd.read_csv(csv_path, usecols=list(SOURCE_COLUMNS), chunksize=chunk_rows)
    return pd.concat(map(tidy, chunks), ignore_index=True)


class StreamingSimulator:
    def __init__(self, csv_path: str | Path, interval: float = 2.0):
        if interval < 0:
            raise ValueError("interval cannot be negative")
        self.csv_path = Path(csv_path)
        self.interval = interval
        self.delivered = 0
        self._rows = pd.read_csv(self.csv_path, usecols=list(SOURCE_COLUMNS), chunksize=1)
        self._last_tick: float | None = None

    def nextDataPoint(self) -> pd.DataFrame | None:
        self._hold_until_due()
        try:
            chunk = next(self._rows)
        except StopIteration:
            self._rows.close()
            return None
        self._last_tick = time.monotonic()
        self.delivered += 1
        return tidy(chunk).reset_index(drop=True)

    def _hold_until_due(self) -> None:
        if self._last_tick is None:
            return
        wait = self.interval - (time.monotonic() - self._last_tick)
        if wait > 0:
            time.sleep(wait)
