#!/usr/bin/env python3
import pathlib
import shutil
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent.parent
native = root/'native'
bundle = root/'build/OpenPhone.app'
macos = bundle/'Contents/MacOS'
macos.mkdir(parents=True, exist_ok=True)
shutil.copy2(native/'Info.plist',bundle/'Contents/Info.plist')
subprocess.run([sys.executable,str(native/'protocol.py')],check=True)
subprocess.run(['xcrun','clang','-fobjc-arc','-Wall','-Wextra','-framework','Foundation','-framework','CoreBluetooth','-sectcreate','__TEXT','__info_plist',str(native/'Info.plist'),str(native/'hid.m'),'-o',str(macos/'open-phone-hid')],check=True)
subprocess.run(['xcrun','swiftc',str(native/'capture.swift'),'-Xlinker','-sectcreate','-Xlinker','__TEXT','-Xlinker','__info_plist','-Xlinker',str(native/'Info.plist'),'-o',str(macos/'open-phone-capture')],check=True)
subprocess.run(['codesign','--force','--sign','-','--identifier','org.openphone.capture',str(macos/'open-phone-capture')],check=True)
subprocess.run(['codesign','--force','--sign','-','--identifier','org.openphone.local',str(bundle)],check=True)
print(bundle)
