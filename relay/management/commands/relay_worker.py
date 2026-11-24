from django.conf import settings
import time
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, OperationalError
from relay.processing import run_step
class Command(BaseCommand):
    help='Process queued Relay grouping and extraction jobs independently of the browser.'
    def add_arguments(self,parser):parser.add_argument('--once',action='store_true')
    def handle(self,*args,**options):
        try:
            with connections['default'].cursor() as cursor:
                cursor.execute('SELECT 1')
        except OperationalError:
            raise CommandError('Database is unavailable. On Windows use run_worker.ps1 to start and verify the existing local database. For a remote database, check connection settings and availability.') from None
        finally:
            connections.close_all()
        self.stdout.write('Relay worker active. Ctrl+C stops it; queued work is retained.')
        failures=0
        try:
            while True:
                try:
                    worked=run_step()
                    if failures:self.stdout.write('Database connection recovered. Resuming saved jobs.')
                    failures=0
                except OperationalError:
                    worked=False
                    failures+=1
                    if failures==1:self.stderr.write('Database connection lost. Retrying with backoff (up to 60 seconds); saved jobs are retained. Check PostgreSQL or restart with run_worker.ps1.')
                    if options['once']:raise CommandError('Database unavailable; job was not completed.') from None
                finally:
                    # Management commands have no request-finished connection cleanup.
                    connections.close_all()
                if options['once']:return
                time.sleep(min(60, 5 * 2 ** min(failures-1, 4)) if failures else settings.WORKER_BUSY_POLL_SECONDS if worked else settings.WORKER_IDLE_POLL_SECONDS)
        except KeyboardInterrupt:self.stdout.write('Worker stopped.')
