"""Serial transport recovery for the Halo. Software only: no ESP32 or LED ring is involved."""
import time

from hardware_halo import HaloController
from hardware_halo.protocol import HELLO,IDENTITY
from test_hardware_halo import MockSerial,snapshot


def wait_for(condition,seconds=4):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        if condition():return True
        time.sleep(.01)
    return False


class FlakySerial(MockSerial):
    """Identifies correctly, then fails after a number of telemetry writes (an unplugged cable)."""
    def __init__(self,fail_after,**kwargs):
        super().__init__(**kwargs);self.fail_after=fail_after
    def write(self,data):
        if data!=HELLO:
            self.fail_after-=1
            if self.fail_after<0:raise OSError('device unplugged')
        return super().write(data)
    def close(self):
        super().close();raise OSError('close after unplug also fails')


def test_lost_link_reconnects_and_firmware_error_lines_are_reported():
    first=FlakySerial(3,response=IDENTITY+b'\r\nAETHER_HALO_ERROR RMT_WRITE\r\nnoise\n');second=MockSerial()
    ports=[first,second]
    controller=HaloController({'enabled':True,'port':'EXPLICIT_TEST_PORT'},serial_factory=lambda **kwargs:ports.pop(0))
    try:
        controller.publish(snapshot(now=time.time()))
        assert second.received.wait(4)
        status=controller.status()
        assert first.closed and status['connected'] and status['connections']==2
        assert 'unplugged' in status['last_error'] and status['last_device_error']=='AETHER_HALO_ERROR RMT_WRITE'
        assert wait_for(lambda:controller.status()['packets_sent']>3)
    finally:controller.close()


def test_missing_port_is_retried_with_backoff_until_the_device_appears():
    attempts=[];serial=MockSerial()
    def factory(**kwargs):
        attempts.append(time.monotonic())
        if len(attempts)<3:raise OSError('could not open port')
        return serial
    controller=HaloController({'enabled':True,'port':'EXPLICIT_TEST_PORT'},serial_factory=factory)
    try:
        controller.publish(snapshot(now=time.time()))
        assert serial.received.wait(5) and controller.status()['connected']
        assert len(attempts)==3 and attempts[2]-attempts[1]>=attempts[1]-attempts[0]>=.4
    finally:controller.close()


def test_a_wrong_device_on_the_configured_port_is_never_probed_again():
    opened=[]
    def factory(**kwargs):opened.append(kwargs);return MockSerial(b'UNRELATED_DEVICE 7\n')
    controller=HaloController({'enabled':True,'port':'ONLY_THIS_PORT','handshake_timeout_seconds':.1},serial_factory=factory)
    try:
        time.sleep(1.2)
        assert len(opened)==1 and not controller.status()['connected']
    finally:controller.close()
