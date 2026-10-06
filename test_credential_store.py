"""
deployment/credential_store.py and the dashboard endpoints that use it.

The properties that matter: a secret goes IN and never comes back out, the store cannot be used to
write arbitrary keys, and a credential can never be set over an unencrypted connection.
"""
import json
import os
import stat
import tempfile
import unittest

from deployment.credential_store import (KNOWN_CREDENTIALS, credential, load, save, status,
                                         store_path)


class _Settings:
    COINDCX_API_KEY = "from-settings-key"
    KITE_API_KEY = "kite-settings-key"


class TestSaveAndLoad(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_a_saved_credential_reads_back(self):
        save(self.d, {"COINDCX_API_KEY": "abc123"})
        self.assertEqual(load(self.d)["COINDCX_API_KEY"], "abc123")

    def test_unknown_names_are_ignored(self):
        # the form must not be usable to write arbitrary keys into the file
        save(self.d, {"COINDCX_API_KEY": "ok", "EVIL": "x", "__import__": "y"})
        self.assertEqual(set(load(self.d)), {"COINDCX_API_KEY"})

    def test_a_blank_value_removes_rather_than_storing_an_empty_string(self):
        save(self.d, {"COINDCX_API_KEY": "abc"})
        save(self.d, {"COINDCX_API_KEY": "  "})
        self.assertNotIn("COINDCX_API_KEY", load(self.d))

    def test_saving_one_credential_leaves_the_others_alone(self):
        save(self.d, {"COINDCX_API_KEY": "a", "COINDCX_API_SECRET": "b"})
        save(self.d, {"COINDCX_API_KEY": "c"})
        self.assertEqual(load(self.d)["COINDCX_API_SECRET"], "b")

    def test_the_file_is_owner_only(self):
        save(self.d, {"COINDCX_API_KEY": "abc"})
        mode = stat.S_IMODE(os.stat(store_path(self.d)).st_mode)
        if os.name != "nt":                       # Windows has no POSIX mode to assert on
            self.assertEqual(mode & (stat.S_IRWXG | stat.S_IRWXO), 0)

    def test_a_corrupt_store_is_empty_not_an_exception(self):
        with open(store_path(self.d), "w", encoding="utf-8") as f:
            f.write("{ truncated")
        self.assertEqual(load(self.d), {})
        self.assertEqual(credential("COINDCX_API_KEY", self.d, _Settings), "from-settings-key")


class TestResolution(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def test_the_store_wins_over_settings(self):
        save(self.d, {"COINDCX_API_KEY": "from-store"})
        self.assertEqual(credential("COINDCX_API_KEY", self.d, _Settings), "from-store")

    def test_settings_is_the_fallback_so_nothing_has_to_be_migrated(self):
        self.assertEqual(credential("KITE_API_KEY", self.d, _Settings), "kite-settings-key")

    def test_an_absent_credential_is_empty_string_not_an_error(self):
        self.assertEqual(credential("COINDCX_API_SECRET", self.d, _Settings), "")


class TestStatusLeaksNothing(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        save(self.d, {"COINDCX_API_KEY": "key-abcdefgh", "COINDCX_API_SECRET": "supersecretvalue"})

    def test_no_stored_value_appears_anywhere_in_the_status(self):
        blob = json.dumps(status(self.d, _Settings))
        for secret in ("key-abcdefgh", "supersecretvalue", "from-settings-key"):
            self.assertNotIn(secret, blob, secret)

    def test_a_secret_never_gets_a_hint_even_a_partial_one(self):
        by_name = {c["name"]: c for c in status(self.d, _Settings)}
        self.assertEqual(by_name["COINDCX_API_SECRET"]["hint"], "")
        self.assertEqual(by_name["COINDCX_API_KEY"]["hint"], "efgh")     # a key is an identifier

    def test_status_reports_where_each_credential_came_from(self):
        by_name = {c["name"]: c for c in status(self.d, _Settings)}
        self.assertEqual(by_name["COINDCX_API_KEY"]["source"], "dashboard")
        self.assertEqual(by_name["KITE_API_KEY"]["source"], "settings.py")
        self.assertEqual(by_name["KITE_TOTP_SECRET"]["source"], "")
        self.assertFalse(by_name["KITE_TOTP_SECRET"]["configured"])

    def test_every_known_credential_is_reported(self):
        self.assertEqual({c["name"] for c in status(self.d, _Settings)},
                         {c["name"] for c in KNOWN_CREDENTIALS})


class TestTheEndpointRefusesPlainHttp(unittest.TestCase):
    def test_the_write_path_checks_tls_and_the_read_path_does_not(self):
        # structural: the refusal is the only thing standing between a typed secret and a cleartext
        # hop across the public internet, so it must not be possible to lose it in a refactor
        import dashboard.server as srv
        with open(srv.__file__, encoding="utf-8") as f:
            source = f.read()
        post = source[source.index('if parsed.path == "/api/credentials":   # Settings tab'):]
        post = post[:post.index('if parsed.path == "/api/live/promote"')]
        self.assertIn("_is_tls()", post)
        self.assertIn("only be set over HTTPS", post)
        # the proto must never be READ from a header -- a client sets those. The string appearing
        # in a comment that says exactly this is fine; reading it is not.
        for header_read in ('headers.get("X-Forwarded-Proto"', "headers.get('X-Forwarded-Proto'",
                            'headers["X-Forwarded-Proto"]'):
            self.assertNotIn(header_read, source, header_read)

    def test_is_tls_reads_the_socket_not_a_header(self):
        import dashboard.server as srv
        with open(srv.__file__, encoding="utf-8") as f:
            source = f.read()
        body = source[source.index("def _is_tls"):]
        body = body[:body.index("\n    def ", 1)]
        self.assertIn("ssl.SSLSocket", body)


if __name__ == "__main__":
    unittest.main()
