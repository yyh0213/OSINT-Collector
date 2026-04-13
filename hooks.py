# hooks.py
import asyncio


class HookManager:
    """시스템 전반의 이벤트를 관리하는 이벤트 버스(Event Bus)"""

    def __init__(self):
        self._hooks = {}

    def register(self, event_name: str, callback):
        """특정 이벤트에 실행할 함수(구독자)를 등록"""
        if event_name not in self._hooks:
            self._hooks[event_name] = []
        self._hooks[event_name].append(callback)

    async def trigger(self, event_name: str, *args, **kwargs):
        """이벤트 발생 시 등록된 모든 함수를 백그라운드(비동기)로 실행"""
        if event_name in self._hooks:
            for callback in self._hooks[event_name]:
                # 메인 프로세스(수집기)를 멈추지 않고 백그라운드 작업으로 던짐
                asyncio.create_task(callback(*args, **kwargs))
