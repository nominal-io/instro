"""SQLite sink implementation."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from instro.unstable.lib.sinks.sink import Sink


class SQLiteSink(Sink):
	"""Persist consumed items into a SQLite table."""

	def __init__(self, db_path: str | Path, table_name: str = "records"):
		super().__init__()
		self.db_path = str(db_path)
		self.table_name = table_name
		self._conn: sqlite3.Connection | None = None
		self._lock = threading.Lock()

	def _connect(self) -> sqlite3.Connection:
		conn = sqlite3.connect(self.db_path, check_same_thread=False)
		conn.execute(
			f"""
			CREATE TABLE IF NOT EXISTS {self.table_name} (
				id INTEGER PRIMARY KEY AUTOINCREMENT,
				record_type TEXT NOT NULL,
				payload TEXT NOT NULL,
				created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
			)
			"""
		)
		conn.commit()
		return conn

	def start(self, consumer):
		if self._conn is None:
			self._conn = self._connect()
		super().start(consumer)

	def stop(self):
		super().stop()
		with self._lock:
			if self._conn is not None:
				self._conn.close()
				self._conn = None

	def _serialize_item(self, item: Any) -> tuple[str, str]:
		if dataclasses.is_dataclass(item):
			payload = dataclasses.asdict(item)
			record_type = type(item).__name__
		elif isinstance(item, dict):
			payload = item
			record_type = item.get("type", "dict")
		elif hasattr(item, "__dict__"):
			payload = {k: v for k, v in vars(item).items() if not k.startswith("_")}
			record_type = type(item).__name__
		else:
			payload = {"value": item}
			record_type = type(item).__name__

		return record_type, json.dumps(payload, default=str)

	def process_item(self, item: Any):
		if self._conn is None:
			raise RuntimeError("SQLiteSink is not connected.")

		record_type, payload = self._serialize_item(item)
		with self._lock:
			self._conn.execute(
				f"INSERT INTO {self.table_name} (record_type, payload) VALUES (?, ?)",
				(record_type, payload),
			)
			self._conn.commit()
