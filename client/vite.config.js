import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react-swc'

export default defineConfig({
	// Relative assets let the same build run at / and behind nginx at /sem-search/.
	base: './',
	plugins: [react()],
	build: {
		outDir: '../public'
	},
	server: {
		port: 5173,
		proxy: {
		  '/api': {
			target: 'http://127.0.0.1:5002',
			changeOrigin: true,
			secure: false,
		  },
		  '/sem-search/api': {
			target: 'http://127.0.0.1:5002',
			changeOrigin: true,
			secure: false,
			rewrite: (path) => path.replace(/^\/sem-search/, '')
		  }
		}
 	}
})
