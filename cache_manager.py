import threading
import time
import sqlite3
import pickle
from typing import Any, Optional, Dict, Tuple, Union
from collections import OrderedDict
import hashlib
import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from contextlib import contextmanager
from multiprocessing import Manager
import functools

class CacheLevel(Enum):
    MEMORY = auto()
    SHARED = auto()
    DISK = auto()

class CachePriority(Enum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3

@dataclass
class CacheEntry:
    value: Any
    priority: CachePriority
    last_accessed: float = field(default_factory=time.time)

@dataclass
class CacheMetrics:
    hits: int = 0
    misses: int = 0
    evictions: int = 0
    total_access_time: float = 0.0
    access_count: int = 0

    @property
    def average_access_time(self) -> float:
        return self.total_access_time / self.access_count if self.access_count > 0 else 0.0

    @property
    def hit_rate(self) -> float:
        total_requests = self.hits + self.misses
        return (self.hits / total_requests) * 100 if total_requests > 0 else 0.0

class LRUCache:
    def __init__(self, capacity: int):
        self.capacity = capacity
        self.cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self.lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self.lock:
            if key not in self.cache:
                return None
            self.cache.move_to_end(key)
            entry = self.cache[key]
            entry.last_accessed = time.time()
            return entry.value

    def set(self, key: str, value: Any, priority: CachePriority) -> None:
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
            self.cache[key] = CacheEntry(value, priority)
            if len(self.cache) > self.capacity:
                self.cache.popitem(last=False)

    def remove(self, key: str) -> None:
        with self.lock:
            self.cache.pop(key, None)

    def clear(self) -> None:
        with self.lock:
            self.cache.clear()

class SharedCache:
    def __init__(self, name: str, size: int):
        self.manager = Manager()
        self.cache = self.manager.dict()
        self.lock = self.manager.Lock()

    def get(self, key: str) -> Optional[Any]:
        with self.lock:
            entry = self.cache.get(key)
            if entry:
                entry.last_accessed = time.time()
                return entry.value
            return None

    def set(self, key: str, value: Any, priority: CachePriority) -> None:
        with self.lock:
            self.cache[key] = CacheEntry(value, priority)

    def remove(self, key: str) -> None:
        with self.lock:
            self.cache.pop(key, None)

    def clear(self) -> None:
        with self.lock:
            self.cache.clear()

class DiskCache:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute('''CREATE TABLE IF NOT EXISTS cache
                            (key TEXT PRIMARY KEY, value BLOB, priority INTEGER, last_accessed REAL)''')
        self.conn.commit()
        self.lock = threading.Lock()

    @contextmanager
    def _get_cursor(self):
        cursor = self.conn.cursor()
        try:
            yield cursor
            self.conn.commit()
        finally:
            cursor.close()

    def get(self, key: str) -> Optional[Tuple[Any, CachePriority]]:
        with self.lock, self._get_cursor() as cursor:
            cursor.execute("SELECT value, priority FROM cache WHERE key = ?", (key,))
            result = cursor.fetchone()
            if result:
                value, priority = result
                cursor.execute("UPDATE cache SET last_accessed = ? WHERE key = ?", (time.time(), key))
                return pickle.loads(value), CachePriority(priority)
            return None

    def set(self, key: str, value: Any, priority: CachePriority) -> None:
        with self.lock, self._get_cursor() as cursor:
            cursor.execute("INSERT OR REPLACE INTO cache (key, value, priority, last_accessed) VALUES (?, ?, ?, ?)",
                           (key, pickle.dumps(value), priority.value, time.time()))

    def remove(self, key: str) -> None:
        with self.lock, self._get_cursor() as cursor:
            cursor.execute("DELETE FROM cache WHERE key = ?", (key,))

    def clear(self) -> None:
        with self.lock, self._get_cursor() as cursor:
            cursor.execute("DELETE FROM cache")

    def close(self) -> None:
        self.conn.close()

class MultiLevelCache:
    def __init__(self, name: str, memory_size: int, shared_size: int, db_path: str):
        self.name = name
        self.memory_cache = LRUCache(memory_size)
        self.shared_cache = SharedCache(f"{name}_shared", shared_size)
        self.disk_cache = DiskCache(db_path)
        self.metrics = {level: CacheMetrics() for level in CacheLevel}
        self.logger = self._setup_logger()
        self.metric_log_interval = 3600  # Log metrics every hour
        self._start_metric_logging()

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"{self.name}_cache")
        logger.setLevel(logging.DEBUG)

        file_handler = logging.FileHandler(f"{self.name}_cache.log")
        file_handler.setLevel(logging.INFO)
        file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.DEBUG)
        stream_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        stream_handler.setFormatter(stream_formatter)
        logger.addHandler(stream_handler)

        return logger

    def _start_metric_logging(self):
        self.log_cache_metrics()
        threading.Timer(self.metric_log_interval, self._start_metric_logging).start()

    def _hash_key(self, key: str) -> str:
        return hashlib.blake2b(key.encode(), digest_size=16).hexdigest()

    def _update_metrics(self, level: CacheLevel, hit: bool, access_time: float) -> None:
        metrics = self.metrics[level]
        if hit:
            metrics.hits += 1
        else:
            metrics.misses += 1
        metrics.total_access_time += access_time
        metrics.access_count += 1
        self.logger.debug(f"{level.name} Cache - Hit: {hit}, Access Time: {access_time:.6f}s")

    def _get_from_cache(self, key: str, level: CacheLevel) -> Tuple[Optional[Any], Optional[CachePriority], float]:
        start_time = time.time()
        value = priority = None
        try:
            if level == CacheLevel.MEMORY:
                value = self.memory_cache.get(key)
            elif level == CacheLevel.SHARED:
                value = self.shared_cache.get(key)
            elif level == CacheLevel.DISK:
                result = self.disk_cache.get(key)
                if result:
                    value, priority = result
        except Exception as e:
            self.logger.error(f"Error accessing cache at {level.name} level: {e}")

        access_time = time.time() - start_time
        hit = value is not None
        self._update_metrics(level, hit, access_time)
        return value, priority or CachePriority.MEDIUM, access_time

    def _update_caches(self, key: str, value: Any, priority: CachePriority, found_level: CacheLevel) -> None:
        if found_level == CacheLevel.DISK:
            self.shared_cache.set(key, value, priority)
        self.memory_cache.set(key, value, priority)

    def _evict_if_full(self, level: CacheLevel) -> None:
        if level == CacheLevel.MEMORY and len(self.memory_cache.cache) >= self.memory_cache.capacity:
            _, evicted_entry = self.memory_cache.cache.popitem(last=False)
            self.metrics[level].evictions += 1
            self.logger.debug(f"Evicted item from {level.name} cache")

    def get(self, key: str, default: Any = None) -> Any:
        hashed_key = self._hash_key(key)
        for level in CacheLevel:
            value, priority, _ = self._get_from_cache(hashed_key, level)
            if value is not None:
                self._update_caches(hashed_key, value, priority, level)
                return value
        return default

    def set(self, key: str, value: Any, priority: CachePriority = CachePriority.MEDIUM) -> None:
        hashed_key = self._hash_key(key)
        self._evict_if_full(CacheLevel.MEMORY)
        self.memory_cache.set(hashed_key, value, priority)
        if priority.value >= CachePriority.MEDIUM.value:
            self.shared_cache.set(hashed_key, value, priority)
        if priority.value == CachePriority.HIGH.value:
            self.disk_cache.set(hashed_key, value, priority)
        self.logger.info(f"Cached value for key: {key} with priority: {priority.name}")

    def invalidate(self, key: str) -> None:
        hashed_key = self._hash_key(key)
        self.memory_cache.remove(hashed_key)
        self.shared_cache.remove(hashed_key)
        self.disk_cache.remove(hashed_key)
        self.logger.info(f"Invalidated cache for key: {key}")

    def clear(self) -> None:
        self.memory_cache.clear()
        self.shared_cache.clear()
        self.disk_cache.clear()
        self.logger.info("Cleared all cache levels")

    def get_metrics(self) -> Dict[CacheLevel, CacheMetrics]:
        return self.metrics

    def log_cache_metrics(self):
        for level, metrics in self.metrics.items():
            self.logger.info(f"{level.name} Cache - Hit Rate: {metrics.hit_rate:.2f}%, "
                             f"Hits: {metrics.hits}, Misses: {metrics.misses}, "
                             f"Evictions: {metrics.evictions}, "
                             f"Avg Access Time: {metrics.average_access_time:.6f}s")

    def close(self) -> None:
        self.disk_cache.close()
        self.logger.info("Closed all cache levels")

    def warm_up_cache(self, data: Dict[str, Any]) -> None:
        for key, value in data.items():
            hashed_key = self._hash_key(key)
            self.set(hashed_key, value, CachePriority.HIGH)
        self.logger.info(f"Cache warmed up with {len(data)} items.")

def cache(ttl: Optional[int] = None, priority: CachePriority = CachePriority.MEDIUM):
    def decorator(func):
        @functools.wraps(func)
        def wrapped(*args, **kwargs):
            from flask import current_app
            cache_manager = current_app.cache_manager
            cache_key = f"{func.__module__}:{func.__name__}:{hash(frozenset(args))}{hash(frozenset(kwargs.items()))}"
            result = cache_manager.get(cache_key)
            if result is None:
                result = func(*args, **kwargs)
                cache_manager.set(cache_key, result, priority)
            return result
        return wrapped
    return decorator

def track_performance(func):
    @functools.wraps(func)
    def wrapped(*args, **kwargs):
        start_time = time.time()
        result = func(*args, **kwargs)
        execution_time = time.time() - start_time
        logging.info(f"Function '{func.__name__}' executed in {execution_time:.4f} seconds.")
        return result
    return wrapped