from io import StringIO
from unittest.mock import patch
from django.test import SimpleTestCase
from django.db import OperationalError
from django.core.management import call_command
from .errors import api_exception_handler
from .topic_discovery import useful_name, acknowledgement


class RecoveryTests(SimpleTestCase):
    def test_database_failure_returns_safe_json(self):
        response=api_exception_handler(OperationalError('private database connection details'),{})
        self.assertEqual(response.status_code,503)
        self.assertEqual(response['Retry-After'],'5')
        self.assertIn('Database temporarily unavailable',response.data['detail'])
        self.assertNotIn('private',response.data['detail'])

    def test_worker_closes_connections_after_success_and_failure(self):
        for error in (None,OperationalError('connection slots exhausted')):
            with patch('relay.management.commands.relay_worker.run_step',side_effect=error,return_value=False), patch('relay.management.commands.relay_worker.connections.close_all') as close:
                call_command('relay_worker',once=True,stdout=StringIO(),stderr=StringIO())
                close.assert_called_once()

    def test_greetings_and_announcements_are_not_workstreams(self):
        for name in ['Hi everyone','Hello team','Hey guys','Good morning everyone']:
            self.assertTrue(acknowledgement(name))
            self.assertFalse(useful_name(name,'work'))
        for name in ['All are invited','Work from Home','No leaves will be given for ReUT','8–8.30 ko shuru hoga 12 tak chalega','Mishra Did Vietnam']:
            self.assertFalse(useful_name(name,'work'))
        self.assertTrue(useful_name('NimbusMart','project'))
        self.assertTrue(useful_name('Payment integration','work'))
