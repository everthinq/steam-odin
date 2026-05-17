import React, { useState, useEffect } from 'react';
import { useOutletContext } from 'react-router-dom';
import { ArrowRight, ArrowLeft } from 'lucide-react';

const CDN_URL = 'https://community.cloudflare.steamstatic.com/economy/image/';

const RatatoskrTransfer = () => {
    const { steamid } = useOutletContext();

    // Data
    const [inventory, setInventory] = useState([]);
    const [caskets, setCaskets] = useState([]);
    const [casketItems, setCasketItems] = useState([]);
    const [selectedCasket, setSelectedCasket] = useState(null);

    // UI State
    const [loading, setLoading] = useState(false);
    const [invSearch, setInvSearch] = useState('');
    const [selectedInvItems, setSelectedInvItems] = useState([]);
    const [selectedCasketItems, setSelectedCasketItems] = useState([]);
    const [successMsg, setSuccessMsg] = useState(null);

    useEffect(() => {
        fetchInventory();
        fetchCaskets();
    }, [steamid]);

    useEffect(() => {
        if (selectedCasket) {
            fetchCasketContents(selectedCasket.item_id);
            setSelectedCasketItems([]);
        } else {
            setCasketItems([]);
        }
    }, [selectedCasket]);

    const fetchInventory = async () => {
        try {
            const res = await fetch(`/api/ratatoskr/inventory/${steamid}`);
            const data = await res.json();
            if (data.items) setInventory(data.items);
        } catch (err) { console.error(err); }
    };

    const fetchCaskets = async () => {
        try {
            const res = await fetch(`/api/ratatoskr/caskets/${steamid}`);
            const data = await res.json();
            if (data.caskets) {
                setCaskets(data.caskets);
                if (data.caskets.length > 0 && !selectedCasket) {
                    setSelectedCasket(data.caskets[0]);
                }
            }
        } catch (err) { console.error(err); }
    };

    const fetchCasketContents = async (casketId) => {
        setLoading(true);
        try {
            const res = await fetch(`/api/ratatoskr/casket/${steamid}/${casketId}`);
            const data = await res.json();
            if (data.items) setCasketItems(data.items);
        } catch (err) { console.error(err); }
        finally { setLoading(false); }
    };

    const handleMove = async (direction) => {
        if (!selectedCasket) return;

        let itemsToMove = [];
        let source, target;

        if (direction === 'to_storage') {
            itemsToMove = selectedInvItems;
            source = 'inventory';
            target = 'casket';
        } else {
            itemsToMove = selectedCasketItems;
            source = 'casket';
            target = 'inventory';
        }

        if (itemsToMove.length === 0) return;

        // Process sequentially for now (could be optimized)
        setLoading(true);
        try {
            for (const itemID of itemsToMove) {
                await fetch('/api/ratatoskr/move', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        steamID: steamid,
                        itemID,
                        casketID: selectedCasket.item_id,
                        source,
                        target
                    })
                });
            }

            setSuccessMsg(`Moved ${itemsToMove.length} items successfully!`);
            setTimeout(() => setSuccessMsg(null), 3000);

            // Refresh
            setTimeout(() => {
                fetchInventory();
                fetchCasketContents(selectedCasket.item_id);
            }, 1000);

            setSelectedInvItems([]);
            setSelectedCasketItems([]);

        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const toggleInvSelection = (id) => {
        setSelectedInvItems(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
    };

    const toggleCasketSelection = (id) => {
        setSelectedCasketItems(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);
    };

    const filteredInventory = inventory.filter(item =>
        item.item_name.toLowerCase().includes(invSearch.toLowerCase()) &&
        item.def_index !== 1201
    );

    return (
        <div className="h-[calc(100vh-6rem)] flex flex-col">
            <h1 className="text-3xl font-bold text-white font-serif mb-6">Transfer Manager</h1>

            {successMsg && (
                <div className="mb-4 bg-emerald-500/20 border border-emerald-500/30 text-emerald-300 px-4 py-2 rounded-lg text-sm text-center">
                    {successMsg}
                </div>
            )}

            <div className="flex-1 flex gap-4 min-h-0">
                {/* Left: Inventory */}
                <div className="flex-1 bg-odin-blue/30 border border-white/5 rounded-2xl flex flex-col overflow-hidden">
                    <div className="p-4 border-b border-white/5 bg-black/20 flex justify-between items-center">
                        <span className="font-bold text-slate-300">Your Inventory</span>
                        <input
                            type="text"
                            placeholder="Search..."
                            value={invSearch}
                            onChange={(e) => setInvSearch(e.target.value)}
                            className="bg-black/30 border border-white/10 rounded-lg px-2 py-1 text-sm text-white w-32 focus:outline-none"
                        />
                    </div>
                    <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
                        <div className="grid grid-cols-3 xl:grid-cols-4 gap-2">
                            {filteredInventory.map(item => (
                                <div
                                    key={item.item_id}
                                    onClick={() => toggleInvSelection(item.item_id)}
                                    className={`aspect-square rounded-lg border cursor-pointer relative ${selectedInvItems.includes(item.item_id) ? 'border-amber-500 bg-amber-500/20' : 'border-white/5 hover:bg-white/5'}`}
                                >
                                    <img src={`https://api.steamapis.com/image/item/730/${encodeURIComponent(item.item_name)}`} className="w-full h-full object-contain p-2" />
                                    <div className="absolute bottom-0 left-0 w-full p-1 bg-black/60 text-[10px] text-center truncate text-slate-300">{item.item_name}</div>
                                </div>
                            ))}
                        </div>
                    </div>
                </div>

                {/* Middle: Actions */}
                <div className="w-16 flex flex-col items-center justify-center gap-4">
                    <button
                        onClick={() => handleMove('to_storage')}
                        disabled={selectedInvItems.length === 0 || !selectedCasket || loading}
                        className="p-3 bg-amber-600 hover:bg-amber-500 disabled:opacity-30 rounded-xl transition-all"
                    >
                        <ArrowRight size={20} />
                    </button>
                    <button
                        onClick={() => handleMove('from_storage')}
                        disabled={selectedCasketItems.length === 0 || !selectedCasket || loading}
                        className="p-3 bg-slate-700 hover:bg-slate-600 disabled:opacity-30 rounded-xl transition-all"
                    >
                        <ArrowLeft size={20} />
                    </button>
                </div>

                {/* Right: Storage */}
                <div className="flex-1 bg-odin-blue/30 border border-white/5 rounded-2xl flex flex-col overflow-hidden">
                    <div className="p-4 border-b border-white/5 bg-black/20">
                        <select
                            className="w-full bg-black/30 border border-white/10 rounded-lg px-3 py-1.5 text-sm text-white focus:outline-none"
                            value={selectedCasket?.item_id || ''}
                            onChange={(e) => {
                                const c = caskets.find(x => x.item_id === e.target.value);
                                setSelectedCasket(c);
                            }}
                        >
                            <option value="">Select Storage Unit...</option>
                            {caskets.map(c => (
                                <option key={c.item_id} value={c.item_id}>
                                    {c.item_customname || c.item_name} ({c.item_storage_total} items)
                                </option>
                            ))}
                        </select>
                    </div>
                    <div className="flex-1 overflow-y-auto p-4 custom-scrollbar">
                        {loading && casketItems.length === 0 ? (
                            <div className="flex justify-center p-10"><div className="animate-spin rounded-full h-8 w-8 border-b-2 border-white"></div></div>
                        ) : (
                            <div className="grid grid-cols-3 xl:grid-cols-4 gap-2">
                                {casketItems.map(item => (
                                    <div
                                        key={item.item_id}
                                        onClick={() => toggleCasketSelection(item.item_id)}
                                        className={`aspect-square rounded-lg border cursor-pointer relative ${selectedCasketItems.includes(item.item_id) ? 'border-amber-500 bg-amber-500/20' : 'border-white/5 hover:bg-white/5'}`}
                                    >
                                        <img src={`https://api.steamapis.com/image/item/730/${encodeURIComponent(item.item_name)}`} className="w-full h-full object-contain p-2" />
                                        <div className="absolute bottom-0 left-0 w-full p-1 bg-black/60 text-[10px] text-center truncate text-slate-300">{item.item_name}</div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                </div>
            </div>
        </div>
    );
};

export default RatatoskrTransfer;
