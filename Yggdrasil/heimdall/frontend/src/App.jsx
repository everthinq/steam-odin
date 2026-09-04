import { useEffect, lazy, Suspense } from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate, useLocation } from 'react-router-dom';
import Dashboard from './pages/Dashboard';
import './index.css';

// Each tool screen is code-split into its own chunk and loaded on first
// navigation, keeping the initial bundle small. The dashboard is the landing
// route, so it stays eagerly bundled (no chunk fetch on first paint).
const AddAccount = lazy(() => import('./pages/AddAccount'));
const Confirmations = lazy(() => import('./pages/Confirmations'));
const RatatoskrLayout = lazy(() => import('./pages/RatatoskrLayout'));
const RatatoskrInventory = lazy(() => import('./pages/ratatoskr/Inventory'));
const RatatoskrTransfer = lazy(() => import('./pages/ratatoskr/Transfer'));
const RatatoskrAutoStore = lazy(() => import('./pages/ratatoskr/AutoStore'));
const HuginnArbitrage = lazy(() => import('./pages/huginn/Arbitrage'));
const DraupnirPortfolios = lazy(() => import('./pages/draupnir/Portfolios'));
const DraupnirPortfolio = lazy(() => import('./pages/draupnir/Portfolio'));
const MimirVault = lazy(() => import('./pages/mimir/Vault'));

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

// Shown briefly while a code-split tool chunk loads (near-instant locally).
function RouteFallback() {
  return (
    <div className="min-h-screen flex items-center justify-center text-slate-400 text-sm animate-pulse">
      Loading…
    </div>
  );
}

function App() {
  return (
    <Router>
      <TitleManager />
      <div className="background-container">
        <div className="background-image" />
      </div>
      <div className="min-h-screen text-white relative">
        <Suspense fallback={<RouteFallback />}>
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
        </Suspense>
      </div>
    </Router>
  );
}

export default App;
