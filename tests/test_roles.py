"""Role ordering and the PUBLIC_READ_ACCESS switch (aggregator/models.py,
aggregator/permissions.py). No database: User objects are never added."""
import os
import unittest
from unittest import mock

from aggregator.models import ROLE_ADMIN, ROLE_READER, ROLE_SCRAPER, User
from aggregator.permissions import public_read_access_from_env


class HasRoleTests(unittest.TestCase):
    def user(self, role):
        u = User(username="x", email="x@example.com")
        u.set_role(role)
        return u

    def test_each_role_includes_the_ones_below(self):
        self.assertTrue(self.user(ROLE_ADMIN).has_role(ROLE_SCRAPER))
        self.assertTrue(self.user(ROLE_ADMIN).has_role(ROLE_READER))
        self.assertTrue(self.user(ROLE_SCRAPER).has_role(ROLE_READER))
        self.assertTrue(self.user(ROLE_READER).has_role(ROLE_READER))

    def test_lower_roles_do_not_reach_higher_ones(self):
        self.assertFalse(self.user(ROLE_READER).has_role(ROLE_SCRAPER))
        self.assertFalse(self.user(ROLE_SCRAPER).has_role(ROLE_ADMIN))

    def test_unknown_stored_role_has_no_access(self):
        u = User(username="x", email="x@example.com")
        u.role = "superuser"
        self.assertFalse(u.has_role(ROLE_READER))

    def test_set_role_keeps_is_admin_in_step(self):
        u = self.user(ROLE_ADMIN)
        self.assertTrue(u.is_admin)
        u.set_role(ROLE_SCRAPER)
        self.assertFalse(u.is_admin)

    def test_set_role_rejects_unknown_roles(self):
        with self.assertRaises(ValueError):
            self.user(ROLE_READER).set_role("owner")


class PublicReadSwitchTests(unittest.TestCase):
    def test_off_unless_set(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PUBLIC_READ_ACCESS", None)
            self.assertFalse(public_read_access_from_env())

    def test_truthy_values(self):
        for value in ("true", "TRUE", "1", "yes", "on", " true "):
            with mock.patch.dict(os.environ, {"PUBLIC_READ_ACCESS": value}):
                self.assertTrue(public_read_access_from_env(), value)

    def test_other_values_stay_off(self):
        for value in ("false", "0", "no", "", "maybe"):
            with mock.patch.dict(os.environ, {"PUBLIC_READ_ACCESS": value}):
                self.assertFalse(public_read_access_from_env(), value)


if __name__ == "__main__":
    unittest.main()
