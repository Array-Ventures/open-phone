"""Controller-side client for the optional Shortcut bridge."""
import os
import re
from openphone.clients.local import Client


class BridgeClient(Client):
    def __init__(self,url=None,token_file=None):
        super().__init__(url or os.environ.get('OPEN_PHONE_BRIDGE_URL','http://127.0.0.1:8767'),token_file)
    def enqueue(self,kind,payload,ttl=60):
        return self.request('/v1/bridge/actions',{'kind':kind,'payload':payload,'ttl':ttl})
    def _path(self,action_id):
        if not isinstance(action_id,str) or not re.fullmatch('[0-9a-f]{32}',action_id):raise ValueError('Invalid action ID')
        return '/v1/bridge/actions/'+action_id
    def result(self,action_id):return self.request(self._path(action_id))
    def cancel(self,action_id):return self.request(self._path(action_id)+'/cancel',{})
    def status(self):return self.request('/v1/bridge/status')
