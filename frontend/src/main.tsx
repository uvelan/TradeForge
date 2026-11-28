import React from 'react';
import {createRoot} from 'react-dom/client';
import './tokens.css';
import App from './App';
import DecisionReview from './DecisionReview';
import {TimezoneProvider} from './timezone';
import {CandleColorsProvider} from './candleColors';
createRoot(document.getElementById('root')!).render(<React.StrictMode><TimezoneProvider><CandleColorsProvider>{new URLSearchParams(location.search).has('practice') ? <DecisionReview/> : <App/>}</CandleColorsProvider></TimezoneProvider></React.StrictMode>);
