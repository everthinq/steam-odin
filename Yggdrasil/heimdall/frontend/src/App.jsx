import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import AddAccount from './pages/AddAccount';
import Confirmations from './pages/Confirmations';
import RatatoskrLayout from './pages/RatatoskrLayout';
import RatatoskrInventory from './pages/ratatoskr/Inventory';
import RatatoskrTransfer from './pages/ratatoskr/Transfer';
import RatatoskrAutoStore from './pages/ratatoskr/AutoStore';
import HuginnArbitrage from './pages/huginn/Arbitrage';
import DraupnirPortfolios from './pages/draupnir/Portfolios';
import DraupnirPortfolio from './pages/draupnir/Portfolio';
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

          {/* Huginn Routes */}
          <Route path="/huginn" element={<HuginnArbitrage />} />

          {/* Draupnir Routes (portfolio tracker) */}
          <Route path="/draupnir" element={<DraupnirPortfolios />} />
          <Route path="/draupnir/:portfolioId" element={<DraupnirPortfolio />} />

          {/* Ratatoskr Routes */}
          <Route path="/ratatoskr/:steamid" element={<RatatoskrLayout />}>
            <Route index element={<Navigate to="inventory" replace />} />
            <Route path="inventory" element={<RatatoskrInventory />} />
            <Route path="transfer" element={<RatatoskrTransfer />} />
            <Route path="auto-store" element={<RatatoskrAutoStore />} />
          </Route>

        </Routes>
      </div>
    </Router>
  );
}

export default App;
