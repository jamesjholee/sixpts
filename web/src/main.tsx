import React from 'react'; import ReactDOM from 'react-dom/client'; import App from './App'; import AgeGate from './AgeGate'; import { initAnalytics } from './analytics'; import './styles.css'
initAnalytics()
ReactDOM.createRoot(document.getElementById('root')!).render(<React.StrictMode><AgeGate><App /></AgeGate></React.StrictMode>)
