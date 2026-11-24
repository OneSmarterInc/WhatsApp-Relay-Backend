"""Start only Relay's existing development database, then verify app access."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parent


def ensure_database():
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    import django
    django.setup()
    from django.conf import settings
    from django.db import connection
    db = settings.DATABASES['default']
    data = ROOT / '.local' / 'postgres'
    # Never provision, reset, stop, or change credentials for an existing cluster.
    if settings.LOCAL_DEV and db['HOST'] == '127.0.0.1' and str(db['PORT']) == '55432':
        ctl = Path(os.environ.get('POSTGRES_BIN', 'C:/Program Files/PostgreSQL/18/bin')) / 'pg_ctl.exe'
        if not ctl.is_file() or not (data / 'PG_VERSION').is_file():
            raise RuntimeError('Local PostgreSQL installation or Relay data directory is missing. Check POSTGRES_BIN and backend/.local/postgres; no database was created.')
        flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        def run(*args):
            return subprocess.run([str(ctl), '-D', str(data), *args], capture_output=True, timeout=75, creationflags=flags)
        status = run('status')
        if status.returncode == 3:
            print('Starting existing Relay PostgreSQL on 127.0.0.1:55432...', flush=True)
            started = run('-l', str(ROOT / '.local' / 'postgres.log'), '-o', '-h 127.0.0.1 -p 55432', '-w', '-t', '60', 'start')
            if started.returncode:
                raise RuntimeError('PostgreSQL could not start. Check backend/.local/postgres.log and whether port 55432 is occupied. Existing data was not reset.')
        elif status.returncode:
            raise RuntimeError('Cannot determine local PostgreSQL status. Check directory permissions and POSTGRES_BIN.')
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            cursor.fetchone()
    except Exception:
        raise RuntimeError('Database connection check failed. Verify PGHOST, PGPORT, PGDATABASE, PGUSER and PGPASSWORD in backend/.env, and database availability.') from None
    finally:
        connection.close()
    print('Relay database connection verified.', flush=True)


if __name__ == '__main__':
    try:
        ensure_database()
    except Exception as exc:
        # Do not print connection strings or raw database exceptions.
        print(str(exc) if isinstance(exc, RuntimeError) else 'Database startup check failed. Check configuration and PostgreSQL logs.', file=sys.stderr)
        sys.exit(1)
