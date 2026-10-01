"""The OpenPhone command-line entry point. Optional services load on demand."""
import argparse
import importlib
import sys

COMMANDS = {
    'serve': ('openphone.services.local', 'main', 'Start the local device API'),
    'dashboard': ('openphone.services.dashboard', 'main', 'Open the local control dashboard'),
    'device': ('openphone.clients.local', 'main', 'Call a local phone action'),
    'relay': ('openphone.relay.server', 'main', 'Start the self-hosted command relay'),
    'host': ('openphone.relay.worker', 'main', 'Connect this Mac to a relay'),
    'bridge': ('openphone.bridge.server', 'main', 'Start the optional Shortcut bridge'),
    'shortcut': ('openphone.bridge.shortcut', 'main', 'Generate the phone Shortcut'),
    'record': ('openphone.video.record', 'main', 'Record the selected phone to MP4'),
    'build': ('openphone.build', 'main', 'Build the native macOS helpers'),
    'export': ('openphone.export', 'main', 'Export source without private runtime artifacts'),
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='openphone', description='Control a physical iPhone from your Mac.')
    parser.add_argument('command', choices=COMMANDS, help='\n'.join(f'{key}: {value[2]}' for key, value in COMMANDS.items()))
    if not argv or argv[0] in ('-h', '--help'):
        parser.print_help()
        return
    command = parser.parse_args(argv[:1]).command
    module_name, entry, _ = COMMANDS[command]
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if command == 'record' and error.name in ('av', 'aiortc'):
            parser.error('Recording requires the video extra: python -m pip install -e ".[video]"')
        raise
    previous = sys.argv
    try:
        sys.argv = [f'openphone {command}', *argv[1:]]
        try:
            getattr(module, entry)()
        except ModuleNotFoundError as error:
            if command == 'record' and error.name in ('av', 'aiortc'):
                parser.error('Recording requires the video extra: python -m pip install -e ".[video]"')
            raise
    finally:
        sys.argv = previous
