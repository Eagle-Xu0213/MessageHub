"""Background processing for the personal task pipeline."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from messagehub.config import BASE_DIR, CN_TZ, DB_PATH
from messagehub import task_pipeline, mail, desktop_notify


class TaskScheduler:
    def __init__(self, db_path=DB_PATH, state_path=None, poll_seconds=10, hourly_seconds=3600):
        self.db_path = Path(db_path)
        self.capture_path = Path(state_path or BASE_DIR / 'runtime' / 'moect_live_state.json')
        self.poll_seconds = poll_seconds
        self.hourly_seconds = hourly_seconds
        self.started_at = datetime.now(CN_TZ)
        self._stop = threading.Event()
        self._thread = None
        self.state = {'running': False, 'capture': '等待新快照', 'last_error': None}

    def start(self):
        if self._thread and self._thread.is_alive():
            return {'started': False, 'reason': 'already running'}
        self.started_at = datetime.now(CN_TZ)
        self._stop.clear()
        self.state['running'] = True
        self._thread = threading.Thread(target=self._run, daemon=True, name='personal-task-scheduler')
        self._thread.start()
        return {'started': True}

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)
        self.state['running'] = False
        return {'stopped': True}

    def snapshot_ready(self):
        try:
            state = json.loads(self.capture_path.read_text(encoding='utf-8'))
            completed = task_pipeline._time(state.get('snapshot_completed_at'))
            return bool(state.get('processed_generation') and completed and completed >= self.started_at
                        and state.get('last_stats', {}).get('drained'))
        except (OSError, ValueError, TypeError):
            return False

    def _run(self):
        next_hour = time.monotonic() + self.hourly_seconds
        while not self._stop.is_set():
            try:
                with sqlite3.connect(str(self.db_path), timeout=30) as conn:
                    batch = task_pipeline.process_batch(conn)
                    # Drain queued message batches before summaries or alerts.
                    if batch['processed'] >= 200:
                        continue
                    urgent_email, digest_email = mail.task_email_preferences()
                    task_pipeline.send_due_alerts(conn, None, desktop_notify.notify_urgent)
                    if urgent_email:
                        task_pipeline.send_due_alerts(
                            conn, lambda event: mail.send_urgent_email({'event': event}),
                            None,
                        )
                    if time.monotonic() >= next_hour:
                        next_hour = time.monotonic() + self.hourly_seconds
                        if digest_email:
                            task_pipeline.send_current_digest(conn, mail.send_digest_email)
                    ready = self.snapshot_ready()
                    self.state['capture'] = '补扫完成' if ready else '等待微信新快照'
                    self.state['last_error'] = None
            except Exception as exc:
                self.state['last_error'] = f'{type(exc).__name__}: {exc}'
            self._stop.wait(self.poll_seconds)


_scheduler = TaskScheduler()


def get_scheduler():
    return _scheduler
