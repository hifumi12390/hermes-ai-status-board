const path=require('node:path');
const fs=require('node:fs');
const modules=process.env.HERMES_NODE_MODULES;
if(!modules)throw new Error('Set HERMES_NODE_MODULES to existing Hermes node_modules');
require(path.join(modules,'esbuild')).buildSync({entryPoints:[path.join(__dirname,'preview.jsx')],bundle:true,outfile:path.join(__dirname,'preview-dist/app.js'),nodePaths:[modules],alias:{'@hermes/plugin-sdk':path.join(__dirname,'preview_sdk.js')},logLevel:'warning'});
fs.writeFileSync(path.join(__dirname,'preview-dist/index.html'),'<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>AI Status Board · isolated verification</title><style>body{margin:0;background:#10151d}*{box-sizing:border-box}#root{height:100vh}button:focus-visible,select:focus-visible,summary:focus-visible,a:focus-visible{outline:2px solid #65c9e6;outline-offset:4px}</style><div id="root"></div><script src="/app.js"></script>');
console.log('Built actual plugin UI with an isolated ctx.rest bridge');
