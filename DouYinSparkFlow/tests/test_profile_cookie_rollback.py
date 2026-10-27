import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from core import browser
from core import tasks


PROFILE = {
    "enabled": True,
    "root": "profile-root",
    "seedCookiesWhenEmpty": True,
    "syncStoredCookiesBeforeRun": True,
    "refreshStoredCookiesAfterLogin": True,
}
AUTH = {"sessionid", "sid_guard"}


def _cookie(name, value):
    return {"name": name, "value": value, "domain": ".douyin.com", "path": "/"}


def _context(current):
    return SimpleNamespace(
        cookies=AsyncMock(return_value=list(current)),
        add_cookies=AsyncMock(),
    )


def _push(context, account, profile=PROFILE):
    return asyncio.run(
        browser.push_stored_cookies_into_profile(context, account, "demo", profile, AUTH)
    )


class ProfileCookieRollbackTests(unittest.TestCase):
    def test_store_copied_from_the_profile_does_not_overwrite_it(self):
        # The store was last copied from the profile; the profile has since
        # been re-issued a newer session that the store has never seen.
        stored = [_cookie("sessionid", "old"), _cookie("sid_guard", "old")]
        account = {
            "cookies": stored,
            browser.PROFILE_COOKIE_FINGERPRINT_KEY: browser.cookie_fingerprint(stored),
        }
        context = _context([_cookie("sessionid", "rotated")])

        self.assertFalse(_push(context, account))
        context.add_cookies.assert_not_awaited()

    def test_cookies_saved_after_the_copy_are_pushed(self):
        copied = [_cookie("sessionid", "old")]
        fresh_login = [_cookie("sessionid", "new")]
        account = {
            "cookies": fresh_login,
            browser.PROFILE_COOKIE_FINGERPRINT_KEY: browser.cookie_fingerprint(copied),
        }
        context = _context([_cookie("sessionid", "old")])

        self.assertTrue(_push(context, account))
        context.add_cookies.assert_awaited_once_with(fresh_login)

    def test_account_without_a_recorded_copy_is_pushed(self):
        stored = [_cookie("sessionid", "v")]
        context = _context([_cookie("sessionid", "v")])

        self.assertTrue(_push(context, {"cookies": stored}))
        context.add_cookies.assert_awaited_once_with(stored)

    def test_profile_that_lost_its_session_is_reseeded(self):
        stored = [_cookie("sessionid", "v")]
        account = {
            "cookies": stored,
            browser.PROFILE_COOKIE_FINGERPRINT_KEY: browser.cookie_fingerprint(stored),
        }
        context = _context([_cookie("ttwid", "x")])

        self.assertTrue(_push(context, account))
        context.add_cookies.assert_awaited_once_with(stored)

    def test_seed_only_policy_leaves_a_logged_in_profile_alone(self):
        stored = [_cookie("sessionid", "new")]
        context = _context([_cookie("sessionid", "old")])
        profile = dict(PROFILE, syncStoredCookiesBeforeRun=False)

        self.assertFalse(_push(context, {"cookies": stored}, profile))
        context.add_cookies.assert_not_awaited()

    def test_fingerprint_ignores_order_and_expiry_but_not_values(self):
        first = [
            dict(_cookie("sessionid", "a"), expires=1),
            _cookie("sid_guard", "b"),
        ]
        reordered = [_cookie("sid_guard", "b"), dict(_cookie("sessionid", "a"), expires=2)]
        changed = [_cookie("sid_guard", "b"), _cookie("sessionid", "c")]

        self.assertEqual(browser.cookie_fingerprint(first), browser.cookie_fingerprint(reordered))
        self.assertNotEqual(browser.cookie_fingerprint(first), browser.cookie_fingerprint(changed))

    def test_refresh_records_the_fingerprint_of_the_copy(self):
        profile_cookies = [_cookie("sessionid", "live")]
        context = SimpleNamespace(cookies=AsyncMock(return_value=profile_cookies))
        stored_account = {"username": "demo", "unique_id": "1001", "cookies": []}
        user = dict(stored_account)

        def fake_update(mutate, force_reload=False):
            result, _changed = mutate([stored_account])
            return result

        with patch.object(tasks, "update_user_data", side_effect=fake_update):
            asyncio.run(tasks.refresh_stored_cookies_from_profile(context, user, "demo"))

        expected = browser.cookie_fingerprint(profile_cookies)
        self.assertEqual(expected, stored_account[browser.PROFILE_COOKIE_FINGERPRINT_KEY])
        self.assertEqual(expected, user[browser.PROFILE_COOKIE_FINGERPRINT_KEY])
        # The next run then leaves the live profile session in place.
        next_run = _context(profile_cookies)
        self.assertFalse(_push(next_run, stored_account))


if __name__ == "__main__":
    unittest.main()
