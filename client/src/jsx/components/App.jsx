import React from 'react';
import { BrowserRouter, Routes, Route } from "react-router-dom"
import '../../styles/App.scss'
import Home from './pages/Home';

const App = () => {
	return (
		<BrowserRouter>
			<Routes>
				<Route path="*" element={<Home settings={{}} />} />
			</Routes>
		</BrowserRouter>
	);
}


export default App;
