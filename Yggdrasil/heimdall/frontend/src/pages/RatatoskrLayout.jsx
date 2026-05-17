import React, { useState, useEffect } from 'react';
import { Outlet, Link, useParams, useLocation, useNavigate } from 'react-router-dom';
import { Package, ArrowRightLeft, LogOut, Database, LayoutDashboard } from 'lucide-react';

const RatatoskrLayout = () => {
    const { steamid } = useParams();
    const navigate = useNavigate();
    const location = useLocation();

    // Auth State
    const [status, setStatus] = useState('checking'); // checking, disconnected, connected
    // const [password, setPassword] = useState(''); // Handled by backend now
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);

    useEffect(() => {
        checkStatus();
    }, [steamid]);

    const checkStatus = async () => {
        setLoading(true);
        try {
            const res = await fetch(`/api/ratatoskr/status/${steamid}`);
            const data = await res.json();
            setStatus(data.status);
            if (data.status === 'connected') setError(null);
        } catch (err) {
            console.error(err);
            setStatus('disconnected');
        } finally {
            setLoading(false);
        }
    };

    const handleLogin = async (e) => {
        if (e) e.preventDefault();
        setLoading(true);
        setError(null);
        try {
            // Password is now handled by backend from storage
            const res = await fetch('/api/ratatoskr/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ steam_id: steamid })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Login failed');
            setStatus('connected');
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const navItems = [
        { path: 'inventory', icon: Package, label: 'Inventory' },
        { path: 'transfer', icon: ArrowRightLeft, label: 'Transfer Manager' },
    ];

    if (status === 'disconnected') {
        return (
            <div className="min-h-screen bg-odin-dark flex items-center justify-center p-4">
                <div className="bg-odin-blue border border-amber-600/30 rounded-2xl p-8 max-w-md w-full shadow-2xl relative overflow-hidden">
                    <div className="text-center mb-8">
                        <div className="w-16 h-16 bg-amber-900/30 rounded-full flex items-center justify-center mx-auto mb-4 border border-amber-500/30">
                            <Database className="text-amber-500" size={32} />
                        </div>
                        <h1 className="text-2xl font-bold text-amber-100 font-serif">Automated Ratatoskr Access</h1>
                        <p className="text-slate-400 text-sm mt-2">
                            Secure connection with the Steam Game Coordinator required.
                        </p>
                    </div>

                    <div className="space-y-4">
                        <button
                            onClick={handleLogin}
                            disabled={loading}
                            className="w-full py-3.5 bg-gradient-to-r from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600 text-white font-bold rounded-xl transition-all shadow-lg shadow-amber-900/20 disabled:opacity-50 disabled:cursor-not-allowed flex items-center justify-center gap-2"
                        >
                            {loading ? (
                                <>
                                    <div className="w-5 h-5 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                    <span>Connecting...</span>
                                </>
                            ) : (
                                <span>Establish Connection</span>
                            )}
                        </button>

                        {error && (
                            <div className="text-red-400 text-sm bg-red-900/20 p-3 rounded-lg border border-red-500/20 text-center">
                                {error}
                            </div>
                        )}
                    </div>

                    <button onClick={() => navigate('/')} className="w-full text-slate-500 hover:text-white mt-4 text-sm transition-colors">
                        Return to Dashboard
                    </button>
                </div>
            </div>
        );
    }

    // Loading State (Initial Check)
    if (status === 'checking') {
        return (
            <div className="min-h-screen bg-odin-dark flex items-center justify-center">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500"></div>
            </div>
        );
    }

    // Connected Layout
    return (
        <div className="min-h-screen bg-odin-dark flex">
            {/* Sidebar */}
            <div className="w-64 bg-odin-blue border-r border-white/5 flex flex-col fixed h-full z-10">
                <div className="p-6 border-b border-white/5">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-amber-900/30 rounded-lg border border-amber-600/30">
                            <Database className="text-amber-500" size={20} />
                        </div>
                        <div>
                            <h2 className="font-bold text-amber-100 font-serif tracking-wide">Ratatoskr</h2>
                            <p className="text-[10px] text-emerald-400 font-mono uppercase tracking-wider">Online &bull; Stable</p>
                        </div>
                    </div>
                </div>

                <nav className="flex-1 p-4 space-y-1">
                    {navItems.map((item) => {
                        const isActive = location.pathname.includes(item.path);
                        const Icon = item.icon;
                        return (
                            <Link
                                key={item.path}
                                to={item.path}
                                className={`flex items-center gap-3 px-4 py-3 rounded-xl transition-all duration-200 group ${isActive ? 'bg-amber-600/10 text-amber-400 border border-amber-600/20' : 'text-slate-400 hover:text-white hover:bg-white/5'}`}
                            >
                                <Icon size={18} className={isActive ? 'text-amber-400' : 'text-slate-500 group-hover:text-white transition-colors'} />
                                <span className="font-medium">{item.label}</span>
                            </Link>
                        );
                    })}
                </nav>

                <div className="p-4 border-t border-white/5">
                    <button
                        onClick={() => navigate('/')}
                        className="flex items-center gap-3 px-4 py-3 rounded-xl text-slate-400 hover:text-white hover:bg-white/5 w-full transition-all"
                    >
                        <LayoutDashboard size={18} />
                        <span className="font-medium">Dashboard</span>
                    </button>
                </div>
            </div>

            {/* Main Content */}
            <div className="flex-1 ml-64 p-8">
                <Outlet context={{ steamid }} />
            </div>
        </div>
    );
};

export default RatatoskrLayout;
