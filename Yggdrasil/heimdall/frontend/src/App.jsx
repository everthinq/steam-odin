import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import AddAccount from './pages/AddAccount';
import './index.css';

function App() {
  return (
    <Router>
      <div className="bg-slate-950 min-h-screen text-white">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/add-account" element={<AddAccount />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
