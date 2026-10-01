#!/usr/bin/env node
// Original stdio adapter to the self-hosted relay.
import {StdioServerTransport} from '@modelcontextprotocol/sdk/server/stdio.js';
import {createRelayTools} from './relay_tools.mjs';
import {createRelayRPC} from './relay_rpc.mjs';
const backend=createRelayRPC();
const server=createRelayTools(backend.request);
process.on('exit',()=>backend.close());
process.stdin.on('end',()=>{backend.close();server.close()});
await server.connect(new StdioServerTransport());
