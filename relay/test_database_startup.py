from unittest.mock import patch, Mock
from django.test import TestCase, override_settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import OperationalError
from ensure_database import ensure_database


class DatabaseStartupTests(TestCase):
    @override_settings(LOCAL_DEV=True, DATABASES={'default': {'ENGINE':'django.db.backends.postgresql', 'HOST':'127.0.0.1', 'PORT':'55432'}})
    def test_stopped_existing_cluster_is_started_before_connection_check(self):
        with patch('django.setup'), patch('pathlib.Path.is_file', return_value=True), patch('subprocess.run', side_effect=[Mock(returncode=3), Mock(returncode=0)]) as run, patch('django.db.connection.cursor') as cursor, patch('django.db.connection.close'):
            ensure_database()
            self.assertEqual(run.call_count, 2)
            self.assertIn('start', run.call_args.args[0])
            cursor.return_value.__enter__.return_value.execute.assert_called_once_with('SELECT 1')

    @override_settings(LOCAL_DEV=True, DATABASES={'default': {'ENGINE':'django.db.backends.postgresql', 'HOST':'127.0.0.1', 'PORT':'55432'}})
    def test_running_cluster_is_not_restarted(self):
        with patch('django.setup'), patch('pathlib.Path.is_file', return_value=True), patch('subprocess.run', return_value=Mock(returncode=0)) as run, patch('django.db.connection.cursor'), patch('django.db.connection.close'):
            ensure_database()
            self.assertEqual(run.call_count, 1)
            self.assertIn('status', run.call_args.args[0])

    def test_direct_worker_stops_with_actionable_error_when_database_is_down(self):
        with patch('django.db.backends.base.base.BaseDatabaseWrapper.cursor', side_effect=OperationalError('private connection details')), patch('relay.management.commands.relay_worker.run_step') as step:
            with self.assertRaisesMessage(CommandError, 'run_worker.ps1'):
                call_command('relay_worker', once=True)
            step.assert_not_called()
