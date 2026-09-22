import io
import os
from contextlib import AbstractContextManager
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    Connection,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Index,
    MetaData,
    SmallInteger,
    Table,
    Text,
    create_engine,
    delete,
    func,
    insert,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import insert as upsert
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool
from sqlalchemy.schema import CreateSchema, DropSchema

from acquisition.controller_feed import JOINTS

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "cat_dc"
ROBOT_ID = 1

catalog = MetaData(schema=SCHEMA)

equipment = Table(
    "equipment",
    catalog,
    Column("equipment_id", SmallInteger, primary_key=True),
    Column("name", Text, nullable=False, unique=True),
    Column("type_class", Text, nullable=False),
    Column("instance", Text, nullable=False),
)

joint = Table(
    "joint",
    catalog,
    Column("joint_id", SmallInteger, primary_key=True),
    Column("label", Text, nullable=False, unique=True),
)

sample = Table(
    "sample",
    catalog,
    Column("sample_id", BigInteger, Identity(always=False), primary_key=True),
    Column("equipment_id", SmallInteger, ForeignKey(equipment.c.equipment_id), nullable=False),
    Column("sampled_at", DateTime(timezone=True), nullable=False),
    Column("trait", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("origin", Text, nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("state IN ('RUNNING', 'IDLE')", name="sample_state_known"),
    CheckConstraint("origin IN ('seed', 'stream')", name="sample_origin_known"),
    Index("sample_origin_time", "origin", "sampled_at"),
)

joint_current = Table(
    "joint_current",
    catalog,
    Column("sample_id", BigInteger, ForeignKey(sample.c.sample_id, ondelete="CASCADE"), primary_key=True),
    Column("joint_id", SmallInteger, ForeignKey(joint.c.joint_id), primary_key=True),
    Column("amps", Float, nullable=False),
)

PIVOT_COLUMNS = ",\n       ".join(
    f"max(c.amps) FILTER (WHERE c.joint_id = {n}) AS j{n}" for n in range(1, 9)
)

WIDE_VIEW = f"""
CREATE OR REPLACE VIEW {SCHEMA}.sample_wide AS
SELECT s.sample_id, s.sampled_at, s.trait, s.state, s.origin,
       {PIVOT_COLUMNS}
FROM {SCHEMA}.sample s
JOIN {SCHEMA}.joint_current c USING (sample_id)
GROUP BY s.sample_id, s.sampled_at, s.trait, s.state, s.origin
"""


class TelemetryStore:
    def __init__(self, database_url: str | None = None):
        load_dotenv(PROJECT_ROOT / ".env")
        raw_url = database_url or os.environ.get("DATABASE_URL")
        if not raw_url:
            raise RuntimeError("DATABASE_URL is not set. Copy .env.example to .env and add the Neon connection string.")
        url = make_url(raw_url).set(drivername="postgresql+psycopg")
        self.engine = create_engine(url, poolclass=NullPool, connect_args={"prepare_threshold": None})

    def connection(self) -> AbstractContextManager[Connection]:
        return self.engine.begin()

    def initialise(self, robot_name: str = "RMBR4-2") -> None:
        with self.connection() as conn:
            conn.execute(CreateSchema(SCHEMA, if_not_exists=True))
            catalog.create_all(conn)
            conn.execute(text(WIDE_VIEW))
            conn.execute(
                upsert(equipment)
                .values(equipment_id=ROBOT_ID, name=robot_name, type_class="Robot", instance="Material handling")
                .on_conflict_do_nothing()
            )
            conn.execute(
                upsert(joint)
                .values([{"joint_id": n, "label": f"Axis #{n}"} for n in range(1, 9)])
                .on_conflict_do_nothing()
            )

    def drop_everything(self) -> None:
        with self.connection() as conn:
            conn.execute(DropSchema(SCHEMA, cascade=True, if_exists=True))

    def has_history(self) -> bool:
        with self.connection() as conn:
            table_exists = conn.execute(
                text("SELECT to_regclass(:table) IS NOT NULL"), {"table": f"{SCHEMA}.sample"}
            ).scalar_one()
        return bool(table_exists) and self.count("seed") > 0

    def count(self, origin: str | None = None) -> int:
        query = select(func.count()).select_from(sample)
        if origin:
            query = query.where(sample.c.origin == origin)
        with self.connection() as conn:
            return conn.execute(query).scalar_one()

    def seed(self, shift: pd.DataFrame) -> int:
        if self.count("seed"):
            return 0
        headers = shift.assign(sample_id=range(1, len(shift) + 1), equipment_id=ROBOT_ID, origin="seed")
        header_cols = ["sample_id", "equipment_id", "sampled_at", "trait", "state", "origin"]
        currents = headers.melt(id_vars="sample_id", value_vars=JOINTS, var_name="joint", value_name="amps")
        currents["joint_id"] = currents["joint"].str.removeprefix("j").astype(int)
        current_cols = ["sample_id", "joint_id", "amps"]

        raw = self.engine.raw_connection()
        try:
            pg = raw.driver_connection
            with pg.cursor() as cur:
                for table_name, frame, cols in (
                    ("sample", headers, header_cols),
                    ("joint_current", currents, current_cols),
                ):
                    buffer = io.StringIO()
                    frame[cols].to_csv(buffer, index=False, header=False)
                    copy_sql = f"COPY {SCHEMA}.{table_name} ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv)"
                    with cur.copy(copy_sql) as copy:
                        copy.write(buffer.getvalue())
                cur.execute(
                    f"SELECT setval(pg_get_serial_sequence('{SCHEMA}.sample', 'sample_id'), "
                    f"(SELECT max(sample_id) FROM {SCHEMA}.sample))"
                )
            pg.commit()
        finally:
            raw.close()
        return len(headers)

    def write(self, conn: Connection, record: pd.DataFrame, origin: str = "stream") -> list[int]:
        new_ids = []
        for row in record.to_dict("records"):
            sample_id = conn.execute(
                insert(sample)
                .values(
                    equipment_id=ROBOT_ID,
                    sampled_at=row["sampled_at"],
                    trait=row["trait"],
                    state=row["state"],
                    origin=origin,
                )
                .returning(sample.c.sample_id)
            ).scalar_one()
            conn.execute(
                insert(joint_current),
                [{"sample_id": sample_id, "joint_id": n, "amps": row[f"j{n}"]} for n in range(1, 9)],
            )
            new_ids.append(sample_id)
        return new_ids

    def purge_stream(self) -> int:
        with self.connection() as conn:
            return conn.execute(delete(sample).where(sample.c.origin == "stream")).rowcount

    def ask(self, sql: str, **params) -> pd.DataFrame:
        with self.connection() as conn:
            return pd.read_sql(text(sql), conn, params=params)

    def wide(self, origin: str = "seed") -> pd.DataFrame:
        frame = self.ask(
            f"SELECT * FROM {SCHEMA}.sample_wide WHERE origin = :origin ORDER BY sample_id",
            origin=origin,
        )
        frame["sampled_at"] = pd.to_datetime(frame["sampled_at"], utc=True)
        return frame

    def page(self, after_id: int = 0, size: int = 30) -> pd.DataFrame:
        long = self.ask(
            f"""
            SELECT s.sample_id, s.sampled_at, s.state, c.joint_id, c.amps
            FROM {SCHEMA}.sample s
            JOIN {SCHEMA}.joint_current c USING (sample_id)
            WHERE s.sample_id IN (
                SELECT sample_id FROM {SCHEMA}.sample
                WHERE origin = 'seed' AND sample_id > :after
                ORDER BY sample_id LIMIT :size
            )
            """,
            after=after_id,
            size=size,
        )
        if long.empty:
            return long
        wide = long.pivot_table(index=["sample_id", "sampled_at", "state"], columns="joint_id", values="amps")
        wide.columns = [f"j{n}" for n in wide.columns]
        wide = wide.reset_index().sort_values("sample_id", ignore_index=True)
        wide["sampled_at"] = pd.to_datetime(wide["sampled_at"], utc=True)
        return wide

    def calibration_window(self, minutes: int = 60) -> tuple[int, pd.Timestamp, pd.Timestamp]:
        window = self.ask(
            f"""
            SELECT max(sample_id) AS last_id, min(sampled_at) AS first_at, max(sampled_at) AS last_at
            FROM {SCHEMA}.sample
            WHERE origin = 'seed'
              AND sampled_at < (SELECT min(sampled_at) FROM {SCHEMA}.sample WHERE origin = 'seed')
                               + make_interval(mins => :minutes)
            """,
            minutes=minutes,
        ).iloc[0]
        return int(window["last_id"]), pd.Timestamp(window["first_at"]), pd.Timestamp(window["last_at"])

    def calibration_limits(self, minutes: int = 60) -> dict[str, float]:
        limits = self.ask(
            f"""
            SELECT c.joint_id, max(c.amps) AS limit_amps
            FROM {SCHEMA}.joint_current c
            JOIN {SCHEMA}.sample s USING (sample_id)
            WHERE s.origin = 'seed' AND s.state = 'RUNNING'
              AND s.sampled_at < (SELECT min(sampled_at) FROM {SCHEMA}.sample WHERE origin = 'seed')
                                 + make_interval(mins => :minutes)
            GROUP BY c.joint_id
            ORDER BY c.joint_id
            """,
            minutes=minutes,
        )
        return {f"j{row.joint_id}": float(row.limit_amps) for row in limits.itertuples()}

    def minute_peaks(self) -> pd.DataFrame:
        total = " + ".join(JOINTS)
        return self.ask(
            f"""
            SELECT date_trunc('minute', sampled_at AT TIME ZONE 'UTC') AS minute,
                   max({total}) AS peak_amps,
                   avg({total}) AS mean_amps
            FROM {SCHEMA}.sample_wide
            WHERE origin = 'seed'
            GROUP BY 1
            ORDER BY 1
            """
        )
