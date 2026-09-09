import React from 'react';
import {createRoot} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import plugin,{BoardView} from '../desktop/plugin.js';
plugin.register({registerMany(){},async rest(path,opts={}){
 const r=await fetch('/api/plugins/ai-status-board'+path,{method:opts.method||'GET'});
 if(!r.ok)throw new Error('HTTP '+r.status);return r.json();
}});
createRoot(document.getElementById('root')).render(<QueryClientProvider client={new QueryClient()}><BoardView/></QueryClientProvider>);
