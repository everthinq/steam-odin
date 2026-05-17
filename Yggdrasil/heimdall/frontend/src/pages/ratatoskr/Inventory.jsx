import React, { useState, useEffect } from 'react';
import { useOutletContext } from 'react-router-dom';
import { Search, RefreshCw, Filter, Copy } from 'lucide-react';

const CDN_URL = 'https://community.cloudflare.steamstatic.com/economy/image/';

const RatatoskrInventory = () => {
    const { steamid } = useOutletContext();
    const [inventory, setInventory] = useState([]);
    const [loading, setLoading] = useState(true);
    const [search, setSearch] = useState('');
    const [selectedItems, setSelectedItems] = useState([]);

    useEffect(() => {
        fetchInventory();
    }, [steamid]);

    const fetchInventory = async () => {
        setLoading(true);
        try {
            const res = await fetch(`/api/ratatoskr/inventory/${steamid}`);
            const data = await res.json();
            if (data.items) setInventory(data.items);
        } catch (err) {
            console.error(err);
        } finally {
            setLoading(false);
        }
    };

    const toggleSelection = (itemId) => {
        setSelectedItems(prev =>
            prev.includes(itemId)
                ? prev.filter(id => id !== itemId)
                : [...prev, itemId]
        );
    };

    const filteredInventory = inventory.filter(item =>
        item.item_name.toLowerCase().includes(search.toLowerCase()) &&
        item.def_index !== 1201 // Hide caskets
    );

    return (
        <div className="animate-in fade-in duration-500">
            {/* Header / Actions */}
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-8">
                <div>
                    <h1 className="text-3xl font-bold text-white font-serif mb-1">Inventory</h1>
                    <p className="text-slate-400 dark:text-slate-500">Manage and view your CS2 items.</p>
                </div>

                <div className="flex items-center gap-3 w-full md:w-auto">
                    <div className="relative flex-1 md:w-64">
                        <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" size={16} />
                        <input
                            type="text"
                            placeholder="Search items..."
                            value={search}
                            onChange={(e) => setSearch(e.target.value)}
                            className="w-full bg-black/20 border border-white/10 rounded-xl pl-9 pr-4 py-2.5 text-white focus:outline-none focus:border-amber-500/50 transition-all placeholder:text-slate-600"
                        />
                    </div>
                    <button
                        onClick={fetchInventory}
                        className="p-2.5 bg-white/5 hover:bg-white/10 rounded-xl text-slate-400 hover:text-white transition-colors border border-white/5"
                    >
                        <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
                    </button>
                </div>
            </div>

            {/* Grid */}
            {loading ? (
                <div className="flex items-center justify-center py-20">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500"></div>
                </div>
            ) : filteredInventory.length > 0 ? (
                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6 gap-4">
                    {filteredInventory.map(item => (
                        <div
                            key={item.item_id}
                            onClick={() => toggleSelection(item.item_id)}
                            className={`group aspect-square bg-odin-blue/50 rounded-2xl border cursor-pointer relative overflow-hidden transition-all duration-200 ${selectedItems.includes(item.item_id) ? 'border-amber-500 ring-2 ring-amber-500/20 bg-amber-500/5' : 'border-white/5 hover:border-white/20 hover:bg-white/5'}`}
                        >
                            {/* Rarity Bar */}
                            <div className={`absolute top-0 left-0 w-full h-1`} style={{ backgroundColor: item.rarity_color || '#b0c3d9' }} />

                            <div className="absolute top-3 right-3 z-10 flex flex-col items-end gap-1">
                                {item.stattrak && <span className="text-[10px] uppercase font-bold bg-orange-600/90 text-white px-1.5 py-0.5 rounded shadow-sm backdrop-blur-sm">ST</span>}
                                {item.rarityName && <span className="text-[10px] px-1.5 py-0.5 rounded bg-black/60 text-white backdrop-blur-sm truncate max-w-[80px]">{item.rarityName}</span>}
                            </div>

                            <div className="w-full h-full p-4 flex items-center justify-center relative z-0">
                                <img
                                    src={`https://api.steamapis.com/image/item/730/${encodeURIComponent(item.item_name)}`}
                                    alt={item.item_name}
                                    className="max-w-full max-h-full object-contain filter drop-shadow-xl group-hover:scale-110 transition-transform duration-300"
                                    loading="lazy"
                                />
                            </div>

                            <div className="absolute bottom-0 left-0 w-full p-3 bg-gradient-to-t from-black/90 via-black/50 to-transparent pt-8">
                                <p className="text-xs text-center text-slate-200 font-medium truncate px-1">{item.item_name}</p>
                                {item.item_customname && <p className="text-[10px] text-center text-amber-400 truncate">"{item.item_customname}"</p>}
                            </div>
                        </div>
                    ))}
                </div>
            ) : (
                <div className="text-center py-20 text-slate-500">
                    <p className="text-lg">No items found matching your search.</p>
                </div>
            )}
        </div>
    );
};

export default RatatoskrInventory;
