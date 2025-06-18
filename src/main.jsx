import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App.jsx'; // Esto asume que tienes un componente principal llamado App.jsx
import './index.css';     // Esto asume que tienes un fichero de estilos llamado index.css

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)