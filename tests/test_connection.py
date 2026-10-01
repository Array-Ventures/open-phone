import pathlib
import sys
import unittest
from unittest.mock import Mock,patch
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[1]))
from driver import PhoneDriver


class ConnectionTests(unittest.TestCase):
    def test_running_capture_without_a_fresh_frame_does_not_enable_input(self):
        d=PhoneDriver();d.address='AA:BB:CC:DD:EE:FF';d.capture_id='old';d.usb_udid='old'
        d.hid=Mock();d.capture=Mock();d.devices=Mock(return_value={})
        d.capture.call.side_effect=[{'capture_id':'new'},RuntimeError('No fresh frame')]
        with self.assertRaisesRegex(RuntimeError,'No fresh frame'):
            d.connect('AA:BB:CC:DD:EE:FF',capture_id='new')
        self.assertIsNone(d.address);self.assertIsNone(d.capture_id);self.assertIsNone(d.usb_udid)
        d.hid.call.assert_not_called()

    def test_missing_fresh_frame_stops_all_reports_before_channel_reconnect(self):
        d=PhoneDriver();d.address='AA:BB:CC:DD:EE:FF';d.hid=Mock()
        d.screenshot=Mock(side_effect=RuntimeError('USB disconnected'))
        d._connect_channels=Mock()
        with self.assertRaisesRegex(RuntimeError,'USB disconnected'):
            d.call('native_gesture',dict(action='tap',params={'x':500,'y':500},width=1000,height=2000))
        d._connect_channels.assert_not_called();d.hid.call.assert_not_called()

    def test_native_pixel_input_rejects_changed_dimensions_before_hid_input(self):
        d=PhoneDriver();d.address='AA:BB:CC:DD:EE:FF';d.hid=Mock()
        d.screenshot=Mock(return_value={'screen_width':2000,'screen_height':1000})
        with self.assertRaisesRegex(RuntimeError,'dimensions changed'):
            d.call('native_gesture',dict(action='tap',params={'x':500,'y':500},width=1000,height=2000))
        d.hid.call.assert_not_called()

    def test_failed_native_pixel_sequence_is_not_replayed_or_reported_complete(self):
        d=PhoneDriver();d.address='AA:BB:CC:DD:EE:FF';d.hid=Mock()
        d.screenshot=Mock(return_value={'screen_width':1000,'screen_height':2000})
        d._connect_channels=Mock();d.hid.call.side_effect=RuntimeError('Partial report write failed')
        with patch('driver.time.sleep') as sleep:
            with self.assertRaisesRegex(RuntimeError,'Partial report'):
                d.call('native_gesture',dict(action='tap',params={'x':500,'y':500},width=1000,height=2000))
        self.assertEqual(d.hid.call.call_count,1);sleep.assert_not_called()

    def test_host_target_guard_rejects_changed_selection_before_any_input(self):
        d=PhoneDriver();d.address='AA:BB:CC:DD:EE:FF';d.hid=Mock();d.capture=Mock()
        with self.assertRaisesRegex(RuntimeError,'no action was sent'):
            d.call('tap',{'x':.5,'y':.5},expected_address='11:22:33:44:55:66')
        d.hid.call.assert_not_called();d.capture.call.assert_not_called()
    def test_mismatched_bluetooth_address_does_not_start_capture_or_input(self):
        d=PhoneDriver();d.capture=Mock()
        d.devices=Mock(return_value={'usb_phones':[{'bluetooth_address':'AA:BB:CC:DD:EE:FF','udid':'12345678-1234567812345678'}]})
        with self.assertRaises(ValueError):d.connect('11:22:33:44:55:66')
        d.capture.call.assert_not_called()
        self.assertIsNone(d.hid)

    def test_failed_capture_selection_disables_previous_input_context(self):
        d=PhoneDriver();d.address='AA:BB:CC:DD:EE:FF';d.capture_id='old';d.usb_udid='old'
        d.devices=Mock(return_value={});d.capture=Mock()
        d.capture.call.side_effect=RuntimeError('USB capture failed')
        with self.assertRaises(RuntimeError):d.connect('11:22:33:44:55:66')
        self.assertIsNone(d.address);self.assertIsNone(d.capture_id);self.assertIsNone(d.usb_udid)
        with self.assertRaises(RuntimeError):d.sequence([])

    def test_transient_missing_channel_is_retried_before_input(self):
        d=PhoneDriver();d.address='AA:BB:CC:DD:EE:FF';d.hid=Mock()
        opens=[]
        def call(method,**kwargs):
            if method=='status':
                host={'control_fd':3}
                if len(opens)>=2:host['interrupt_fd']=4
                return {'hosts':{d.address:host}}
            if method=='peer_status':return {'peer_state':2}
            if method=='open_channels':opens.append(True);return {'ok':True}
            raise AssertionError('An already connected peer must not be connected again')
        d.hid.call.side_effect=call
        with patch('driver.time.sleep'):d._connect_channels()
        self.assertEqual(len(opens),2)
        self.assertFalse(any(c.args[0]=='sequence' for c in d.hid.call.call_args_list))


if __name__=='__main__':unittest.main()
