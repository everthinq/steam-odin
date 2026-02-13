import React, { useState, useEffect } from 'react';
import { Plus, RefreshCw, Search, Trash2, Settings, Eye, EyeOff } from 'lucide-react';
import { Link } from 'react-router-dom';
import AccountCard from '../components/AccountCard';
import GlobalConfirmationsModal from '../components/GlobalConfirmationsModal';

const Dashboard = () => {
    const [accounts, setAccounts] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [isVigilMode, setIsVigilMode] = useState(false);

    const fetchAccounts = async () => {
        try {
            const response = await fetch('/api/accounts');
            if (!response.ok) throw new Error('Failed to fetch accounts');
            const data = await response.json();
            setAccounts(data.accounts || []);
            setError(null);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    const handleRemoveAll = async () => {
        if (!confirm(`Are you sure you want to remove ALL ${accounts.length} accounts? This action cannot be undone.`)) {
            return;
        }

        try {
            const response = await fetch('/api/accounts', { method: 'DELETE' });
            if (response.ok) {
                await fetchAccounts();
            } else {
                throw new Error('Failed to remove accounts');
            }
        } catch (err) {
            setError(err.message);
        }
    };

    useEffect(() => {
        fetchAccounts();
        // Poll every second to keep timer in sync and codes updated
        const interval = setInterval(fetchAccounts, 1000);
        return () => clearInterval(interval);
    }, []);

    // Filter accounts based on search query
    const filteredAccounts = accounts.filter(account =>
        account.account_name.toLowerCase().includes(searchQuery.toLowerCase())
    );

    return (
        <div className="min-h-screen text-white p-4 md:p-8">
            <GlobalConfirmationsModal isOpen={isSettingsOpen} onClose={() => setIsSettingsOpen(false)} />

            {/* Heimdall's Vigil Toggle */}
            <button
                onClick={() => setIsVigilMode(!isVigilMode)}
                className={`fixed top-6 right-6 z-50 p-3 rounded-full transition-all duration-500 group ${isVigilMode
                    ? 'bg-odin-dark/20 border border-asgard-gold/20 hover:bg-odin-dark/60'
                    : 'bg-odin-blue/40 border border-white/5 hover:bg-odin-blue/60'
                    } backdrop-blur-md shadow-2xl hover:scale-110 hover:shadow-asgard-gold/20`}
                title={isVigilMode ? "Return from Vigil" : "Heimdall's Vigil"}
            >
                <div className={`relative ${isVigilMode ? 'text-asgard-gold' : 'text-frost-white/60 group-hover:text-asgard-gold'}`}>
                    {isVigilMode ? <EyeOff size={24} /> : <Eye size={24} />}
                    <div className={`absolute inset-0 bg-asgard-gold blur-md transition-opacity duration-500 ${isVigilMode ? 'opacity-50' : 'opacity-0 group-hover:opacity-40'}`} />
                </div>
            </button>

            <div className={`max-w-7xl mx-auto transition-all duration-1000 ease-in-out transform ${isVigilMode ? 'opacity-0 pointer-events-none' : 'opacity-100'
                }`}>
                <header className="flex flex-col md:flex-row justify-between items-center mb-6 md:mb-10 gap-4 md:gap-0">
                    <div className="text-center md:text-left">
                        <h1 className="text-3xl md:text-4xl font-bold bg-gradient-to-r from-cyan-300 via-blue-500 to-purple-600 bg-clip-text text-transparent drop-shadow-lg tracking-wider">
                            Steam Heimdall Authenticator
                        </h1>
                        <p className="text-slate-400 mt-2 text-base md:text-lg tracking-wide border-l-0 md:border-l-2 border-blue-500 pl-0 md:pl-3">
                            The Watchman of Your Steam Guard
                        </p>
                    </div>
                    <div className="flex flex-wrap justify-center gap-3 w-full md:w-auto">
                        <Link
                            to="/add-account"
                            className="flex-1 md:flex-none justify-center flex items-center gap-2 bg-blue-600/20 hover:bg-blue-600/40 text-blue-200 border border-blue-500/30 backdrop-blur-md px-4 py-2 rounded-lg transition-all hover:scale-105 active:scale-95 font-medium shadow-lg shadow-blue-900/20 text-sm md:text-base whitespace-nowrap"
                        >
                            <Plus size={18} />
                            Import
                        </Link>
                        <button
                            onClick={() => setIsSettingsOpen(true)}
                            className="flex-1 md:flex-none justify-center flex items-center gap-2 bg-purple-600/20 hover:bg-purple-600/40 text-purple-200 border border-purple-500/30 backdrop-blur-md px-4 py-2 rounded-lg transition-all hover:scale-105 active:scale-95 font-medium shadow-lg shadow-purple-900/20 text-sm md:text-base whitespace-nowrap"
                        >
                            <Settings size={18} />
                            Confirms
                        </button>
                        {accounts.length > 0 && (
                            <button
                                onClick={handleRemoveAll}
                                className="flex-1 md:flex-none justify-center flex items-center gap-2 bg-[#4a040b] hover:bg-[#630611] text-red-100 border border-[#2b0206] px-4 py-2 rounded-lg transition-all hover:scale-105 active:scale-95 font-medium shadow-lg shadow--[#4a040b]/50 text-sm md:text-base whitespace-nowrap"
                            >
                                <Trash2 size={18} />
                                Remove All
                            </button>
                        )}
                    </div>
                </header>

                {accounts.length > 0 && (
                    <div className="mb-8">
                        <div className="relative max-w-md">
                            <Search className="absolute left-4 top-1/2 transform -translate-y-1/2 text-slate-400" size={20} />
                            <input
                                type="text"
                                placeholder="Search accounts..."
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                className="w-full pl-12 pr-4 py-3 glass-panel rounded-lg text-white placeholder-slate-500 focus:outline-none focus:ring-2 focus:ring-blue-500/50 transition-all"
                            />
                            {searchQuery && (
                                <span className="absolute right-4 top-1/2 transform -translate-y-1/2 text-xs text-slate-400">
                                    {filteredAccounts.length} of {accounts.length}
                                </span>
                            )}
                        </div>
                    </div>
                )}

                {error && (
                    <div className="bg-red-500/10 border border-red-500/50 text-red-500 p-4 rounded-lg mb-8">
                        Error: {error}
                    </div>
                )}

                {loading && accounts.length === 0 ? (
                    <div className="flex justify-center items-center h-64">
                        <RefreshCw className="animate-spin text-blue-500" size={40} />
                    </div>
                ) : (
                    <>
                        {accounts.length === 0 ? (
                            <div className="text-center py-20 glass-panel rounded-2xl">
                                <div className="inline-block p-4 bg-slate-800 rounded-full mb-4">
                                    <Plus size={40} className="text-slate-400" />
                                </div>
                                <h2 className="text-xl font-semibold mb-2">No Accounts Linked</h2>
                                <p className="text-slate-400 mb-6">Import your Steam Guard files to get started.</p>
                                <Link
                                    to="/add-account"
                                    className="inline-flex items-center gap-2 text-blue-400 hover:text-blue-300 transition-colors"
                                >
                                    Import maFiles &rarr;
                                </Link>
                            </div>
                        ) : filteredAccounts.length === 0 ? (
                            <div className="text-center py-20 glass-panel rounded-2xl">
                                <div className="inline-block p-4 bg-slate-800 rounded-full mb-4">
                                    <Search size={40} className="text-slate-400" />
                                </div>
                                <h2 className="text-xl font-semibold mb-2">No Accounts Found</h2>
                                <p className="text-slate-400 mb-6">No accounts match "{searchQuery}"</p>
                                <button
                                    onClick={() => setSearchQuery('')}
                                    className="text-blue-400 hover:text-blue-300 transition-colors"
                                >
                                    Clear Search
                                </button>
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4 md:gap-6">
                                {filteredAccounts.map((account) => (
                                    <AccountCard key={account.steamid} account={account} onDelete={fetchAccounts} />
                                ))}
                            </div>
                        )}
                    </>
                )}
            </div>
        </div>
    );
};

export default Dashboard;
