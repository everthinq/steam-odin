import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import AddAccount from './pages/AddAccount';
import Confirmations from './pages/Confirmations';
import RatatoskrLayout from './pages/RatatoskrLayout';
import RatatoskrInventory from './pages/ratatoskr/Inventory';
import RatatoskrTransfer from './pages/ratatoskr/Transfer';
import RatatoskrStore from './pages/ratatoskr/Store';
import './index.css';

function App() {
  return (
    <Router>
      <div className="background-container">
        <div className="background-image" />
      </div>
      <div className="min-h-screen text-white relative">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/add-account" element={<AddAccount />} />
          <Route path="/accounts/:steamid/confirmations" element={<Confirmations />} />

          {/* Ratatoskr Routes */}
          <Route path="/ratatoskr/:steamid" element={<RatatoskrLayout />}>
            <Route index element={<Navigate to="inventory" replace />} />
            <Route path="inventory" element={<RatatoskrInventory />} />
            <Route path="transfer" element={<RatatoskrTransfer />} />
            <Route path="store" element={<RatatoskrStore />} />
          </Route>

        </Routes>
      </div>
    </Router>
  );
}

export default App;
