import React, { useState, useEffect } from 'react';
import { Plus, RefreshCw } from 'lucide-react';
import { Link } from 'react-router-dom';
import AccountCard from '../components/AccountCard';

const Dashboard = () => {
    const [accounts, setAccounts] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

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

    useEffect(() => {
        fetchAccounts();
        // Poll every second to keep timer in sync and codes updated
        const interval = setInterval(fetchAccounts, 1000);
        return () => clearInterval(interval);
    }, []);

    return (
        <div className="min-h-screen bg-slate-950 text-white p-8">
            <div className="max-w-7xl mx-auto">
                <header className="flex justify-between items-center mb-10">
                    <div>
                        <h1 className="text-4xl font-bold bg-gradient-to-r from-cyan-300 via-blue-500 to-purple-600 bg-clip-text text-transparent drop-shadow-lg tracking-wider">
                            Steam Heimdall Authenticator
                        </h1>
                        <p className="text-slate-400 mt-2 text-lg tracking-wide border-l-2 border-blue-500 pl-3">
                            The Watchman of Your Steam Guard
                        </p>
                    </div>
                    <Link
                        to="/add-account"
                        className="flex items-center gap-2 bg-blue-600 hover:bg-blue-700 text-white px-4 py-2 rounded-lg transition-colors font-medium shadow-lg shadow-blue-500/20"
                    >
                        <Plus size={20} />
                        Import maFiles
                    </Link>
                </header>

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
                        ) : (
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-6">
                                {accounts.map((account) => (
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
