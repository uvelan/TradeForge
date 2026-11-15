import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import {TimezoneProvider} from './timezone';
createRoot(document.getElementById('root')!).render(<React.StrictMode><TimezoneProvider><App/></TimezoneProvider></React.StrictMode>);
