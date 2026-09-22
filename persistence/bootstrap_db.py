import argparse
import sys
import time

import numpy as np

from acquisition.controller_feed import JOINTS, read_shift
from persistence.telemetry_store import PROJECT_ROOT, TelemetryStore

CSV_PATH = PROJECT_ROOT / "dataset" / "RMBR4-2_export_test.csv"


def build(store: TelemetryStore) -> None:
    store.initialise()
    print("schema cat_dc ready: equipment, joint, sample, joint_current, sample_wide")


def seed(store: TelemetryStore) -> None:
    build(store)
    started = time.perf_counter()
    loaded = store.seed(read_shift(CSV_PATH))
    if loaded:
        print(f"copied {loaded:,} readings ({loaded * len(JOINTS):,} joint rows) in {time.perf_counter() - started:.1f}s")
    else:
        print("seed rows already present, nothing copied")


def check(store: TelemetryStore) -> None:
    csv = read_shift(CSV_PATH)
    db = store.wide("seed")
    same_rows = len(csv) == len(db)
    same_span = (csv.sampled_at.min(), csv.sampled_at.max()) == (db.sampled_at.min(), db.sampled_at.max())
    worst_gap = np.abs(csv[JOINTS].to_numpy() - db[JOINTS].to_numpy()).max() if same_rows else float("nan")
    print(f"rows      csv {len(csv):,} | db {len(db):,}")
    print(f"span      {db.sampled_at.min()} -> {db.sampled_at.max()}")
    print(f"max |diff| between csv and db currents: {worst_gap}")
    print("MATCH" if same_rows and same_span and worst_gap == 0 else "MISMATCH")


def reset(store: TelemetryStore) -> None:
    store.drop_everything()
    print("schema cat_dc dropped")


ACTIONS = {"build": build, "seed": seed, "check": check, "reset": reset}

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create, load and verify the cat_dc schema on Neon.")
    parser.add_argument("action", choices=ACTIONS)
    parser.add_argument("--yes", action="store_true", help="required for reset")
    args = parser.parse_args()
    if args.action == "reset" and not args.yes:
        sys.exit("reset drops every cat_dc table; rerun with --yes to confirm")
    ACTIONS[args.action](TelemetryStore())
