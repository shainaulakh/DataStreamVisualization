import json
import time

from flask import Flask, Response, jsonify, render_template, request

from acquisition.controller_feed import JOINT_LABELS, JOINTS
from persistence.telemetry_store import TelemetryStore

TICK_SECONDS = 2.0
PAGE_SIZE = 30
WINDOW_SECONDS = 90
CALIBRATION_MINUTES = 60

app = Flask(__name__)
store = TelemetryStore()
if not store.has_history():
    raise SystemExit("Neon has no seed data yet. Run: uv run python -m persistence.bootstrap_db seed")
limits = store.calibration_limits(CALIBRATION_MINUTES)
calibration_last_id, calibration_from, calibration_to = store.calibration_window(CALIBRATION_MINUTES)


def as_event(row) -> str:
    amps = {joint: float(getattr(row, joint)) for joint in JOINTS}
    payload = {
        "id": int(row.sample_id),
        "at": row.sampled_at.strftime("%H:%M:%S"),
        "state": row.state,
        "amps": amps,
        "total": round(sum(amps.values()), 3),
    }
    return f"id: {payload['id']}\ndata: {json.dumps(payload)}\n\n"


def replay(after_id: int):
    cursor = after_id
    while True:
        batch = store.page(cursor, PAGE_SIZE)
        if batch.empty:
            yield "event: finished\ndata: {}\n\n"
            return
        for row in batch.itertuples(index=False):
            yield as_event(row)
            cursor = int(row.sample_id)
            time.sleep(TICK_SECONDS)


@app.get("/")
def monitor():
    settings = {
        "limits": limits,
        "labels": JOINT_LABELS,
        "window": WINDOW_SECONDS,
        "tick": TICK_SECONDS,
        "calibrationLastId": calibration_last_id,
        "calibration": f"{calibration_from:%H:%M}–{calibration_to:%H:%M} UTC",
    }
    return render_template("monitor.html", joints=JOINTS, labels=JOINT_LABELS, limits=limits, settings=settings)


@app.get("/api/stream")
def stream():
    resume_from = request.headers.get("Last-Event-ID") or request.args.get("after", "0")
    return Response(
        replay(int(resume_from)),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/history")
def history():
    peaks = store.minute_peaks()
    return jsonify(
        minutes=peaks["minute"].dt.strftime("%d %b %H:%M").tolist(),
        peak=peaks["peak_amps"].round(2).tolist(),
        mean=peaks["mean_amps"].round(2).tolist(),
        readings=store.count("seed"),
    )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8060, threaded=True)
