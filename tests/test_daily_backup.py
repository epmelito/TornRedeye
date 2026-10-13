"""Offline orchestration tests; no cloud clients or credentials are used."""
from contextlib import redirect_stdout
from datetime import datetime, timezone
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from tools import daily_backup as daily

NOW = datetime(2026, 10, 13, 3, 23, tzinfo=timezone.utc)
PRIVATE = 'private-evidence-and-secret-signature'
SAS = urlencode(dict(sp='rcl', spr='https', sr='c', sig=PRIVATE,
                     skoid='synthetic-object', sktid='synthetic-tenant',
                     skt='2026-10-13T03:18:00Z', ske='2026-10-13T04:38:00Z',
                     sks='b', skv='2022-11-02',
                     st='2026-10-13T03:18:00Z', se='2026-10-13T04:38:00Z'))


class DailyBackupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.environment = {
            'DAILY_BACKUPS_ENABLED': 'true', 'GITHUB_REPOSITORY_ID': '1409429829',
            'GITHUB_REPOSITORY_OWNER_ID': '188345459', 'GITHUB_REF': 'refs/heads/main',
            'GITHUB_EVENT_NAME': 'schedule', 'RUNNER_TEMP': self.temporary.name,
            'BACKUP_AWS_BUCKET': 'synthetic-private-bucket', 'BACKUP_AWS_OWNER': '123456789012',
            'BACKUP_AWS_REGION': 'eu-north-1', 'BACKUP_AZURE_ACCOUNT': 'syntheticaccount',
            'BACKUP_AZURE_CONTAINER': 'synthetic-backups', 'BACKUP_BASELINE_CATALOG_SHA256': 'a'*64,
        }
        self.calls = []
        self.sas = SAS
        self.code = 0
        self.output = json.dumps({'complete': True, 'private_manifest': PRIVATE}).encode()
        self.failure = None

    def process(self, command, **kwargs):
        self.calls.append((command, kwargs))
        if self.failure is not None and len(self.calls) == self.failure[0]:
            raise self.failure[1]
        if command[0] == 'az':
            return SimpleNamespace(stdout=self.sas, returncode=0)
        self.scratch = Path(command[command.index('--scratch-parent')+1])
        self.assertTrue(self.scratch.is_dir())
        (self.scratch / 'private-manifest.json').write_text(PRIVATE)
        kwargs['stdout'].write(self.output)
        kwargs['stderr'].write(PRIVATE.encode())
        return SimpleNamespace(returncode=self.code)

    def invoke(self):
        log = StringIO()
        with patch.dict(os.environ, self.environment, clear=True), \
                patch.object(daily.subprocess, 'run', side_effect=self.process), \
                patch.object(daily, 'datetime') as clock, \
                patch.object(daily.os, 'umask'), redirect_stdout(log):
            clock.now.return_value = NOW
            status = daily.main()
        self.assertNotIn(PRIVATE, log.getvalue())
        self.assertNotIn(SAS, log.getvalue())
        self.assertNotIn(self.temporary.name, log.getvalue())
        private = Path(self.temporary.name) / 'tornredeye-backup'
        if private.exists():
            self.assertEqual(list(private.iterdir()), [])
        return status, log.getvalue()

    def test_verified_success_keeps_credentials_and_evidence_private(self):
        status, log = self.invoke()
        self.assertEqual(status, 0)
        self.assertIn('persisted evidence verified', log)
        az, az_options = self.calls[0]
        for flag in ('--as-user', '--https-only', '--only-show-errors'):
            self.assertIn(flag, az)
        for flag, value in (('--auth-mode', 'login'), ('--permissions', 'rcl'),
                            ('--start', '2026-10-13T03:18:00Z'),
                            ('--expiry', '2026-10-13T04:38:00Z')):
            self.assertEqual(az[az.index(flag)+1], value)
        self.assertEqual(az_options['timeout'], 120)
        runner, options = self.calls[1]
        self.assertIn('--live-access-approved', runner)
        self.assertEqual(runner[runner.index('--max-seconds')+1], '3300')
        self.assertEqual(options['timeout'], 3600)
        self.assertEqual(options['env']['TORNREDEYE_AZURE_SAS'], SAS)
        self.assertNotIn(SAS, ' '.join(runner))
        self.assertFalse(self.scratch.exists())

    def test_manual_run_uses_the_same_gates(self):
        self.environment['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
        self.assertEqual(self.invoke()[0], 0)

    def test_disabled_untrusted_or_debug_execution_never_calls_cloud_cli(self):
        for key, value in (
                ('DAILY_BACKUPS_ENABLED', ''), ('DAILY_BACKUPS_ENABLED', 'TRUE'),
                ('GITHUB_REF', 'refs/heads/feature/test'), ('GITHUB_REF', 'refs/tags/main'),
                ('GITHUB_REPOSITORY_ID', '999'), ('GITHUB_REPOSITORY_OWNER_ID', '999'),
                ('GITHUB_EVENT_NAME', 'pull_request'), ('RUNNER_DEBUG', '1')):
            with self.subTest(key=key, value=value):
                original = self.environment.get(key)
                self.environment[key] = value
                self.calls.clear()
                self.assertEqual(self.invoke()[0], 1)
                self.assertEqual(self.calls, [])
                if original is None:
                    del self.environment[key]
                else:
                    self.environment[key] = original

    def test_configuration_injection_and_missing_pin_fail_before_cloud_access(self):
        for key, value in (('BACKUP_AZURE_ACCOUNT', 'account;echo secret'),
                           ('BACKUP_AZURE_CONTAINER', 'bad--name'),
                           ('BACKUP_BASELINE_CATALOG_SHA256', ''), ('BACKUP_AWS_OWNER', '123')):
            with self.subTest(key=key):
                original = self.environment[key]
                self.environment[key] = value
                self.assertEqual(self.invoke()[0], 1)
                self.assertEqual(self.calls, [])
                self.environment[key] = original

    def test_scratch_inside_git_fails_before_cloud_access(self):
        self.environment['RUNNER_TEMP'] = str(daily.ROOT)
        self.assertEqual(self.invoke()[0], 1)
        self.assertEqual(self.calls, [])

    def test_missing_overprivileged_or_non_delegation_sas_never_runs_backup(self):
        for sas in ('', SAS.replace('sp=rcl', 'sp=rcwl'), SAS+'&sp=rcl',
                    SAS.replace('skoid=', 'missing='), SAS+'\n'+PRIVATE,
                    SAS.replace('se=2026-10-13', 'se=2026-10-14')):
            with self.subTest(sas=sas):
                self.sas = sas
                self.calls.clear()
                self.assertEqual(self.invoke()[0], 1)
                self.assertEqual(len(self.calls), 1)

    def test_cli_failure_and_timeout_hide_captured_credentials(self):
        for error in (subprocess.CalledProcessError(1, ['az'], output=SAS, stderr=PRIVATE),
                      subprocess.TimeoutExpired(['az'], 120, output=SAS, stderr=PRIVATE)):
            with self.subTest(error=type(error).__name__):
                self.failure = (1, error)
                self.calls.clear()
                status, log = self.invoke()
                self.assertEqual(status, 1)
                self.assertIn('Azure SAS generation', log)
                self.assertEqual(len(self.calls), 1)

    def test_incomplete_failed_or_invalid_runner_output_fails_visibly(self):
        for code, output in ((0, b'{"complete":false}'), (1, b'{"complete":true}'),
                             (0, b'{"complete":1}'), (0, b'{}'), (0, PRIVATE.encode()),
                             (0, b'x'*(4*1024**2+1))):
            with self.subTest(code=code, length=len(output)):
                self.code, self.output = code, output
                self.calls.clear()
                status, log = self.invoke()
                self.assertEqual(status, 1)
                self.assertIn('incremental runner', log)

    def test_runner_timeout_cleans_private_scratch_and_fails(self):
        self.failure = (2, subprocess.TimeoutExpired(['python'], 3600, output=PRIVATE))
        status, log = self.invoke()
        self.assertEqual(status, 1)
        self.assertIn('incremental runner', log)


if __name__ == '__main__':
    unittest.main()
