import React, { useEffect, useState, useCallback } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { ArrowLeft, RefreshCw, CheckCircle2, XCircle, AlertCircle } from 'lucide-react';

const typeLabels = {
    1: 'Test',
    2: 'Trade',
    3: 'Market Listing',
    4: 'Feature Opt-Out',
    5: 'Phone Number Change',
    6: 'Account Recovery'
};

const Confirmations = () => {
    const { steamid } = useParams();
    const navigate = useNavigate();

    const [loading, setLoading] = useState(true);
    const [confirmations, setConfirmations] = useState([]);
    const [error, setError] = useState(null);
    const [actingId, setActingId] = useState(null);
    const [infoMessage, setInfoMessage] = useState(null);

    const loadConfirmations = useCallback(async () => {
        setLoading(true);
        setError(null);
        setInfoMessage(null);
        try {
            const res = await fetch(`/api/accounts/${steamid}/confirmations`);
            const data = await res.json();
            
            if (!res.ok) {
                throw new Error(data.error || data.message || 'Failed to load confirmations');
            }
            setConfirmations(data.confirmations || []);
        } catch (err) {
            // Handle JSON parse errors gracefully
            if (err instanceof SyntaxError && err.message.includes('JSON')) {
                setError('Server returned invalid response. Please check your authentication.');
            } else {
                setError(err.message);
            }
        } finally {
            setLoading(false);
        }
    }, [steamid]);

    useEffect(() => {
        loadConfirmations();
        // We do not auto-poll confirmations; user can refresh manually.
    }, [loadConfirmations]);

    const handleAction = async (conf, op) => {
        setError(null);
        setInfoMessage(null);
        setActingId(`${conf.id}-${op}`);
        try {
            const res = await fetch(`/api/accounts/${steamid}/confirmations/${conf.id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    ck: conf.nonce,
                    op
                })
            });
            const data = await res.json();
            
            if (!res.ok) {
                throw new Error(data.error || data.message || 'Failed to update confirmation');
            }
            setInfoMessage(op === 'allow' ? 'Confirmation approved.' : 'Confirmation denied.');
            // Remove this confirmation from the list
            setConfirmations(prev => prev.filter(c => c.id !== conf.id));
        } catch (err) {
            // Handle JSON parse errors gracefully
            if (err instanceof SyntaxError && err.message.includes('JSON')) {
                setError('Server returned invalid response. Please check your authentication.');
            } else {
                setError(err.message);
            }
        } finally {
            setActingId(null);
        }
    };

    return (
        <div className="min-h-screen bg-slate-950 text-white p-8 flex flex-col items-center">
            <div className="w-full max-w-4xl">
                <div className="flex items-center justify-between mb-6">
                    <button
                        onClick={() => navigate(-1)}
                        className="flex items-center gap-2 text-slate-400 hover:text-white transition-colors"
                    >
                        <ArrowLeft size={20} />
                        Back
                    </button>
                    <button
                        onClick={loadConfirmations}
                        className="flex items-center gap-2 text-slate-300 hover:text-white transition-colors"
                    >
                        <RefreshCw size={18} className={loading ? 'animate-spin' : ''} />
                        Refresh
                    </button>
                </div>

                <div className="glass-panel rounded-2xl p-6 mb-4">
                    <h2 className="text-2xl font-bold mb-2">Pending Confirmations</h2>
                    <p className="text-slate-400 text-sm">
                        These are trade and market confirmations currently pending for account{' '}
                        <span className="font-mono text-slate-200">{steamid}</span>. Approving a confirmation
                        will allow the trade or listing; denying will cancel it.
                    </p>
                </div>

                {error && (
                    <div className="mb-4 bg-red-500/10 border border-red-500/50 text-red-400 px-4 py-3 rounded-lg flex items-center gap-2">
                        <AlertCircle size={18} />
                        <span className="text-sm">{error}</span>
                    </div>
                )}

                {infoMessage && (
                    <div className="mb-4 bg-emerald-500/10 border border-emerald-500/50 text-emerald-400 px-4 py-3 rounded-lg flex items-center gap-2">
                        <CheckCircle2 size={18} />
                        <span className="text-sm">{infoMessage}</span>
                    </div>
                )}

                {loading ? (
                    <div className="flex justify-center items-center h-48">
                        <RefreshCw className="animate-spin text-blue-500" size={32} />
                    </div>
                ) : confirmations.length === 0 ? (
                    <div className="glass-panel rounded-2xl p-10 text-center">
                        <p className="text-lg font-semibold mb-2">No pending confirmations</p>
                        <p className="text-slate-400 text-sm">
                            There are currently no trade or market confirmations waiting for this account.
                        </p>
                    </div>
                ) : (
                    <div className="space-y-4">
                        {confirmations.map((conf) => {
                            const typeLabel = typeLabels[conf.type] || 'Unknown';
                            const headline = conf.headline || 'Confirmation';
                            const summary = conf.summary || [];
                            return (
                                <div
                                    key={conf.id}
                                    className="glass-card rounded-xl p-4 border border-slate-700 flex flex-col gap-3"
                                >
                                    <div className="flex justify-between items-start gap-4">
                                        <div>
                                            <p className="text-sm uppercase tracking-wide text-slate-400">
                                                {typeLabel}
                                            </p>
                                            <h3 className="text-lg font-semibold text-white">
                                                {headline}
                                            </h3>
                                            {summary.length > 0 && (
                                                <ul className="mt-2 text-xs text-slate-400 space-y-1">
                                                    {summary.slice(0, 3).map((line, idx) => (
                                                        <li key={idx}>{line}</li>
                                                    ))}
                                                </ul>
                                            )}
                                        </div>
                                        <div className="text-right text-[10px] text-slate-500 font-mono">
                                            <div>CID: {conf.id}</div>
                                            <div>CK: {conf.nonce}</div>
                                        </div>
                                    </div>
                                    <div className="flex justify-end gap-3">
                                        <button
                                            type="button"
                                            onClick={() => handleAction(conf, 'allow')}
                                            disabled={actingId !== null}
                                            className="px-4 py-2 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white disabled:opacity-60 disabled:cursor-not-allowed"
                                        >
                                            Approve
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => handleAction(conf, 'cancel')}
                                            disabled={actingId !== null}
                                            className="px-4 py-2 rounded-lg text-xs font-semibold bg-slate-800 hover:bg-slate-700 text-slate-200 disabled:opacity-60 disabled:cursor-not-allowed"
                                        >
                                            Deny
                                        </button>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}

                <div className="mt-6 text-xs text-slate-500 text-center">
                    Powered by Steam Heimdall • Confirmations are fetched securely from Steam using your encrypted maFile.
                </div>
            </div>
        </div>
    );
};

export default Confirmations;

