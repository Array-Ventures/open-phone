"""Optional trusted-USB inventory adapter to the installed, open-source go-ios CLI.

Only metadata is returned. No tunnel, developer service, or device agent is started.
The capture and HID transport remain independent of this optional dependency.
"""
import json
import os
import pathlib
import re
import shutil
import subprocess


def executable():
    configured=os.environ.get('OPEN_PHONE_IOS')
    if configured:return configured
    found=shutil.which('ios')
    if found:return found
    for path in ('/opt/homebrew/bin/ios','/usr/local/bin/ios'):
        if pathlib.Path(path).is_file():return path
    return None


def validate_udid(udid):
    if not isinstance(udid,str) or not re.fullmatch(r'(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{16}|[0-9a-fA-F]{40})',udid):
        raise ValueError('Invalid USB device identifier')
    return udid


def run(arguments,udid=None):
    path=executable()
    if not path:raise RuntimeError('Optional USB inventory requires go-ios; set OPEN_PHONE_IOS to its executable')
    command=[path,*arguments]
    if udid is not None:command.append('--udid='+validate_udid(udid))
    result=subprocess.run(command,capture_output=True,text=True,timeout=20)
    if result.returncode:raise RuntimeError('go-ios USB inventory failed; check USB connection, trust and unlock')
    try:return json.loads(result.stdout)
    except ValueError:raise RuntimeError('go-ios returned no valid inventory; check USB connection, trust and unlock') from None


def phones():
    devices=run(['list']).get('deviceList',[])
    out=[]
    for udid in devices:
        value=run(['info','lockdown'],udid)
        out.append({'udid':udid,'name':value.get('DeviceName'),
                    'bluetooth_address':str(value.get('BluetoothAddress','')).upper(),
                    'product_version':value.get('ProductVersion')})
    return out


def apps(udid,include_system=True):
    if not isinstance(include_system,bool):raise ValueError('include_system must be a boolean')
    values=run(['apps','--all'],validate_udid(udid))
    if not isinstance(values,list):raise RuntimeError('Unexpected go-ios application inventory format')
    out=[]
    for value in values:
        if not include_system and value.get('ApplicationType')!='User':continue
        identifier=value.get('CFBundleIdentifier')
        if not identifier:continue
        out.append({'bundle_id':identifier,'name':value.get('CFBundleDisplayName') or value.get('CFBundleName') or identifier,
                    'version':value.get('CFBundleShortVersionString'),'type':value.get('ApplicationType')})
    return {'ok':True,'udid':udid,'applications':sorted(out,key=lambda a:a['bundle_id'])}
