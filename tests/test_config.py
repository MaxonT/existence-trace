import os
import tempfile
import unittest

from etrace import config
from etrace.config import ConfigError

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class LoadConfigTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, text):
        path = os.path.join(self.dir, "trace.toml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return path

    def test_empty_file_gets_defaults(self):
        cfg = config.load(self.write(""))
        self.assertEqual(cfg.timezone, "America/Chicago")
        self.assertEqual(cfg.preset, "default")
        self.assertEqual(cfg.language, "en")
        self.assertEqual(cfg.outreach, "note")
        self.assertFalse(cfg.email_enabled)
        self.assertEqual(cfg.tasks["calibrate"], ["09:00"])
        self.assertEqual(cfg.tasks["feedback"], ["12:30", "20:30"])
        self.assertEqual(cfg.tasks["daily"], ["19:30"])
        self.assertEqual(cfg.tasks["g"], ["20:30"])
        self.assertEqual(cfg.tasks["audit"], ["Sun 22:15"])
        self.assertEqual(cfg.credentials, {"email": None, "notion": None})
        self.assertTrue(os.path.isabs(cfg.data_dir))
        self.assertEqual(cfg.source, os.path.abspath(os.path.join(self.dir, "trace.toml")))

    def test_full_config(self):
        cfg = config.load(self.write(
            'timezone = "Europe/Berlin"\n'
            'data_dir = "data"\n'
            'preset = "tiger-method"\n'
            'language = "en"\n'
            'outreach = "note+email"\n'
            'git_remote = "../remote.git"\n'
            '[tasks]\n'
            'calibrate = "08:15"\n'
            'feedback = "13:00"\n'
            'audit = "Sat 21:00"\n'
            '[credentials]\n'
            'email = "secrets/email.toml"\n'
            'notion = "secrets/notion.toml"\n'
        ))
        self.assertEqual(cfg.timezone, "Europe/Berlin")
        self.assertEqual(cfg.data_dir, os.path.join(self.dir, "data"))
        self.assertEqual(cfg.preset, "tiger-method")
        self.assertEqual(cfg.language, "en")
        self.assertTrue(cfg.email_enabled)
        self.assertEqual(cfg.git_remote, "../remote.git")
        self.assertEqual(cfg.tasks["calibrate"], ["08:15"])
        self.assertEqual(cfg.tasks["feedback"], ["13:00"])
        self.assertEqual(cfg.tasks["daily"], ["19:30"])
        self.assertEqual(cfg.tasks["audit"], ["Sat 21:00"])
        self.assertEqual(cfg.credentials["email"], os.path.join(self.dir, "secrets", "email.toml"))
        self.assertEqual(cfg.credentials["notion"], os.path.join(self.dir, "secrets", "notion.toml"))

    def test_git_remote_defaults_to_none_and_must_be_string(self):
        self.assertIsNone(config.load(self.write("")).git_remote)
        with self.assertRaisesRegex(ConfigError, "git_remote"):
            config.load(self.write('git_remote = ""\n'))
        with self.assertRaisesRegex(ConfigError, "git_remote"):
            config.load(self.write("git_remote = 3\n"))

    def test_unknown_top_level_key_named(self):
        with self.assertRaisesRegex(ConfigError, "'timezon'"):
            config.load(self.write('timezon = "UTC"\n'))

    def test_unknown_task_key_named(self):
        with self.assertRaisesRegex(ConfigError, "tasks.'weekly'"):
            config.load(self.write('[tasks]\nweekly = "10:00"\n'))

    def test_unknown_credential_key_named(self):
        with self.assertRaisesRegex(ConfigError, "credentials.'slack'"):
            config.load(self.write('[credentials]\nslack = "x"\n'))

    def test_invalid_choices(self):
        for text, needle in (
            ('preset = "mine"\n', "preset"),
            ('language = "fr"\n', "language"),
            ('outreach = "sms"\n', "outreach"),
            ('timezone = "Mars/Base"\n', "timezone"),
            ('preset = 3\n', "preset"),
        ):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ConfigError, needle):
                    config.load(self.write(text))

    def test_invalid_times(self):
        for text in (
            '[tasks]\ndaily = "7:30"\n',
            '[tasks]\ndaily = "24:00"\n',
            '[tasks]\nfeedback = []\n',
            '[tasks]\ng = 2030\n',
            '[tasks]\naudit = "22:15"\n',
            '[tasks]\naudit = "Sunday 22:15"\n',
        ):
            with self.subTest(text=text):
                with self.assertRaises(ConfigError):
                    config.load(self.write(text))

    def test_email_outreach_requires_credentials(self):
        with self.assertRaisesRegex(ConfigError, "credentials.email"):
            config.load(self.write('outreach = "note+email"\n'))

    def test_missing_file_and_bad_toml(self):
        with self.assertRaisesRegex(ConfigError, "not found"):
            config.load(os.path.join(self.dir, "nope.toml"))
        with self.assertRaisesRegex(ConfigError, "invalid TOML"):
            config.load(self.write("preset = \n"))

    def test_example_file_loads_and_documents_every_key(self):
        path = os.path.join(REPO, "examples", "trace.toml")
        cfg = config.load(path)
        self.assertEqual(cfg.tasks, config.Config().tasks)
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        for key in config._TOP_KEYS + config.TASKS + config._CREDENTIAL_KEYS:
            self.assertRegex(text, rf"(?m)^(# )?\[?{key}\]?( =|$)", key)


if __name__ == "__main__":
    unittest.main()
