"""
Реестр платежей, уже обработанных вебхуком ЮКассы.
Используется для досрочного выхода из фонового polling-таска в payment.py.
"""
from asyncio import Event as _Event

# payment_id (str) → asyncio.Event, который сигналит что вебхук сработал
_handled: dict[str, _Event] = {}


def get_or_create(payment_id: str) -> _Event:
    """Вернуть Event для payment_id (создать если нет)."""
    if payment_id not in _handled:
        _handled[payment_id] = _Event()
    return _handled[payment_id]


def mark_handled(payment_id: str) -> None:
    """Вызывается из вебхука: сигналим что платёж обработан."""
    event = get_or_create(payment_id)
    event.set()


def is_handled(payment_id: str) -> bool:
    """Быстрая синхронная проверка."""
    ev = _handled.get(payment_id)
    return ev is not None and ev.is_set()


def cleanup(payment_id: str) -> None:
    """Удаляем запись после завершения таска (необязательно, но экономит память)."""
    _handled.pop(payment_id, None)