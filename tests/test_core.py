"""Offline regression tests. All HTTP access is blocked during these tests."""
from __future__ import annotations

import contextlib
import datetime as dt
import importlib.util
import io
import json
import random
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from qwat import api, auth, autoplan, cas, daemon, geo, sign

NOW = dt.datetime(2030, 1, 2, 9)


class FrozenDatetime(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.replace(tzinfo=tz) if tz else NOW


class OfflineTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.addCleanup(patch.stopall)
        patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')).start()
        self.output = io.StringIO()
        self.stdout = contextlib.redirect_stdout(self.output)
        self.stdout.__enter__()
        self.addCleanup(self.stdout.__exit__, None, None, None)


class AuthTests(OfflineTest):
    def test_fingerprint_is_unique_and_persistent(self):
        first, second = self.root / 'first', self.root / 'second'
        value = auth.get_fingerprint(first)
        self.assertEqual(value, auth.get_fingerprint(first))
        self.assertNotEqual(value, auth.get_fingerprint(second))

    def test_cookie_storage_includes_browser_fields(self):
        session = cas.new_session()
        session.cookies.set('example', 'dummy-value', domain='example.org', path='/', secure=True)
        state = self.root / 'state.json'
        cas.save_cookies(session, state)
        cookie = json.loads(state.read_text('utf-8'))['cookies'][0]
        self.assertTrue({'name', 'value', 'domain', 'path', 'expires', 'secure', 'httpOnly', 'sameSite'} <= cookie.keys())
        self.assertEqual(cas.load_state(state).cookies.get('example'), 'dummy-value')

    def test_existing_session_is_saved_after_open(self):
        state = self.root / 'state.json'
        state.write_text('{}')
        session = Mock()
        patch('qwat.cas.load_state', return_value=session).start()
        opened = patch('qwat.cas.open_app').start()
        saved = patch('qwat.cas.save_cookies').start()
        self.assertIs(auth.ensure_app(state), session)
        opened.assert_called_once_with(session)
        saved.assert_called_once_with(session, state)

    def test_expired_session_relogs_before_saving(self):
        state = self.root / 'state.json'
        state.write_text('{}')
        stale, fresh = Mock(), Mock()
        patch('qwat.cas.load_state', return_value=stale).start()
        patch('qwat.auth.load_creds', return_value=('dummy-user', 'dummy-password')).start()
        patch('qwat.auth.login', return_value=fresh).start()
        opened = patch('qwat.cas.open_app', side_effect=[cas.NotLoggedIn(), None]).start()
        saved = patch('qwat.cas.save_cookies').start()
        self.assertIs(auth.ensure_app(state), fresh)
        self.assertEqual(opened.call_count, 2)
        saved.assert_called_once_with(fresh, state)

    def test_dpapi_credentials_fail_clearly_on_linux(self):
        path = self.root / 'creds.bin'
        path.write_bytes(b'DPAPI1dummy')
        with patch('qwat.auth.sys.platform', 'linux'):
            with self.assertRaises(auth.LoginError):
                auth.load_creds(path)

    def test_captcha_prevents_password_submission(self):
        session = Mock()
        session.get.return_value = Mock(text='<input id="pwdEncryptSalt" value="0123456789abcdef">', content=b'dummy')
        session.post.return_value.json.return_value = {'isNeed': True}
        patch('qwat.auth.attach_fingerprint').start()
        with patch('qwat.auth.Path') as path:
            with self.assertRaises(auth.NeedCaptcha):
                auth.login('dummy-user', 'dummy-password', session=session)
            path.return_value.write_bytes.assert_called_once_with(b'dummy')
        self.assertEqual(session.post.call_count, 1)


class SignTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.course = {'date': '2030-01-02', 'start_time': '09:00:00', 'end_time': '10:00:00',
                       'course_name': 'Example', 'student_course_id': 1, 'building_name': 'Example building'}
        self.instance = {**self.course, 'session_id': 'server-session', 'enrollment_id': 1}
        self.client = Mock()
        self.client.status.return_value = {'logged_in': True, 'user': {'username': 'dummy-user'}}
        self.client.schedule.return_value = [self.course]
        self.client.location_info.return_value = {'data': {'is_valid_location': True}}
        self.data = {'course_instance': self.instance, 'can_signin': True, 'already_signed': False}
        self.client.check_status.return_value = {'data': self.data}
        patch('qwat.api.QwClient.from_state', return_value=self.client).start()

    def execute(self, **kwargs):
        return sign.sign(occurrence=self.course, at=(22.5, 113.9), verbose=False, **kwargs)

    def test_dry_run_does_not_upload_or_submit(self):
        self.assertTrue(self.execute(dry_run=True)['dry_run'])
        self.client.upload_photo.assert_not_called()
        self.client.signin.assert_not_called()

    def test_mismatched_already_signed_does_not_finish_wrong_course(self):
        self.data['already_signed'] = True
        self.instance['start_time'] = '08:00:00'
        self.assertEqual(self.execute()['reason'], 'instance_mismatch')
        self.client.upload_photo.assert_not_called()

    def test_missing_instance_date_is_rejected(self):
        del self.instance['date']
        self.assertEqual(self.execute()['reason'], 'instance_mismatch')

    def test_matching_already_signed_skips_submission(self):
        self.data['already_signed'] = True
        self.assertTrue(self.execute()['ok'])
        self.client.upload_photo.assert_not_called()

    def test_outside_location_never_checks_or_submits(self):
        self.client.location_info.return_value = {'data': {'is_valid_location': False}}
        self.assertFalse(self.execute()['ok'])
        self.client.check_status.assert_not_called()
        self.client.signin.assert_not_called()

    def test_submission_uses_server_instance(self):
        self.client.upload_photo.return_value = 'dummy-photo-path'
        self.client.signin.return_value = {'success': True}
        self.assertTrue(self.execute(photo='dummy.jpg')['ok'])
        payload = self.client.signin.call_args.args[0]
        self.assertEqual(payload['session_id'], 'server-session')
        self.assertEqual(payload['photo'], 'dummy-photo-path')


class PlanTests(OfflineTest):
    def setUp(self):
        super().setUp()
        self.path = self.root / 'plan.json'
        self.photo = self.root / 'photo.jpg'
        self.photo.write_bytes(b'dummy')
        self.entry = {'date': '2030-01-02', 'course': 'Example', 'classroom': 'Example room',
                      'building': 'Example building', 'start': '09:00', 'end': '10:00',
                      'target_at': '2030-01-02T08:58:00', 'lat': 22.5, 'lng': 113.9,
                      'offset_m': 10, 'state': 'pending', 'session_id': None,
                      'student_course_id': 1}
        patch('qwat.autoplan.dt.datetime', FrozenDatetime).start()
        patch('qwat.auth.ensure_app').start()
        self.signer = patch('qwat.sign.sign', return_value={'ok': True, 'dry_run': True}).start()

    def write(self):
        autoplan.save_plan([self.entry], self.path)

    def test_dry_run_preserves_file_bytes(self):
        self.write()
        original = self.path.read_bytes()
        autoplan.run(self.path, photo=str(self.photo), dry_run=True)
        self.assertEqual(self.path.read_bytes(), original)
        self.signer.assert_called_once()

    def test_success_is_persisted(self):
        self.signer.return_value = {'ok': True, 'response': {'data': {'example': True}}}
        self.write()
        autoplan.run(self.path, photo=str(self.photo), once=True)
        self.assertEqual(autoplan.load_plan(self.path)[0]['state'], 'done')

    def test_expired_state_is_saved_without_signing(self):
        self.entry['date'] = '2030-01-01'
        self.entry['target_at'] = '2030-01-01T08:58:00'
        self.write()
        autoplan.run(self.path, photo=str(self.photo), once=True)
        self.assertEqual(autoplan.load_plan(self.path)[0]['state'], 'expired')
        self.signer.assert_not_called()

    def test_failed_terminal_task_exits_without_polling_forever(self):
        self.entry['end'] = '08:59'
        self.signer.return_value = {'ok': False, 'reason': 'example failure'}
        self.write()
        with patch('qwat.autoplan.time.sleep', side_effect=AssertionError('Should exit')):
            autoplan.run(self.path, photo=str(self.photo))
        self.assertEqual(autoplan.load_plan(self.path)[0]['state'], 'failed')

    def test_retry_does_not_complete_task(self):
        self.signer.return_value = {'ok': False, 'reason': 'instance_mismatch'}
        self.write()
        autoplan.run(self.path, photo=str(self.photo), once=True)
        entry = autoplan.load_plan(self.path)[0]
        self.assertEqual(entry['state'], 'pending')
        self.assertEqual(entry['retry_after'], '2030-01-02T09:00:30')

    def test_missing_photo_fails_before_authentication(self):
        self.write()
        with self.assertRaises(SystemExit):
            autoplan.run(self.path, once=True)
        auth.ensure_app.assert_not_called()

    def test_dry_run_of_expired_plan_preserves_state(self):
        self.entry['date'] = '2030-01-01'
        self.entry['target_at'] = '2030-01-01T08:58:00'
        self.write()
        original = self.path.read_bytes()
        autoplan.run(self.path, dry_run=True, once=True)
        self.assertEqual(self.path.read_bytes(), original)

    def test_small_building_random_points_stay_inside(self):
        for radius in (1, 10, 25, 175):
            rng = random.Random(42)
            for _ in range(50):
                lng, lat = geo.random_point(22.5, 113.9, radius, rng=rng)
                self.assertLessEqual(autoplan._dist_m(22.5, 113.9, lat, lng), radius)

    def test_invalid_lead_is_rejected_without_network(self):
        with self.assertRaises(ValueError):
            autoplan.build_plan(lead=(5, 1))


class DaemonTests(OfflineTest):
    def test_dry_run_does_not_write_done_file(self):
        import yaml
        done = self.root / 'done.json'
        config = self.root / 'config.yaml'
        config.write_text(yaml.safe_dump({'done_file': str(done), 'courses': [{'name': 'Example',
                          'weekday': 3, 'start': '09:00'}]}), 'utf-8')
        patch('qwat.daemon.dt.datetime', FrozenDatetime).start()
        patch('qwat.autoplan.ensure_timezone').start()
        patch('qwat.daemon.attempt', return_value={'ok': True, 'dry_run': True}).start()
        with patch('sys.argv', ['daemon', '--config', str(config), '--once', '--dry-run']):
            daemon.main()
        self.assertFalse(done.exists())


class DeploymentTests(OfflineTest):
    def test_invalid_session_cannot_report_deployment_success(self):
        spec = importlib.util.spec_from_file_location('deploy_check', Path(__file__).resolve().parents[1] / 'scripts' / 'deploy_check.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        patch('qwat.api.QwClient.from_state', side_effect=FileNotFoundError('No credentials')).start()
        errors = module.check(self.root / 'state.json', self.root / 'plan.json', self.root / 'photo.jpg')
        self.assertTrue(errors)


if __name__ == '__main__':
    unittest.main()
