import React, { useState, useEffect, useCallback } from 'react';
import { useOutletContext } from 'react-router-dom';
import {
    ShoppingBag,
    RefreshCw,
    Wallet,
    Database,
    AlertTriangle,
    Info,
} from 'lucide-react';
import storageUnitImage from '../../assets/ratatoskr/storage-unit.png';
import { getItemImageUrl } from '../../utils/ratatoskrImages';

const STORAGE_DEF_ID = 1201;

const FALLBACK_CATALOG = [
    {
        item_def_id: STORAGE_DEF_ID,
        name: 'Storage Unit',
        description: 'Store up to 1,000 items from your CS2 inventory.',
        image: 'econ/tools/casket',
        max_quantity: 5,
        category: 'tools',
        price_label: 'kr 18.50 (NOK)',
        available: false,
    },
];

const RatatoskrStore = () => {
    const { steamid } = useOutletContext();

    const [loading, setLoading] = useState(true);
    const [purchasingId, setPurchasingId] = useState(null);
    const [purchaseStatus, setPurchaseStatus] = useState(null);
    const [store, setStore] = useState(null);
    const [error, setError] = useState(null);
    const [success, setSuccess] = useState(null);
    const [quantities, setQuantities] = useState({});
    const [showPriceList, setShowPriceList] = useState(false);
    const [priceFilter, setPriceFilter] = useState('');

    const catalog = store?.catalog ?? [];
    const priceList = store?.price_list ?? [];
    const filteredPriceList = priceFilter.trim()
        ? priceList.filter((row) => {
              const q = priceFilter.trim().toLowerCase();
              return (
                  String(row.item_def_id).includes(q) ||
                  (row.name && row.name.toLowerCase().includes(q)) ||
                  (row.price_label && row.price_label.toLowerCase().includes(q))
              );
          })
        : priceList;

    const fetchStore = useCallback(async ({ quiet = false } = {}) => {
        if (!quiet) {
            setLoading(true);
            setError(null);
        }
        try {
            const res = await fetch(`/api/ratatoskr/store/${steamid}`);
            const data = await res.json();
            if (data.error && !data.catalog?.length) {
                throw new Error(data.error || 'Failed to load store');
            }
            setStore(data);
            if (data.error && !quiet) setError(data.error);
        } catch (err) {
            if (!quiet) {
                setError(err.message);
                setStore({
                    catalog: FALLBACK_CATALOG,
                    gc_connected: false,
                    warning: err.message,
                });
            }
        } finally {
            if (!quiet) setLoading(false);
        }
    }, [steamid]);

    useEffect(() => {
        fetchStore();
    }, [fetchStore]);

    const getQty = (itemDefId) => quantities[itemDefId] ?? 1;

    const setQty = (itemDefId, value) => {
        setQuantities((prev) => ({ ...prev, [itemDefId]: value }));
    };

    const finishPurchase = async (auth) => {
        if (!auth?.txn_id) return;

        const res = await fetch('/api/ratatoskr/store/purchase/finish', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                steamID: steamid,
                txnId: auth.txn_id,
            }),
        });
        const data = await res.json();
        if (!res.ok) {
            throw new Error(data.error || 'Could not complete purchase');
        }
        setSuccess(data.message || `Purchased ${auth.item_name}`);
        setPurchaseStatus(null);
        await fetchStore({ quiet: true });
    };

    const handleGcPurchase = async (item) => {
        const qty = getQty(item.item_def_id);
        const priceHint = item.price_label || 'kr 18.50 (estimated)';
        const confirmed = window.confirm(
            `Buy ${qty}× ${item.name} with Steam Wallet?\nPrice: ${priceHint} each`
        );
        if (!confirmed) return;

        setPurchasingId(item.item_def_id);
        setError(null);
        setSuccess(null);
        setPurchaseStatus('Starting purchase…');

        try {
            const beginRes = await fetch('/api/ratatoskr/store/purchase/begin', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    steamID: steamid,
                    itemDefId: item.item_def_id,
                    quantity: qty,
                }),
            });
            const beginData = await beginRes.json();

            if (!beginRes.ok) {
                throw new Error(beginData.error || beginData.message || 'Purchase init failed');
            }

            setPurchaseStatus('Charging Steam Wallet via Ratatoskr…');

            const auth = {
                txn_id: beginData.txn_id,
                item_name: beginData.item_name || item.name,
                item_def_id: item.item_def_id,
            };
            await finishPurchase(auth);
        } catch (err) {
            setError(err.message);
            setPurchaseStatus(null);
        } finally {
            setPurchasingId(null);
        }
    };

    const itemImage = (item) => {
        if (item.item_def_id === STORAGE_DEF_ID) return storageUnitImage;
        return getItemImageUrl({ item_url: item.image });
    };

    const storageItem = catalog.find((i) => i.item_def_id === STORAGE_DEF_ID) ?? catalog[0];
    const gcMode = store?.gc_session_mode || 'headful';

    const renderItem = (item) => {
        const qty = getQty(item.item_def_id);
        const maxQty = item.max_quantity || 20;
        const busy = purchasingId === item.item_def_id;

        return (
            <article
                key={item.item_def_id}
                className="rounded-2xl border border-white/10 bg-black/30 overflow-hidden"
            >
                <div className="p-5 flex flex-col sm:flex-row gap-5">
                    <div className="w-full sm:w-28 h-28 rounded-xl bg-gradient-to-b from-slate-800/80 to-slate-900 flex items-center justify-center shrink-0 border border-white/5">
                        <img
                            src={itemImage(item)}
                            alt={item.name}
                            className="w-24 h-24 object-contain drop-shadow-lg"
                        />
                    </div>
                    <div className="flex-1 min-w-0">
                        <h2 className="text-lg font-bold text-white mb-1">{item.name}</h2>
                        <p className="text-xs text-slate-500 mb-2">
                            def_index {item.item_def_id}
                            {item.price_source ? ` · price: ${item.price_source}` : ''}
                        </p>
                        <p className="text-sm text-slate-400 mb-3 leading-relaxed">{item.description}</p>
                        <p className="text-xl font-bold text-amber-400 mb-4">
                            {item.price_label ?? 'See Steam checkout'}
                        </p>

                        <div className="flex flex-wrap items-center gap-2">
                            <label className="text-sm text-slate-400 flex items-center gap-2">
                                Qty
                                <select
                                    value={qty}
                                    onChange={(e) => setQty(item.item_def_id, Number(e.target.value))}
                                    className="bg-black/40 border border-white/10 rounded-lg px-2 py-1.5 text-white text-sm"
                                    disabled={busy}
                                >
                                    {Array.from({ length: maxQty }, (_, i) => i + 1).map((n) => (
                                        <option key={n} value={n}>
                                            {n}
                                        </option>
                                    ))}
                                </select>
                            </label>

                            <button
                                type="button"
                                onClick={() => handleGcPurchase(item)}
                                disabled={!store?.gc_connected || !item.available || busy}
                                title={
                                    !store?.gc_connected
                                        ? 'Connect to GC first'
                                        : !item.available
                                          ? 'Waiting for GC price'
                                          : 'Buy with Steam Wallet'
                                }
                                className="inline-flex items-center gap-2 px-4 py-2 rounded-xl text-sm font-semibold bg-gradient-to-r from-amber-600 to-amber-700 hover:from-amber-500 hover:to-amber-600 text-white disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                                {busy ? (
                                    <div className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
                                ) : (
                                    <ShoppingBag size={15} />
                                )}
                                Buy with Wallet
                            </button>
                        </div>
                    </div>
                </div>
            </article>
        );
    };

    return (
        <div className="animate-in fade-in duration-500 max-w-5xl">
            <div className="flex flex-col md:flex-row justify-between items-start md:items-center gap-4 mb-6">
                <div>
                    <h1 className="text-3xl font-bold text-white font-serif mb-1">Store</h1>
                    <p className="text-slate-400 text-sm max-w-lg">
                        Buy a Storage Unit (def_index 1201) with Steam Wallet via GC + checkout approval.
                    </p>
                </div>
                <button
                    type="button"
                    onClick={() => fetchStore()}
                    disabled={loading}
                    className="p-2.5 bg-white/5 hover:bg-white/10 rounded-xl text-slate-400 hover:text-white transition-colors border border-white/5"
                >
                    <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
                </button>
            </div>

            {store?.gc_connected && (
                <div className="mb-4 flex flex-wrap items-center gap-3 text-xs text-slate-400">
                    <span className="text-emerald-400/90">GC connected · mode {gcMode}</span>
                    {store.storage_unit?.price_label && (
                        <span>
                            Storage Unit:{' '}
                            <strong className="text-amber-400/90">{store.storage_unit.price_label}</strong>
                            {store.storage_unit.price_source
                                ? ` (${store.storage_unit.price_source})`
                                : ''}
                        </span>
                    )}
                    {priceList.length > 0 && (
                        <button
                            type="button"
                            onClick={() => setShowPriceList((v) => !v)}
                            className="font-medium text-amber-400 hover:text-amber-300 underline-offset-2 hover:underline"
                        >
                            {showPriceList ? 'Hide sheet debug' : 'Show sheet debug'}
                        </button>
                    )}
                </div>
            )}

            {store?.warning && (
                <div className="mb-4 p-4 rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-100 text-sm flex gap-2">
                    <Info size={18} className="shrink-0 mt-0.5" />
                    <p>{store.warning}</p>
                </div>
            )}

            {store?.wallet && (
                <div className="flex items-center gap-3 mb-4 p-4 rounded-xl border border-emerald-500/20 bg-emerald-500/5">
                    <Wallet className="text-emerald-400 shrink-0" size={20} />
                    <div>
                        <p className="text-xs text-slate-400 uppercase tracking-wide">Steam Wallet</p>
                        <p className="text-lg font-semibold text-emerald-100">{store.wallet.balance_label}</p>
                    </div>
                    <span className="ml-auto text-xs text-slate-500">{store.currency_label}</span>
                </div>
            )}

            {typeof store?.storage_units_owned === 'number' && (
                <div className="flex items-center gap-2 mb-6 text-sm text-slate-400">
                    <Database size={16} className="text-amber-500" />
                    <span>
                        You own <strong className="text-white">{store.storage_units_owned}</strong> storage unit
                        {store.storage_units_owned === 1 ? '' : 's'}.
                    </span>
                </div>
            )}

            {error && (
                <div className="mb-4 p-4 rounded-xl border border-red-500/30 bg-red-500/10 text-red-200 text-sm flex gap-2">
                    <AlertTriangle size={18} className="shrink-0 mt-0.5" />
                    <p>{error}</p>
                </div>
            )}

            {purchaseStatus && (
                <div className="mb-4 p-4 rounded-xl border border-sky-500/40 bg-sky-500/10 text-sky-100 text-sm flex gap-3 items-center">
                    <div className="w-5 h-5 border-2 border-sky-300/40 border-t-sky-200 rounded-full animate-spin shrink-0" />
                    <p>{purchaseStatus}</p>
                </div>
            )}

            {success && (
                <div className="mb-4 p-4 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-emerald-200 text-sm">
                    {success}
                </div>
            )}

            {loading ? (
                <div className="flex justify-center py-20">
                    <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-amber-500" />
                </div>
            ) : storageItem ? (
                <section>
                    <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider mb-3">
                        Storage Unit
                    </h2>
                    {renderItem(storageItem)}
                </section>
            ) : (
                <p className="text-slate-500 text-center py-12">
                    Could not load store catalog. Is Ratatoskr running?
                </p>
            )}

            {showPriceList && priceList.length > 0 && (
                <section className="mt-8">
                    <div className="flex flex-col sm:flex-row sm:items-center gap-3 mb-3">
                        <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wider">
                            GC sheet debug ({priceList.length} explicit rows)
                        </h2>
                        <input
                            type="search"
                            value={priceFilter}
                            onChange={(e) => setPriceFilter(e.target.value)}
                            placeholder="Filter by id, name, or price…"
                            className="sm:ml-auto w-full sm:w-64 bg-black/40 border border-white/10 rounded-lg px-3 py-2 text-sm text-white placeholder:text-slate-600"
                        />
                    </div>
                    <div className="rounded-xl border border-white/10 overflow-hidden max-h-[28rem] overflow-y-auto">
                        <table className="w-full text-sm text-left">
                            <thead className="sticky top-0 bg-slate-900/95 text-slate-400 text-xs uppercase">
                                <tr>
                                    <th className="px-3 py-2 font-medium">def_id</th>
                                    <th className="px-3 py-2 font-medium">Name</th>
                                    <th className="px-3 py-2 font-medium text-right">Price</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-white/5">
                                {filteredPriceList.map((row) => (
                                    <tr
                                        key={row.item_def_id}
                                        className="hover:bg-white/5 text-slate-300"
                                    >
                                        <td className="px-3 py-2 font-mono text-slate-500">
                                            {row.item_def_id}
                                        </td>
                                        <td className="px-3 py-2">{row.name}</td>
                                        <td className="px-3 py-2 text-right text-amber-400/90 whitespace-nowrap">
                                            {row.price_label}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                        {filteredPriceList.length === 0 && (
                            <p className="p-4 text-center text-slate-500 text-sm">No matches.</p>
                        )}
                    </div>
                    <p className="mt-2 text-xs text-slate-600">
                        Only rows with an explicit item_def_id in the sheet are shown. The old 192-row list was
                        mostly mis-parsed currency buckets.
                    </p>
                </section>
            )}

            <p className="mt-8 text-xs text-slate-600 leading-relaxed">
                Same flow as SkinLedger: GC <code className="text-slate-500">StorePurchaseInit</code> → Steam
                checkout with <strong className="text-slate-500 font-normal">Authorize</strong> → finalize on
                Game Coordinator. Storage Unit is <strong className="text-slate-500 font-normal">kr 18.50</strong>{' '}
                (1850 øre) when the GC price sheet is unavailable.
            </p>
        </div>
    );
};

export default RatatoskrStore;
