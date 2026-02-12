import { BrowserRouter as Router, Routes, Route } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import AddAccount from './pages/AddAccount';
import Confirmations from './pages/Confirmations';
import './index.css';

function App() {
  return (
    <Router>
      <div className="bg-slate-950 min-h-screen text-white">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/add-account" element={<AddAccount />} />
          <Route path="/accounts/:steamid/confirmations" element={<Confirmations />} />
        </Routes>
      </div>
    </Router>
  );
}

export default App;
