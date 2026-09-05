import { useState, useEffect, useCallback } from 'react';
import { Boxes, RefreshCw } from 'lucide-react';

// Inventory readiness for bulk-buying cheap cases: free Storage Unit slots and
// loose-inventory fill for the selected account. Needs a live Ratatoskr session
// (else it reports the error the service passes through). GET /readiness?steamid=.
const ReadinessPanel = ({ steamid, accountName }) => {
    const [data, setData] = useState(null);
    const [loading, setLoading] = useState(false);

    const load = useCallback(() => {
        if (!steamid) return Promise.resolve();
        return fetch(`/api/huginn/gjallarhorn/readiness?steamid=${steamid}`)
            .then((r) => (r.ok ? r.json() : null))
            .then((d) => setData(d))
            .catch(() => setData(null))
            .finally(() => setLoading(false));
    }, [steamid]);

    // Manual refresh (event handler — spinner state is fine to set here).
    const refresh = () => { setLoading(true); load(); };

    useEffect(() => { load(); }, [load]);

    if (!steamid) {
        return (
            <div className="rounded-xl border border-white/10 bg-odin-blue/30 px-4 py-3 text-xs text-slate-500">
                Pick an account to check inventory space.
            </div>
        );
    }

    const err = data && data.error;
    const looseFree = data && data.looseItems != null ? data.looseCapacity - data.looseItems : null;

    return (
        <div className="rounded-xl border border-white/10 bg-odin-blue/30 px-4 py-3">
            <div className="flex items-center gap-2 mb-2">
                <Boxes size={14} className="text-amber-500/70" />
                <span className="text-xs font-semibold text-slate-300">Space · {accountName || steamid}</span>
                <button type="button" onClick={refresh} className="ml-auto text-slate-500 hover:text-slate-300" title="Refresh">
                    <RefreshCw size={12} className={loading ? 'animate-spin' : ''} />
                </button>
            </div>
            {err ? (
                <p className="text-xs text-amber-400/80">{err} — connect the account first (Confirmations → Connect).</p>
            ) : !data ? (
                <p className="text-xs text-slate-500">{loading ? 'Loading…' : '—'}</p>
            ) : (
                <div className="flex flex-wrap gap-x-6 gap-y-1 text-xs">
                    <div>
                        <span className="text-slate-500">Storage free </span>
                        <span className="tabular-nums text-emerald-400 font-semibold">{data.storageFree.toLocaleString()}</span>
                        <span className="text-slate-600"> across {data.storageUnits.length} unit{data.storageUnits.length === 1 ? '' : 's'}</span>
                    </div>
                    <div>
                        <span className="text-slate-500">Loose inventory </span>
                        <span className="tabular-nums text-slate-300">{data.looseItems == null ? '—' : `${data.looseItems.toLocaleString()} / ${data.looseCapacity.toLocaleString()}`}</span>
                        {looseFree != null && <span className="text-slate-600"> ({looseFree.toLocaleString()} free)</span>}
                    </div>
                </div>
            )}
        </div>
    );
};

export default ReadinessPanel;
