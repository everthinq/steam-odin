import React, { useState, useEffect } from 'react';
import { X, Package, ArrowRight, ArrowLeft, RefreshCw, Search, Archive, Database } from 'lucide-react';

const CDN_URL = 'https://community.cloudflare.steamstatic.com/economy/image/';

const RatatoskrModal = ({ isOpen, onClose, account }) => {
    const [status, setStatus] = useState('checking'); // checking, disconnected, connected
    // const [password, setPassword] = useState(''); // Handled by backend now
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const [successMsg, setSuccessMsg] = useState(null);

    // Data
    const [inventory, setInventory] = useState([]);
    const [caskets, setCaskets] = useState([]);
    const [selectedCasket, setSelectedCasket] = useState(null);
    const [casketItems, setCasketItems] = useState([]);
    const [searchInv, setSearchInv] = useState('');

    // Selection
    const [selectedInvItem, setSelectedInvItem] = useState(null);
    const [selectedCasketItem, setSelectedCasketItem] = useState(null);

    useEffect(() => {
        if (isOpen && account) {
            checkStatus();
        }
    }, [isOpen, account]);

    useEffect(() => {
        if (status === 'connected') {
            fetchInventory();
            fetchCaskets();
        }
    }, [status]);

    useEffect(() => {
        if (selectedCasket) {
            fetchCasketContents(selectedCasket.item_id);
        } else {
            setCasketItems([]);
        }
    }, [selectedCasket]);

    const checkStatus = async () => {
        setLoading(true);
        try {
            const res = await fetch(`/api/ratatoskr/status/${account.steamid}`);
            const data = await res.json();
            setStatus(data.status);
            if (data.status === 'connected') setError(null);
        } catch (err) {
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
                body: JSON.stringify({ steam_id: account.steamid })
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

    const fetchInventory = async () => {
        setLoading(true);
        try {
            const res = await fetch(`/api/ratatoskr/inventory/${account.steamid}`);
            const data = await res.json();
            if (data.items) setInventory(data.items);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const fetchCaskets = async () => {
        try {
            const res = await fetch(`/api/ratatoskr/caskets/${account.steamid}`);
            const data = await res.json();
            if (data.caskets) {
                setCaskets(data.caskets);
                if (data.caskets.length > 0 && !selectedCasket) {
                    setSelectedCasket(data.caskets[0]);
                }
            }
        } catch (err) {
            console.error(err);
        }
    };

    const fetchCasketContents = async (casketId) => {
        setLoading(true);
        try {
            const res = await fetch(`/api/ratatoskr/casket/${account.steamid}/${casketId}`);
            const data = await res.json();
            if (data.items) setCasketItems(data.items);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const handleMove = async (direction) => {
        if (!selectedCasket) return;

        let itemID, source, target;

        if (direction === 'to_storage') {
            if (!selectedInvItem) return;
            itemID = selectedInvItem.item_id;
            source = 'inventory';
            target = 'casket';
        } else {
            if (!selectedCasketItem) return;
            itemID = selectedCasketItem.item_id;
            source = 'casket';
            target = 'inventory';
        }

        setLoading(true);
        try {
            const res = await fetch('/api/ratatoskr/move', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    steamID: account.steamid,
                    itemID,
                    casketID: selectedCasket.item_id,
                    source,
                    target
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.error || 'Move failed');

            setSuccessMsg(`Moved item!`);
            setTimeout(() => setSuccessMsg(null), 3000);

            // Refresh Both
            setTimeout(() => {
                fetchInventory();
                fetchCasketContents(selectedCasket.item_id);
            }, 1000); // Give steam a second

            setSelectedInvItem(null);
            setSelectedCasketItem(null);

        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    };

    // Filter items
    const filteredInventory = inventory.filter(item =>
        item.item_name.toLowerCase().includes(searchInv.toLowerCase()) &&
        item.def_index !== 1201 // Don't show caskets in inventory list to move?
    );

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-odin-dark/95 flex items-center justify-center z-50 backdrop-blur-md p-4 animate-in fade-in duration-200">
            <div className="bg-odin-blue border border-amber-600/30 rounded-2xl w-full max-w-6xl h-[90vh] shadow-2xl relative overflow-hidden flex flex-col">

                {/* Header */}
                <div className="flex justify-between items-center p-6 border-b border-white/5 bg-black/20">
                    <div className="flex items-center gap-3">
                        <div className="p-2 bg-amber-900/40 rounded-lg border border-amber-600/30">
                            <Package className="text-amber-400" size={24} />
                        </div>
                        <div>
                            <h2 className="text-2xl font-bold text-amber-100 font-serif tracking-wide">Ratatoskr Interface</h2>
                            <p className="text-amber-400/60 text-xs uppercase tracking-widest font-bold">Connected to GC</p>
                        </div>
                    </div>
                    <button onClick={onClose} className="text-slate-400 hover:text-white transition-colors p-2 hover:bg-white/5 rounded-lg">
                        <X size={24} />
                    </button>
                </div>

                {/* Body */}
                <div className="flex-1 overflow-hidden relative">
                    {loading && (
                        <div className="absolute inset-0 bg-odin-dark/60 backdrop-blur-[2px] flex items-center justify-center z-50">
                            <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500"></div>
                        </div>
                    )}

                    {status === 'disconnected' ? (
                        <div className="flex flex-col items-center justify-center h-full p-8 max-w-md mx-auto">
                            <Database className="text-amber-600/50 mb-6" size={64} />
                            <h3 className="text-xl font-bold text-white mb-2">Authentication Required</h3>
                            <p className="text-slate-400 text-center mb-6">
                                Ratatoskr requires a dedicated session with the Steam Game Coordinator to manage your storage units.
                            </p>
                            <button
                                onClick={handleLogin}
                                disabled={loading}
                                className="w-full py-3 bg-amber-600 hover:bg-amber-500 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold rounded-lg transition-colors shadow-lg shadow-amber-900/20 flex items-center justify-center gap-2"
                            >
                                {loading ? (
                                    <>
                                        <div className="animate-spin rounded-full h-4 w-4 border-b-2 border-white"></div>
                                        Connecting...
                                    </>
                                ) : (
                                    'Establish Connection'
                                )}
                            </button>
                            {error && <div className="mt-4 text-red-400 bg-red-900/20 px-4 py-2 rounded-lg border border-red-500/30 w-full text-center text-sm">{error}</div>}
                        </div>
                    ) : (
                        <div className="flex h-full">
                            {/* LEFT: Inventory */}
                            <div className="flex-1 flex flex-col border-r border-white/5 bg-black/10">
                                <div className="p-4 border-b border-white/5 flex gap-2">
                                    <div className="relative flex-1">
                                        <Search className="absolute left-3 top-2.5 text-slate-500" size={16} />
                                        <input
                                            type="text"
                                            placeholder="Search Inventory..."
                                            value={searchInv}
                                            onChange={(e) => setSearchInv(e.target.value)}
                                            className="w-full bg-black/20 border border-white/5 rounded-lg pl-9 pr-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/50"
                                        />
                                    </div>
                                    <button onClick={fetchInventory} className="p-2 bg-white/5 hover:bg-white/10 rounded-lg text-slate-400 hover:text-white transition-colors">
                                        <RefreshCw size={18} />
                                    </button>
                                </div>
                                <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
                                    <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-3">
                                        {filteredInventory.map(item => (
                                            <div
                                                key={item.item_id}
                                                onClick={() => setSelectedInvItem(item)}
                                                className={`aspect-square bg-odin-dark/40 rounded-xl border cursor-pointer relative group transition-all duration-200 ${selectedInvItem?.item_id === item.item_id ? 'border-amber-500 ring-1 ring-amber-500 bg-amber-500/10' : 'border-white/5 hover:border-white/20 hover:bg-white/5'}`}
                                            >
                                                <div className="absolute top-2 right-2 z-10">
                                                    {item.stattrak && <span className="text-[10px] bg-orange-600/80 text-white px-1.5 py-0.5 rounded mr-1">ST</span>}
                                                    {item.rarityName && <span className={`text-[10px] px-1.5 py-0.5 rounded bg-black/50 text-white truncate max-w-[60px] inline-block`}>{item.rarityName}</span>}
                                                </div>
                                                <div className="w-full h-full p-2 flex items-center justify-center">
                                                    <img src={`${CDN_URL}${item.item_url}`} alt={item.item_name} className="max-w-full max-h-full object-contain filter drop-shadow-lg" />
                                                </div>
                                                <div className="absolute bottom-0 left-0 w-full p-2 bg-gradient-to-t from-black/80 to-transparent pt-6">
                                                    <p className="text-[10px] text-center text-slate-300 truncate font-medium">{item.item_name}</p>
                                                </div>
                                            </div>
                                        ))}
                                    </div>
                                    {filteredInventory.length === 0 && <div className="text-center text-slate-500 mt-10">No items found</div>}
                                </div>
                            </div>

                            {/* CENTER: Actions */}
                            <div className="w-16 flex flex-col items-center justify-center gap-4 border-r border-white/5 bg-black/20 z-10">
                                <button
                                    onClick={() => handleMove('to_storage')}
                                    disabled={!selectedInvItem || !selectedCasket}
                                    className="p-3 bg-amber-600 hover:bg-amber-500 disabled:opacity-30 disabled:hover:bg-amber-600 rounded-xl transition-all shadow-lg hover:scale-110 active:scale-95 text-white"
                                    title="Deposit to Storage"
                                >
                                    <ArrowRight size={24} />
                                </button>
                                <button
                                    onClick={() => handleMove('from_storage')}
                                    disabled={!selectedCasketItem}
                                    className="p-3 bg-slate-700 hover:bg-slate-600 disabled:opacity-30 disabled:hover:bg-slate-700 rounded-xl transition-all shadow-lg hover:scale-110 active:scale-95 text-white"
                                    title="Retrieve from Storage"
                                >
                                    <ArrowLeft size={24} />
                                </button>
                            </div>

                            {/* RIGHT: Storage Units */}
                            <div className="flex-1 flex flex-col bg-black/10">
                                <div className="p-4 border-b border-white/5">
                                    <select
                                        className="w-full bg-black/20 border border-white/5 rounded-lg px-3 py-2 text-sm text-white focus:outline-none focus:border-amber-500/50 appearance-none cursor-pointer"
                                        value={selectedCasket?.item_id || ''}
                                        onChange={(e) => {
                                            const casket = caskets.find(c => c.item_id === e.target.value);
                                            setSelectedCasket(casket);
                                        }}
                                    >
                                        <option value="" disabled>Select Storage Unit...</option>
                                        {caskets.map(casket => (
                                            <option key={casket.item_id} value={casket.item_id}>
                                                {casket.item_customname ? `"${casket.item_customname}"` : casket.item_name} ({casket.item_storage_total} items)
                                            </option>
                                        ))}
                                    </select>
                                </div>

                                <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
                                    {selectedCasket ? (
                                        <div className="grid grid-cols-3 sm:grid-cols-4 lg:grid-cols-5 gap-3">
                                            {casketItems.map(item => (
                                                <div
                                                    key={item.item_id}
                                                    onClick={() => setSelectedCasketItem(item)}
                                                    className={`aspect-square bg-odin-dark/40 rounded-xl border cursor-pointer relative group transition-all duration-200 ${selectedCasketItem?.item_id === item.item_id ? 'border-amber-500 ring-1 ring-amber-500 bg-amber-500/10' : 'border-white/5 hover:border-white/20 hover:bg-white/5'}`}
                                                >
                                                    <div className="w-full h-full p-2 flex items-center justify-center">
                                                        <img src={`${CDN_URL}${item.item_url}`} alt={item.item_name} className="max-w-full max-h-full object-contain filter drop-shadow-lg" />
                                                    </div>
                                                    <div className="absolute bottom-0 left-0 w-full p-2 bg-gradient-to-t from-black/80 to-transparent pt-6">
                                                        <p className="text-[10px] text-center text-slate-300 truncate font-medium">{item.item_name}</p>
                                                    </div>
                                                </div>
                                            ))}
                                            {casketItems.length === 0 && <div className="col-span-full text-center text-slate-500 mt-10">Empty Storage Unit</div>}
                                        </div>
                                    ) : (
                                        <div className="h-full flex flex-col items-center justify-center text-slate-500">
                                            <Archive size={48} className="mb-4 opacity-50" />
                                            <p>Select a Storage Unit to view contents</p>
                                        </div>
                                    )}
                                </div>
                                <div className="p-3 border-t border-white/5 bg-black/20 text-xs text-slate-500 flex justify-between">
                                    <span>Selected: {selectedCasket?.item_customname || 'None'}</span>
                                    <span>Capacity: {selectedCasket ? selectedCasket.item_storage_total : 0} / 1000</span>
                                </div>
                            </div>
                        </div>
                    )}
                </div>

                {/* Footer Status */}
                {successMsg && (
                    <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 bg-emerald-600 text-white px-4 py-2 rounded-full shadow-lg text-sm font-bold animate-in fade-in slide-in-from-bottom-4">
                        {successMsg}
                    </div>
                )}
                {error && (
                    <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 bg-red-600 text-white px-4 py-2 rounded-full shadow-lg text-sm font-bold animate-in fade-in slide-in-from-bottom-4">
                        {error}
                    </div>
                )}
            </div>
        </div>
    );
};

export default RatatoskrModal;
