import React from 'react';
import {createRoot} from 'react-dom/client';
import App from './App';
import {TimezoneProvider} from './timezone';
import {CandleColorsProvider} from './candleColors';
createRoot(document.getElementById('root')!).render(<React.StrictMode><TimezoneProvider><CandleColorsProvider><App/></CandleColorsProvider></TimezoneProvider></React.StrictMode>);
