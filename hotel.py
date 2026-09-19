"""Учебный локальный прототип учета номерного фонда и бронирований.

Не предназначен для обработки реальных персональных данных гостей.
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from pathlib import Path


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS rooms (
    id INTEGER PRIMARY KEY,
    number TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    status TEXT NOT NULL DEFAULT 'ready'
        CHECK (status IN ('ready', 'cleaning', 'maintenance'))
);
CREATE TABLE IF NOT EXISTS bookings (
    id INTEGER PRIMARY KEY,
    room_id INTEGER NOT NULL REFERENCES rooms(id),
    guest_label TEXT NOT NULL,
    check_in TEXT NOT NULL,
    check_out TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'booked'
        CHECK (status IN ('booked', 'checked_in', 'checked_out', 'cancelled')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (check_in < check_out)
);
CREATE INDEX IF NOT EXISTS idx_bookings_room_dates
    ON bookings(room_id, check_in, check_out);
"""


class HotelError(ValueError):
    pass


def connect(db_path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(SCHEMA)
    return connection


def parse_dates(check_in: str, check_out: str) -> None:
    try:
        arrival = date.fromisoformat(check_in)
        departure = date.fromisoformat(check_out)
    except ValueError as exc:
        raise HotelError("Даты задаются в формате ГГГГ-ММ-ДД") from exc
    if arrival >= departure:
        raise HotelError("Дата выезда должна быть позже даты заезда")


def add_room(con: sqlite3.Connection, number: str, category: str, capacity: int) -> int:
    if not number.strip() or not category.strip() or capacity < 1:
        raise HotelError("Нужны номер, категория и положительная вместимость")
    try:
        cur = con.execute(
            "INSERT INTO rooms(number, category, capacity) VALUES (?, ?, ?)",
            (number.strip(), category.strip(), capacity),
        )
    except sqlite3.IntegrityError as exc:
        raise HotelError("Номер уже существует") from exc
    return int(cur.lastrowid)


def set_room_status(con: sqlite3.Connection, room_id: int, status: str) -> None:
    if status not in {"ready", "cleaning", "maintenance"}:
        raise HotelError("Недопустимый статус номера")
    if status == "maintenance" and con.execute(
        "SELECT 1 FROM bookings WHERE room_id=? AND status='checked_in'",
        (room_id,),
    ).fetchone():
        raise HotelError("Нельзя вывести заселенный номер в ремонт")
    result = con.execute("UPDATE rooms SET status=? WHERE id=?", (status, room_id))
    if result.rowcount != 1:
        raise HotelError("Номер не найден")


def available_rooms(con: sqlite3.Connection, check_in: str, check_out: str) -> list[sqlite3.Row]:
    parse_dates(check_in, check_out)
    return con.execute(
        """SELECT r.* FROM rooms r
           WHERE r.status != 'maintenance'
             AND NOT EXISTS (
               SELECT 1 FROM bookings b
               WHERE b.room_id = r.id AND b.status IN ('booked','checked_in')
                 AND b.check_in < ? AND ? < b.check_out
             ) ORDER BY r.number""",
        (check_out, check_in),
    ).fetchall()


def reserve(con: sqlite3.Connection, room_id: int, guest_label: str,
            check_in: str, check_out: str) -> int:
    parse_dates(check_in, check_out)
    if not guest_label.strip():
        raise HotelError("Требуется условное обозначение гостя")
    con.execute("BEGIN IMMEDIATE")
    try:
        room = con.execute("SELECT status FROM rooms WHERE id=?", (room_id,)).fetchone()
        if room is None:
            raise HotelError("Номер не найден")
        if room["status"] == "maintenance":
            raise HotelError("Номер находится в ремонте")
        conflict = con.execute(
            """SELECT 1 FROM bookings
               WHERE room_id=? AND status IN ('booked','checked_in')
                 AND check_in < ? AND ? < check_out""",
            (room_id, check_out, check_in),
        ).fetchone()
        if conflict:
            raise HotelError("Интервал пересекается с действующей бронью")
        cur = con.execute(
            "INSERT INTO bookings(room_id,guest_label,check_in,check_out) VALUES (?,?,?,?)",
            (room_id, guest_label.strip(), check_in, check_out),
        )
        con.execute("COMMIT")
        return int(cur.lastrowid)
    except Exception:
        con.execute("ROLLBACK")
        raise


def change_booking_status(con: sqlite3.Connection, booking_id: int, action: str) -> None:
    transitions = {
        "checkin": ("booked", "checked_in"),
        "checkout": ("checked_in", "checked_out"),
        "cancel": ("booked", "cancelled"),
    }
    if action not in transitions:
        raise HotelError("Неизвестное действие")
    old, new = transitions[action]
    con.execute("BEGIN IMMEDIATE")
    try:
        booking = con.execute(
            """SELECT b.status, r.status AS room_status FROM bookings b
               JOIN rooms r ON r.id=b.room_id WHERE b.id=?""", (booking_id,)
        ).fetchone()
        if booking is None:
            raise HotelError("Бронь не найдена")
        if booking["status"] != old:
            raise HotelError(f"Переход {booking['status']} → {new} недопустим")
        if action == "checkin" and booking["room_status"] != "ready":
            raise HotelError("Заселение возможно только в готовый номер")
        con.execute("UPDATE bookings SET status=? WHERE id=?", (new, booking_id))
        if action == "checkout":
            con.execute(
                "UPDATE rooms SET status='cleaning' WHERE id=(SELECT room_id FROM bookings WHERE id=?)",
                (booking_id,),
            )
        con.execute("COMMIT")
    except Exception:
        con.execute("ROLLBACK")
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="hotel_demo.sqlite3")
    sub = parser.add_subparsers(dest="command", required=True)
    room = sub.add_parser("add-room")
    room.add_argument("number")
    room.add_argument("category")
    room.add_argument("capacity", type=int)
    status = sub.add_parser("room-status")
    status.add_argument("room_id", type=int)
    status.add_argument("status", choices=["ready", "cleaning", "maintenance"])
    avail = sub.add_parser("available")
    avail.add_argument("check_in")
    avail.add_argument("check_out")
    booking = sub.add_parser("reserve")
    booking.add_argument("room_id", type=int)
    booking.add_argument("guest_label")
    booking.add_argument("check_in")
    booking.add_argument("check_out")
    for name in ("checkin", "checkout", "cancel"):
        action = sub.add_parser(name)
        action.add_argument("booking_id", type=int)
    sub.add_parser("list")
    args = parser.parse_args()
    try:
        with connect(args.db) as con:
            if args.command == "add-room":
                print("Создан номер", add_room(con, args.number, args.category, args.capacity))
            elif args.command == "room-status":
                set_room_status(con, args.room_id, args.status)
                print("Статус номера обновлен")
            elif args.command == "available":
                for row in available_rooms(con, args.check_in, args.check_out):
                    print(row["id"], row["number"], row["category"], row["status"])
            elif args.command == "reserve":
                print("Создана бронь", reserve(con, args.room_id, args.guest_label,
                                              args.check_in, args.check_out))
            elif args.command in {"checkin", "checkout", "cancel"}:
                change_booking_status(con, args.booking_id, args.command)
                print("Статус брони обновлен")
            elif args.command == "list":
                for row in con.execute(
                    """SELECT b.id,r.number,b.guest_label,b.check_in,b.check_out,b.status
                       FROM bookings b JOIN rooms r ON r.id=b.room_id ORDER BY b.id"""
                ):
                    print(*row)
    except HotelError as exc:
        parser.exit(2, f"Ошибка: {exc}\n")


if __name__ == "__main__":
    main()
