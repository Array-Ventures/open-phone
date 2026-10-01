import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
from openphone.relay.store import RelayStore
from openphone.relay.state import RelayState
from openphone.relay.client import RelayClient

ROOT = Path(__file__).resolve().parents[2]
SESSION = 'a' * 32


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.path = Path(self.tmp.name) / 'state.sqlite'
        self.now = 100; self.wall = 1000; self.stores = []
    def tearDown(self):
        for store in self.stores: store.close()
        self.tmp.cleanup()
    def store(self):
        store = RelayStore(clock=lambda:self.now, wall_clock=lambda:self.wall, state_file=self.path)
        self.stores.append(store); return store
    def register(self, store, session=SESSION): store.register('phone',session,'Fixture phone',1180,2556,'fixture-mac')
    def complete(self, store, method, result):
        identifier=store.enqueue('phone',method,{})
        job=store.claim('phone',SESSION,wait=0)
        store.complete(identifier,SESSION,job['receipt'],result)
        return identifier

    def test_catalog_apps_completed_jobs_and_image_survive_as_offline(self):
        store=self.store();self.register(store);store.rename('phone','Fixture ✓')
        self.complete(store,'apps',{'applications':[{'name':'Fixture app','bundle_id':'org.example.fixture'}]})
        raw=b'\x89PNG\r\n\x1a\nfixture'
        identifier=self.complete(store,'screenshot',{'data':base64.b64encode(raw).decode(),'mime_type':'image/png','screen_width':1180,'screen_height':2556})
        created=store.phone_view('phone')['created_at'];store.close()
        self.now=0;self.wall+=10;restored=self.store()
        phone=restored.phone_view('phone')
        self.assertEqual((phone['connection_status'],phone['connected_mac_id']),('offline',None))
        self.assertEqual((phone['display_name'],phone['created_at']),('Fixture ✓',created))
        self.assertEqual(restored.apps_view('phone')['apps'][0]['bundle_id'],'org.example.fixture')
        self.assertEqual(restored.job_view(identifier)['status'],'completed')
        self.assertEqual(restored.download(identifier),(raw,'image/png'))
        self.register(restored,'b'*32)
        self.assertIsNone(restored.claim('phone','b'*32,wait=0))

    def test_abrupt_process_death_fails_pending_and_running_without_replay(self):
        script="""import json,sys,time
from openphone.relay.store import RelayStore
s=RelayStore(state_file=sys.argv[1]);s.register('phone','a'*32,'Fixture phone',1180,2556)
running=s.enqueue('phone','press_button',{'button':'home'});claimed=s.claim('phone','a'*32,wait=0)
pending=s.enqueue('phone','type_text',{'text':'Fixture text'})
print(json.dumps({'running':running,'pending':pending,'receipt':claimed['receipt']}),flush=True)
time.sleep(60)
"""
        process=subprocess.Popen([sys.executable,'-u','-c',script,str(self.path)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            ids=json.loads(process.stdout.readline())
            process.kill();process.wait(timeout=3)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=3)
            process.stdout.close();process.stderr.close()
        # This fixture used real wall time, so reload with the same wall clock.
        import time
        self.wall=time.time();restored=self.store()
        for key in ('running','pending'):
            self.assertEqual(restored.job_view(ids[key])['result'],{'error':'RELAY_RESTARTED'})
        with self.assertRaises(RuntimeError):restored.complete(ids['running'],SESSION,ids['receipt'],{})
        self.register(restored,'b'*32)
        self.assertIsNone(restored.claim('phone','b'*32,wait=0))

    def test_expired_history_is_removed_across_monotonic_clock_reset(self):
        store=self.store();self.register(store)
        raw=b'\xff\xd8\xfffixture'
        identifier=self.complete(store,'screenshot',{'data':base64.b64encode(raw).decode(),'mime_type':'image/jpeg'})
        store.close();self.now=0;self.wall+=301;restored=self.store()
        with self.assertRaises(KeyError):restored.job_view(identifier)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM images').fetchone()[0],0)
        self.assertEqual(len(restored.phones_view()),1)

    def test_timeout_is_durable_and_old_completion_remains_rejected(self):
        store=self.store();self.register(store)
        identifier=store.enqueue('phone','press_button',{'button':'home'})
        claimed=store.claim('phone',SESSION,wait=0);self.now+=61
        self.assertEqual(store.job_view(identifier)['result'],{'error':'TIMEOUT'})
        store.close();self.now=0;self.wall+=61;restored=self.store()
        self.assertEqual(restored.job_view(identifier)['result'],{'error':'TIMEOUT'})
        with self.assertRaises(RuntimeError):restored.complete(identifier,SESSION,claimed['receipt'],{})

    def test_only_one_writer_and_state_files_are_owner_only(self):
        store=self.store()
        with self.assertRaisesRegex(RuntimeError,'Another relay'):RelayStore(state_file=self.path)
        self.assertEqual(self.path.stat().st_mode&0o777,0o600)
        self.assertEqual(Path(str(self.path)+'.lock').stat().st_mode&0o777,0o600)
        store.close();self.store()

    def test_failed_durable_claim_poisoning_prevents_later_delivery(self):
        store=self.store();self.register(store)
        identifier=store.enqueue('phone','press_button',{'button':'home'})
        with patch.object(store.state,'save',side_effect=OSError('Fixture disk failure')):
            with self.assertRaisesRegex(RuntimeError,'persistence failed'):store.claim('phone',SESSION,wait=0)
        with self.assertRaisesRegex(RuntimeError,'persistence failed'):store.claim('phone',SESSION,wait=0)
        with self.assertRaisesRegex(RuntimeError,'persistence failed'):store.enqueue('phone','press_button',{})
        store.close();restored=self.store()
        self.assertEqual(restored.job_view(identifier)['result'],{'error':'RELAY_RESTARTED'})

    def test_sqlite_transaction_failure_leaves_committed_pending_state_intact(self):
        store=self.store();self.register(store)
        identifier=store.enqueue('phone','press_button',{'button':'home'})
        store.state.db.execute("CREATE TRIGGER fixture_abort BEFORE UPDATE ON jobs BEGIN SELECT RAISE(ABORT,'fixture abort'); END")
        store.state.db.commit()
        with self.assertRaisesRegex(RuntimeError,'persistence failed'):store.claim('phone',SESSION,wait=0)
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(json.loads(db.execute('SELECT data FROM jobs WHERE id=?',(identifier,)).fetchone()[0])['status'],'pending')
            db.execute('DROP TRIGGER fixture_abort')
        store.close();restored=self.store()
        self.assertEqual(restored.job_view(identifier)['result'],{'error':'RELAY_RESTARTED'})

    def test_payload_host_session_and_receipt_are_not_recovery_data(self):
        store=self.store();self.register(store)
        identifier=store.enqueue('phone','type_text',{'text':'Fixture private command'})
        receipt=store.claim('phone',SESSION,wait=0)['receipt']
        with closing(sqlite3.connect(self.path)) as db, db:
            phone=json.loads(db.execute('SELECT data FROM phones').fetchone()[0])
            job=json.loads(db.execute('SELECT data FROM jobs').fetchone()[0])
        self.assertNotIn('session',phone)
        for field in ('params','receipt','session'):self.assertNotIn(field,job)
        self.assertNotIn(receipt,json.dumps(job));self.assertNotIn('Fixture private command',json.dumps(job))

    def test_unrelated_database_is_not_overwritten(self):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE fixture(value TEXT)');db.execute("INSERT INTO fixture VALUES('keep')")
        with self.assertRaisesRegex(ValueError,'unrelated'):RelayState(self.path)
        with closing(sqlite3.connect(self.path)) as db:self.assertEqual(db.execute('SELECT value FROM fixture').fetchone()[0],'keep')

    def test_graceful_stop_records_terminal_failure(self):
        store=self.store();self.register(store);identifier=store.enqueue('phone','press_button',{})
        store.close();restored=self.store()
        self.assertEqual(restored.job_view(identifier)['result'],{'error':'RELAY_STOPPED'})

    def test_maintenance_expires_results_and_images_without_client_access(self):
        store=self.store();self.register(store)
        raw=b'\xff\xd8\xfffixture'
        self.complete(store,'screenshot',{'data':base64.b64encode(raw).decode(),'mime_type':'image/jpeg'})
        self.now+=301;self.wall+=301;store.maintain()
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM jobs').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM images').fetchone()[0],0)

    def test_http_service_recovers_a_crash_without_redelivering_jobs(self):
        # Separate loopback service, credentials and DB; no host driver exists.
        agent_path=Path(self.tmp.name)/'agent-token';host_path=Path(self.tmp.name)/'host-token'
        for path,value in [(agent_path,'fixture-agent-key'),(host_path,'fixture-host-key')]:
            path.write_text(value);path.chmod(0o600)
        with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        agent=RelayClient(f'http://127.0.0.1:{port}',agent_path)
        host=RelayClient(f'http://127.0.0.1:{port}',host_path,host=True)
        process=None
        def start():
            nonlocal process
            process=subprocess.Popen([sys.executable,'-m','openphone','relay','--port',str(port),'--state-file',str(self.path),'--agent-token-file',str(agent_path),'--host-token-file',str(host_path)],cwd=ROOT,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
            for _ in range(100):
                if process.poll() is not None:raise RuntimeError(process.stderr.read())
                try:agent.request('/v1/phones',timeout=.2);return
                except OSError:time.sleep(.02)
            raise RuntimeError('Fixture relay did not start')
        def kill():
            nonlocal process
            if process is not None:
                if process.poll() is None:process.kill();process.wait(timeout=3)
                process.stdout.close();process.stderr.close();process=None
        try:
            start()
            host.request('/v1/host/register',dict(phone_id='phone',session=SESSION,name='Fixture HTTP phone',width=1180,height=2556))
            agent.request('/v1/phones/phone/settings',{'display_name':'Fixture persistent phone'},method='PATCH')
            app_id=agent.request('/v1/phones/phone/apps/refresh?async=true',{},method='POST')['job_id']
            job=host.request('/v1/host/claim',dict(phone_id='phone',session=SESSION))['job']
            self.assertEqual(job['id'],app_id)
            host.request('/v1/host/complete',dict(id=app_id,session=SESSION,receipt=job['receipt'],result={'applications':[{'name':'Fixture app','bundle_id':'org.example.fixture'}]}))
            image_id=agent.request('/v1/phones/phone/screenshot?async=true')['job_id']
            job=host.request('/v1/host/claim',dict(phone_id='phone',session=SESSION))['job']
            raw=b'\x89PNG\r\n\x1a\nHTTP fixture'
            host.request('/v1/host/complete',dict(id=image_id,session=SESSION,receipt=job['receipt'],result={'data':base64.b64encode(raw).decode(),'mime_type':'image/png'}))
            running=agent.request('/v1/phones/phone/home?async=true',{})['job_id']
            job=host.request('/v1/host/claim',dict(phone_id='phone',session=SESSION))['job']
            pending=agent.request('/v1/phones/phone/home?async=true',{})['job_id']
            kill();start()
            phone=agent.request('/v1/phones')[0]
            self.assertEqual((phone['connection_status'],phone['display_name']),('offline','Fixture persistent phone'))
            self.assertEqual(agent.request('/v1/phones/phone/apps')['apps'][0]['bundle_id'],'org.example.fixture')
            self.assertEqual(agent.request('/v1/jobs/'+image_id+'/download'),raw)
            for identifier in (running,pending):
                self.assertEqual(agent.request('/v1/jobs/'+identifier)['result'],{'error':'RELAY_RESTARTED'})
            with self.assertRaises(RuntimeError):host.request('/v1/host/complete',dict(id=running,session=SESSION,receipt=job['receipt'],result={}))
            host.request('/v1/host/register',dict(phone_id='phone',session='b'*32,name='Fixture HTTP phone',width=1180,height=2556))
            self.assertIsNone(host.request('/v1/host/claim',dict(phone_id='phone',session='b'*32))['job'])
        finally:kill()


if __name__=='__main__':unittest.main()
