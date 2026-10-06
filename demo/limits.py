"""데모 사용량 상한.

세션 상한은 세션 상태가, 일일 상한은 이 모듈의 프로세스 메모리 카운터가 맡는다.
프로세스가 재시작되면 일일 카운터도 초기화되므로, 비용의 최종 상한은
OpenRouter 키에 건 크레딧 한도다.
"""
from __future__ import annotations

import os
import threading
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
SESSION_TURN_LIMIT = int(os.environ.get("DEMO_SESSION_TURN_LIMIT", "20"))
DAILY_TURN_LIMIT = int(os.environ.get("DEMO_DAILY_TURN_LIMIT", "200"))


class DailyCounter:
    """KST 날짜가 바뀌면 0으로 돌아가는 전체 턴 카운터."""

    def __init__(self, limit: int = DAILY_TURN_LIMIT, now=lambda: datetime.now(KST)) -> None:
        self.limit = limit
        self._now = now
        self._lock = threading.Lock()
        self._day = self._now().date()
        self._used = 0

    def try_acquire(self) -> bool:
        """한도 안이면 1턴을 차감하고 True를 돌려준다."""
        with self._lock:
            today = self._now().date()
            if today != self._day:
                self._day, self._used = today, 0
            if self._used >= self.limit:
                return False
            self._used += 1
            return True

    def remaining(self) -> int:
        with self._lock:
            if self._now().date() != self._day:
                return self.limit
            return max(0, self.limit - self._used)


def check_turn(session_used: int, daily: DailyCounter,
               session_limit: int = SESSION_TURN_LIMIT) -> str | None:
    """이번 턴을 진행할 수 없으면 사용자에게 보여 줄 이유를 돌려준다."""
    if session_used >= session_limit:
        return f"이 세션의 대화 한도({session_limit}턴)를 모두 사용했습니다. '대화 시작'으로 새 세션을 열어 주세요."
    if not daily.try_acquire():
        return "오늘 데모 전체 사용량이 한도에 도달했습니다. 내일 다시 시도해 주세요."
    return None
