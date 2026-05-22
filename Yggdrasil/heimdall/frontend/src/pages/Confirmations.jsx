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

    const loadConfirmations = useCallback(async ({ preserveMessages = false } = {}) => {
        setLoading(true);
        if (!preserveMessages) {
            setError(null);
            setInfoMessage(null);
        }
        try {
            const res = await fetch(`/api/accounts/${steamid}/confirmations`);
            const data = await res.json();

            if (!res.ok) {
                setConfirmations([]);
                throw new Error(data.error || data.message || 'Failed to load confirmations');
            }
            setConfirmations(data.confirmations || []);
            if ((data.confirmations || []).length === 0) {
                setInfoMessage('No pending confirmations on Steam.');
            }
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
                    ck: conf.nonce || conf.key,
                    op
                })
            });
            const data = await res.json();

            if (!res.ok) {
                throw new Error(data.error || data.message || 'Failed to update confirmation');
            }
            setInfoMessage(op === 'allow' ? 'Confirmation approved.' : 'Confirmation denied.');
            setConfirmations(prev => prev.filter(c => c.id !== conf.id));
            try {
                await loadConfirmations({ preserveMessages: true });
            } catch {
                // Action succeeded on Steam; do not replace success with a list-fetch error.
            }
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
        <div className="min-h-screen text-white p-4 md:p-8 flex flex-col items-center">
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

                <div className="glass-panel rounded-2xl p-6 mb-4 border border-white/5 bg-odin-blue/40">
                    <h2 className="text-xl md:text-2xl font-bold mb-2 text-asgard-gold">Pending Confirmations</h2>
                    <p className="text-frost-white/60 text-sm">
                        These are trade and market confirmations currently pending for account{' '}
                        <span className="font-mono text-bifrost-cyan break-all">{steamid}</span>. Approving a confirmation
                        will allow the trade or listing; denying will cancel it.
                    </p>
                </div>

                {error && (
                    <div className="mb-4 bg-red-500/10 border border-red-500/30 text-red-300 px-4 py-3 rounded-lg flex items-center gap-2 backdrop-blur-md">
                        <AlertCircle size={18} />
                        <span className="text-sm">{error}</span>
                    </div>
                )}

                {infoMessage && (
                    <div className="mb-4 bg-bifrost-cyan/10 border border-bifrost-cyan/30 text-bifrost-cyan px-4 py-3 rounded-lg flex items-center gap-2 backdrop-blur-md">
                        <CheckCircle2 size={18} />
                        <span className="text-sm">{infoMessage}</span>
                    </div>
                )}

                {loading ? (
                    <div className="flex justify-center items-center h-48">
                        <RefreshCw className="animate-spin text-asgard-gold" size={32} />
                    </div>
                ) : error ? null : confirmations.length === 0 ? (
                    <div className="glass-panel rounded-2xl p-10 text-center border border-white/5 bg-odin-blue/20">
                        <p className="text-lg font-semibold mb-2 text-frost-white">No pending confirmations</p>
                        <p className="text-frost-white/40 text-sm">
                            There are currently no trade or market confirmations waiting for this account.
                            If you use Global Settings → auto-confirm trades, the scheduler may have already approved
                            this trade even when this page could not load the list.
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
                                    className="glass-card rounded-xl p-4 border border-white/10 flex flex-col gap-3 bg-odin-blue/40 hover:bg-odin-blue/60 transition-colors"
                                >
                                    <div className="flex flex-col md:flex-row justify-between items-start gap-2 md:gap-4">
                                        <div className="flex-1">
                                            <p className="text-sm uppercase tracking-wide text-asgard-gold/80 font-bold">
                                                {typeLabel}
                                            </p>
                                            <h3 className="text-base md:text-lg font-semibold text-frost-white break-words">
                                                {headline}
                                            </h3>
                                            {summary.length > 0 && (
                                                <ul className="mt-2 text-xs text-frost-white/60 space-y-1">
                                                    {summary.slice(0, 3).map((line, idx) => (
                                                        <li key={idx} className="break-words">{line}</li>
                                                    ))}
                                                </ul>
                                            )}
                                        </div>
                                        <div className="text-left md:text-right text-[10px] text-frost-white/30 font-mono w-full md:w-auto">
                                            <div>CID: {conf.id}</div>
                                            <div>CK: {conf.nonce || conf.key}</div>
                                        </div>
                                    </div>
                                    <div className="flex justify-end gap-3">
                                        <button
                                            type="button"
                                            onClick={() => handleAction(conf, 'allow')}
                                            disabled={actingId !== null}
                                            className="px-4 py-2 rounded-lg text-xs font-semibold bg-bifrost-cyan/20 hover:bg-bifrost-cyan/40 text-bifrost-cyan border border-bifrost-cyan/30 disabled:opacity-60 disabled:cursor-not-allowed transition-all"
                                        >
                                            Approve
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => handleAction(conf, 'cancel')}
                                            disabled={actingId !== null}
                                            className="px-4 py-2 rounded-lg text-xs font-semibold bg-odin-dark/40 hover:bg-odin-dark/60 text-frost-white/60 border border-white/10 disabled:opacity-60 disabled:cursor-not-allowed transition-all"
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

