import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    // Genera un manifest.json para que el backend pueda encontrar los ficheros
    manifest: true,
    rollupOptions: {
      // Sobrescribe el punto de entrada por defecto de Vite (index.html)
      // y le decimos que empiece directamente desde nuestro JavaScript de React.
      input: 'src/main.jsx',
    },
    // Define el directorio de salida donde se guardarán los ficheros compilados.
    outDir: 'dist',
  },
});