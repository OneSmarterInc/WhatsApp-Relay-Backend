"""Provision an isolated loopback PostgreSQL development cluster."""
from pathlib import Path
import secrets, subprocess
import psycopg
from psycopg import sql
from dotenv import dotenv_values
root=Path(__file__).resolve().parent
local=root/'.local'; local.mkdir(exist_ok=True)
env=root/'.env'
if not env.exists():
    env.write_text('RELAY_LOCAL_DEV=1\nDJANGO_SECRET_KEY='+secrets.token_urlsafe(64)+'\nPGHOST=127.0.0.1\nPGPORT=55432\nPGDATABASE=relay\nPGUSER=relay\nPGPASSWORD='+secrets.token_urlsafe(32)+'\n',encoding='utf-8')
config=dotenv_values(env)
if 'CSRF_TRUSTED_ORIGINS' not in config:
    with env.open('a', encoding='utf-8') as stream:
        stream.write('\nCSRF_TRUSTED_ORIGINS=http://127.0.0.1:5173,http://localhost:5173\n')
bin=Path('C:/Program Files/PostgreSQL/18/bin')
data=local/'postgres'
pw=local/'postgres-bootstrap-password'
if not data.exists():
    pw.write_text(secrets.token_urlsafe(32),encoding='utf-8')
    subprocess.run([str(bin/'initdb.exe'),'-D',str(data),'-U','relay_bootstrap','--pwfile='+str(pw),'--auth=scram-sha-256','--encoding=UTF8','--locale=C'],check=True)
if subprocess.run([str(bin/'pg_isready.exe'),'-h','127.0.0.1','-p','55432'],stdout=subprocess.DEVNULL).returncode:
    subprocess.run([str(bin/'pg_ctl.exe'),'-D',str(data),'-l',str(local/'postgres.log'),'-o','-h 127.0.0.1 -p 55432','-w','start'],check=True)
with psycopg.connect(host='127.0.0.1',port=55432,dbname='postgres',user='relay_bootstrap',password=pw.read_text(),autocommit=True) as c:
    if not c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s',('relay',)).fetchone():
        c.execute(sql.SQL('CREATE ROLE relay LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Literal(config['PGPASSWORD'])))
    if not c.execute('SELECT 1 FROM pg_database WHERE datname=%s',('relay',)).fetchone():
        c.execute('CREATE DATABASE relay OWNER relay')
print('Local PostgreSQL ready; application uses non-superuser relay role.')
