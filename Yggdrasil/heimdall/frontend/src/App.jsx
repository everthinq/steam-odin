import { useEffect } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
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
import MimirVault from './pages/mimir/Vault';
import './index.css';

// Sets the browser tab title to the tool that is open right now.
function TitleManager() {
  const { pathname } = useLocation();

  useEffect(() => {
    let title = 'Heimdall';
    if (pathname.startsWith('/ratatoskr')) title = 'Ratatoskr';
    else if (pathname.startsWith('/huginn')) title = 'Huginn';
    else if (pathname.startsWith('/draupnir')) title = 'Draupnir';
    else if (pathname.startsWith('/mimir')) title = 'Mímir';
    else if (pathname.startsWith('/add-account')) title = 'Add account';
    else if (pathname.includes('/confirmations')) title = 'Confirmations';
    document.title = title;
  }, [pathname]);

  return null;
}

function App() {
  return (
    <Router>
      <TitleManager />
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

          {/* Mímir Routes (credential vault) */}
          <Route path="/mimir" element={<MimirVault />} />

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
