"""The interface between the ERP and the MES: a message queue with at-least-once delivery.

A message is delivered after `delay_s` of plant time. What can happen to it, as on a real interface:

* the receiver is down (the nightly maintenance of the ERP): the message waits and is tried again after
  `retry_min`; the later messages of the same direction wait behind it, so they keep their order;
* the receiver processed it, but its answer got lost (`lost_ack`): the sender does not know and sends it
  again. The receiver remembers the IDs it has processed (integration_inbox) and does not book it twice;
* the receiver refuses it for a business reason (a product locked for a stock count): no retry would help,
  the message goes to the error queue. The key user (or somebody on the web) processes it again later,
  or discards it. The error queue does not stop the messages behind it.
"""

from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from mes_lab.mes import models as db
from mes_lab.model.schema import InterfaceRules

log = logging.getLogger("mes_lab.erp.bus")

TO_MES, TO_ERP = "ERP>MES", "MES>ERP"
RECEIVER = {TO_MES: "MES", TO_ERP: "ERP"}
BATCH = 200


class Rejected(Exception):
    """The receiver refuses the message for a business reason: it goes to the error queue."""


Handler = Callable[[Session, db.IntegrationMessage, datetime], str]
Availability = Callable[[datetime], str | None]


def note(msg: db.IntegrationMessage, at: datetime, text: str) -> None:
    msg.log = [*(msg.log or []), [at.isoformat(), text]][-20:]


def post(session: Session, direction: str, kind: str, ref: str, payload: dict, at: datetime,
         delay_s: float) -> db.IntegrationMessage:
    msg = db.IntegrationMessage(id=str(uuid.uuid4()), direction=direction, kind=kind, ref=ref, payload=payload,
                                status="PENDING", attempts=0, duplicates=0, created_at=at,
                                next_at=at + timedelta(seconds=delay_s), log=[])
    note(msg, at, "sent")
    session.add(msg)
    return msg


class Bus:
    def __init__(self, rules: InterfaceRules):
        self.rules = rules
        self.handlers: dict[str, Handler] = {}
        self.available: dict[str, Availability] = {}

    def connect(self, receiver: str, handler: Handler, available: Availability | None = None) -> None:
        self.handlers[receiver] = handler
        if available is not None:
            self.available[receiver] = available

    def post(self, session: Session, direction: str, kind: str, ref: str, payload: dict,
             at: datetime) -> db.IntegrationMessage:
        return post(session, direction, kind, ref, payload, at, self.rules.delay_s)

    def _wait(self, msg: db.IntegrationMessage) -> timedelta:
        waits = self.rules.retry_min
        return timedelta(minutes=waits[min(max(msg.attempts, 1), len(waits)) - 1])

    def _lost(self, msg: db.IntegrationMessage) -> bool:
        """The answer of the first delivery got lost (decided by the message ID: the same every time)."""
        return msg.attempts == 1 and random.Random(f"ack:{msg.id}").random() < self.rules.lost_ack

    def deliver(self, session: Session, at: datetime) -> int:
        """Delivers what is due; returns the number of deliveries tried."""
        tried = 0
        for direction, receiver in RECEIVER.items():
            handler = self.handlers.get(receiver)
            if handler is None:
                continue
            due = session.scalars(select(db.IntegrationMessage)
                                  .where(db.IntegrationMessage.direction == direction,
                                         db.IntegrationMessage.status == "PENDING",
                                         db.IntegrationMessage.next_at <= at)
                                  .order_by(db.IntegrationMessage.created_at, db.IntegrationMessage.id)
                                  .limit(BATCH)).all()
            for msg in due:
                down = self.available.get(receiver, lambda _: None)(at)
                tried += 1
                msg.attempts += 1
                if down:                                   # the later messages wait behind it (their order)
                    msg.error = down
                    msg.next_at = at + self._wait(msg)
                    note(msg, at, f"not delivered: {down}; next try {msg.next_at.isoformat()}")
                    break
                if session.get(db.IntegrationInbox, msg.id) is not None:
                    msg.duplicates += 1
                    self._done(msg, at, msg.result or "processed before")
                    note(msg, at, f"delivered again: {receiver} knows the message ID, nothing booked twice")
                    continue
                try:
                    with session.begin_nested():
                        result = handler(session, msg, at)
                        session.add(db.IntegrationInbox(message_id=msg.id, receiver=receiver, processed_at=at))
                except Rejected as e:
                    msg.status = "ERROR"
                    msg.error = str(e)[:300]
                    note(msg, at, f"refused by {receiver}: {e} -> error queue")
                    log.info("%s %s %s refused: %s", msg.kind, msg.ref, receiver, e)
                    continue
                msg.result = result[:300]
                if self._lost(msg):
                    msg.error = f"no answer from the {receiver} (timeout)"
                    msg.next_at = at + self._wait(msg)
                    note(msg, at, f"processed, but the answer got lost; the sender tries again {msg.next_at.isoformat()}")
                    continue
                self._done(msg, at, result)
        session.flush()
        return tried

    @staticmethod
    def _done(msg: db.IntegrationMessage, at: datetime, result: str) -> None:
        msg.status = "DONE"
        msg.done_at = at
        msg.result = result[:300]
        msg.error = None
        note(msg, at, f"done: {result}"[:300])

    # ------------------------------------------------------------------ the error queue
    @staticmethod
    def reprocess(session: Session, msg: db.IntegrationMessage, at: datetime, who: str) -> None:
        if msg.status != "ERROR":
            raise ValueError(f"{msg.kind} {msg.ref} is {msg.status}, not in the error queue")
        msg.status = "PENDING"
        msg.next_at = at
        note(msg, at, f"processed again by {who}")

    @staticmethod
    def discard(session: Session, msg: db.IntegrationMessage, at: datetime, who: str) -> None:
        if msg.status not in ("ERROR", "PENDING"):
            raise ValueError(f"{msg.kind} {msg.ref} is {msg.status}")
        msg.status = "DISCARDED"
        msg.done_at = at
        note(msg, at, f"discarded by {who}")

    def reprocess_errors(self, session: Session, at: datetime, who: str) -> int:
        rows = session.scalars(select(db.IntegrationMessage).where(db.IntegrationMessage.status == "ERROR")).all()
        for msg in rows:
            self.reprocess(session, msg, at, who)
        return len(rows)
