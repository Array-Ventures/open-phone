import io
import json
import pathlib
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from driver import stdio


class StdioTests(unittest.TestCase):
    def test_action_id_survives_request_correlation(self):
        action_id = 'a' * 32
        driver = Mock()
        driver.call.return_value = {'ok': True, 'id': action_id, 'state': 'queued'}
        request = {'id': 41, 'method': 'shortcut_action',
                   'params': {'kind': 'copy_text', 'payload': {'text': 'test'}}}
        output = io.StringIO()
        with patch('driver.PhoneDriver', return_value=driver), \
             patch('sys.stdin', io.StringIO(json.dumps(request) + '\n')), \
             patch('sys.stdout', output):
            stdio()
        response = json.loads(output.getvalue())
        self.assertEqual(response['id'], 41)
        self.assertEqual(response['result']['id'], action_id)
        driver.close.assert_called_once()


if __name__ == '__main__':
    unittest.main()
