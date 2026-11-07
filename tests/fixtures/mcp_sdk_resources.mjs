// Owned resource callbacks on the independently published official SDK.
// Not a published server product or model/task-quality benchmark.
import {join} from 'node:path';
import {pathToFileURL} from 'node:url';
const install=process.argv[2];
const base=join(install,'node_modules/@modelcontextprotocol/server/dist');
const {McpServer,ResourceTemplate}=await import(pathToFileURL(join(base,'index.mjs')).href);
const {serveStdio}=await import(pathToFileURL(join(base,'stdio.mjs')).href);
serveStdio(()=>{
  const server=new McpServer({name:'agentlab-owned-sdk-resources',version:'1'});
  server.registerResource('reference','owned:///reference',{mimeType:'text/plain'},async uri=>({contents:[{uri:uri.href,text:'Owned SDK reference\n'}]}));
  server.registerResource('binary','owned:///binary',{mimeType:'application/octet-stream'},async uri=>({contents:[{uri:uri.href,blob:'YWJjAA=='}]}));
  server.registerResource('named reference',new ResourceTemplate('owned:///reference/{name}',{list:undefined}),{mimeType:'text/plain'},async (uri,variables)=>({contents:[{uri:uri.href,text:'Named SDK reference '+variables.name}]}));
  return server;
});
