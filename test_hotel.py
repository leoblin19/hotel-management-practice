import tempfile
import unittest
from pathlib import Path

from hotel import (
    HotelError, add_room, available_rooms, change_booking_status,
    connect, reserve, set_room_status,
)


class HotelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = Path(self.temp.name) / "test.sqlite3"
        self.con = connect(self.db)
        self.room = add_room(self.con, "101", "Стандарт", 2)

    def tearDown(self):
        self.con.close()
        self.temp.cleanup()

    def test_booking_occupies_interval(self):
        reserve(self.con, self.room, "Гость 1", "2026-07-01", "2026-07-04")
        self.assertEqual([], available_rooms(self.con, "2026-07-02", "2026-07-03"))

    def test_overlapping_booking_rejected(self):
        reserve(self.con, self.room, "Гость 1", "2026-07-01", "2026-07-04")
        with self.assertRaisesRegex(HotelError, "пересекается"):
            reserve(self.con, self.room, "Гость 2", "2026-07-03", "2026-07-05")

    def test_adjoining_booking_allowed(self):
        reserve(self.con, self.room, "Гость 1", "2026-07-01", "2026-07-04")
        second = reserve(self.con, self.room, "Гость 2", "2026-07-04", "2026-07-05")
        self.assertGreater(second, 1)

    def test_invalid_dates_rejected(self):
        with self.assertRaises(HotelError):
            reserve(self.con, self.room, "Гость", "2026-07-05", "2026-07-05")

    def test_cancel_releases_interval(self):
        booking = reserve(self.con, self.room, "Гость", "2026-07-01", "2026-07-04")
        change_booking_status(self.con, booking, "cancel")
        self.assertEqual(1, len(available_rooms(self.con, "2026-07-02", "2026-07-03")))

    def test_checkout_sends_room_to_cleaning(self):
        booking = reserve(self.con, self.room, "Гость", "2026-07-01", "2026-07-04")
        change_booking_status(self.con, booking, "checkin")
        change_booking_status(self.con, booking, "checkout")
        self.assertEqual("cleaning", self.con.execute(
            "SELECT status FROM rooms WHERE id=?", (self.room,)
        ).fetchone()[0])

    def test_cleaning_blocks_checkin(self):
        booking = reserve(self.con, self.room, "Гость", "2026-07-01", "2026-07-04")
        set_room_status(self.con, self.room, "cleaning")
        with self.assertRaisesRegex(HotelError, "готовый"):
            change_booking_status(self.con, booking, "checkin")

    def test_maintenance_blocks_reservation(self):
        set_room_status(self.con, self.room, "maintenance")
        with self.assertRaisesRegex(HotelError, "ремонте"):
            reserve(self.con, self.room, "Гость", "2026-07-01", "2026-07-04")

    def test_duplicate_room_rejected(self):
        with self.assertRaisesRegex(HotelError, "существует"):
            add_room(self.con, "101", "Люкс", 2)

    def test_invalid_transition_rejected(self):
        booking = reserve(self.con, self.room, "Гость", "2026-07-01", "2026-07-04")
        with self.assertRaisesRegex(HotelError, "недопустим"):
            change_booking_status(self.con, booking, "checkout")


if __name__ == "__main__":
    unittest.main()
