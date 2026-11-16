"""Bounded lock stripes for the single-process deployment."""
import threading

_locks = [threading.RLock() for _ in range(128)]


def course_lock(user_id, course_id):
    return _locks[hash((user_id, course_id)) % len(_locks)]
