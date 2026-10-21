"""Private-output wrapper for the explicitly activated GitHub backup workflow."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import parse_qsl

ROOT = Path(__file__).resolve().parents[1]
CONFIG = {
    'BACKUP_AWS_BUCKET': r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]',
    'BACKUP_AWS_OWNER': r'[0-9]{12}',
    'BACKUP_AWS_REGION': r'[a-z0-9-]+',
    'BACKUP_AZURE_ACCOUNT': r'[a-z0-9]{3,24}',
    'BACKUP_AZURE_CONTAINER': r'[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])',
    'BACKUP_ANCHOR_SHA256': r'[0-9a-f]{64}',
}


def main():
    stage = 'activation/configuration'
    try:
        if not (os.environ.get('DAILY_BACKUPS_ENABLED') == 'true'
                and os.environ.get('GITHUB_REPOSITORY_ID') == '1409429829'
                and os.environ.get('GITHUB_REPOSITORY_OWNER_ID') == '188345459'
                and os.environ.get('GITHUB_REF') == 'refs/heads/main'
                and os.environ.get('GITHUB_EVENT_NAME') in ('schedule', 'workflow_dispatch')):
            raise ValueError()
        config = {name: os.environ.get(name, '') for name in CONFIG}
        if not all(re.fullmatch(pattern, config[name]) for name, pattern in CONFIG.items()):
            raise ValueError()
        if '--' in config['BACKUP_AZURE_CONTAINER'] or os.environ.get('RUNNER_DEBUG') == '1':
            raise ValueError()
        parent = Path(os.environ['RUNNER_TEMP']).resolve()
        if any((part / '.git').exists() for part in (parent, *parent.parents)):
            raise ValueError()
        private = parent / 'tornredeye-backup'
        if private.is_symlink():
            raise ValueError()
        os.umask(0o077)
        private.mkdir(mode=0o700, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=private) as temporary:
            scratch = Path(temporary)
            stage = 'Azure SAS generation'
            now = datetime.now(timezone.utc)
            timestamp = lambda value: value.strftime('%Y-%m-%dT%H:%M:%SZ')
            start = timestamp(now-timedelta(minutes=5))
            expiry = timestamp(now+timedelta(minutes=75))
            sas_result = subprocess.run([
                'az', 'storage', 'container', 'generate-sas',
                '--account-name', config['BACKUP_AZURE_ACCOUNT'],
                '--name', config['BACKUP_AZURE_CONTAINER'],
                '--auth-mode', 'login', '--as-user', '--https-only',
                '--permissions', 'rcl', '--start', start, '--expiry', expiry,
                '--only-show-errors', '--output', 'tsv',
            ], capture_output=True, text=True, timeout=120, check=True)
            sas = sas_result.stdout.strip()
            pairs = parse_qsl(sas, strict_parsing=True)
            fields = dict(pairs)
            if (not sas or any(ord(c) <= 32 or ord(c) >= 127 for c in sas)
                    or len(fields) != len(pairs) or fields.get('sp') != 'rcl'
                    or fields.get('spr') != 'https' or fields.get('sr') != 'c'
                    or fields.get('st') != start or fields.get('se') != expiry
                    or not all(fields.get(key) for key in ('sig', 'skoid', 'sktid', 'skt', 'ske', 'sks', 'skv'))):
                raise ValueError()
            stage = 'incremental runner'
            environment = dict(os.environ, TORNREDEYE_AZURE_SAS=sas)
            command = [sys.executable, '-B', str(ROOT / 'incremental_runner.py'),
                '--bucket', config['BACKUP_AWS_BUCKET'], '--owner', config['BACKUP_AWS_OWNER'],
                '--region', config['BACKUP_AWS_REGION'],
                '--azure-account', config['BACKUP_AZURE_ACCOUNT'],
                '--azure-container', config['BACKUP_AZURE_CONTAINER'],
                '--anchor-sha256', config['BACKUP_ANCHOR_SHA256'],
                '--scratch-parent', str(scratch), '--max-seconds', '3300', '--live-access-approved']
            # provider diagnostics and runner manifests never enter the Actions log
            with tempfile.TemporaryFile(dir=scratch) as output, tempfile.TemporaryFile(dir=scratch) as errors:
                result = subprocess.run(command, env=environment, stdout=output, stderr=errors,
                                        timeout=3600, check=False)
                output.seek(0)
                data = output.read(4*1024**2+1)
                if len(data) > 4*1024**2:
                    raise ValueError()
                summary = json.loads(data)
                if result.returncode != 0 or summary.get('complete') is not True:
                    raise ValueError()
        print('Daily backup complete; persisted evidence verified.')
        return 0
    except Exception:
        # fixed stage labels only; exceptions can contain credentials or private keys
        print(f'Daily backup failed during {stage}; incomplete backup. No private diagnostics published.')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
