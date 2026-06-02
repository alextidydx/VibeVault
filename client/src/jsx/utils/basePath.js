// The app runs both at / on port 5002 and mounted at /sem-search/ behind nginx.
export const BASE = (() => {
	const mounts = ['/sem-search/'];
	const path = window.location.pathname;

	return mounts.find((mount) => path === mount.slice(0, -1) || path.startsWith(mount)) || '/';
})();
