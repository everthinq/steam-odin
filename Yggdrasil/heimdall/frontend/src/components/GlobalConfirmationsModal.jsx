import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Check, Save, Play, Loader2 } from 'lucide-react';
import NorseStepper from './NorseStepper';
import { readJsonOrThrow, failureMessage } from '../utils/readJson';
import { postCommand, followJob } from '../utils/moveCommands';

// Every rule lives in the backend (confirmation_settings.py): the interval limits, what a
// value outside them gets (refused with its reason), and the note on what the loop does.
const GlobalConfirmationsModal = ({ isOpen, onClose }) => {
    const [view, setView] = useState(null);      // {settings, limits, polling_note} from the backend
    const [settings, setSettings] = useState(null);
    const [loading, setLoading] = useState(false);
    const [checking, setChecking] = useState(false);
    const [progress, setProgress] = useState(null);
    const [message, setMessage] = useState(null);
    const openRef = useRef(isOpen);

    const showView = useCallback((data) => {
        setView(data);
        setSettings(data.settings);
    }, []);

    const fetchSettings = useCallback(async () => {
        setLoading(true);
        setView(null);
        setMessage(null);
        try {
            showView(await readJsonOrThrow(await fetch('/api/confirmations/settings')));
        } catch (error) {
            setMessage({ type: 'error', text: failureMessage(error) || 'Failed to load settings' });
        } finally {
            setLoading(false);
        }
    }, [showView]);

    useEffect(() => {
        openRef.current = isOpen;
        if (isOpen) {
            fetchSettings();
        }
    }, [isOpen, fetchSettings]);

    const handleSave = async () => {
        if (loading || !view) return;
        setLoading(true);
        try {
            showView(await postCommand('/api/confirmations/settings', settings));
            setMessage({ type: 'success', text: 'Settings saved.' });
            setTimeout(() => setMessage(null), 3000);
        } catch (error) {
            setMessage({ type: 'error', text: failureMessage(error) });
        } finally {
            setLoading(false);
        }
    };

    const handleCheckNow = async () => {
        if (checking) return;
        setChecking(true);
        setMessage(null);
        try {
            const { job } = await postCommand('/api/confirmations/check-all');
            const finished = await followJob(job.id, {
                onProgress: setProgress,
                isActive: () => openRef.current,
                lostMessage: 'The backend restarted during the check, so its result is unknown.',
                lostTrackMessage: (reason) => `Lost track of the check (${reason}). It keeps running in the backend.`,
            });
            setMessage(finished.error
                ? { type: 'error', text: `The check failed: ${finished.error}` }
                : { type: 'success', text: finished.result.message });
        } catch (error) {
            if (!error.cancelled) setMessage({ type: 'error', text: failureMessage(error) });
        } finally {
            setChecking(false);
            setProgress(null);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-odin-dark/80 backdrop-blur-sm">
            <div className="bg-odin-blue/90 border border-white/10 rounded-2xl w-full max-w-lg shadow-2xl relative overflow-hidden backdrop-blur-xl">
                {/* Header */}
                <div className="p-6 border-b border-white/5 flex justify-between items-center bg-odin-blue/50">
                    <div>
                        <h2 className="text-xl font-bold text-asgard-gold tracking-wide">Global Confirmations</h2>
                        <p className="text-frost-white/60 text-xs mt-1">Manage automation for all accounts</p>
                    </div>
                    <button onClick={onClose} className="text-frost-white/40 hover:text-frost-white transition-colors">
                        <X size={24} />
                    </button>
                </div>

                {/* Body */}
                <div className="p-6 space-y-6">
                    {!view ? (
                        <div className="flex flex-col items-center gap-4 p-8">
                            {loading ? (
                                <Loader2 className="animate-spin text-asgard-gold" size={32} />
                            ) : (
                                <>
                                    <p className="text-sm text-red-400 text-center">{message?.text || 'Settings are not loaded.'}</p>
                                    <button
                                        onClick={fetchSettings}
                                        className="px-4 py-2 bg-odin-blue hover:bg-odin-blue/80 text-frost-white/80 rounded-lg text-sm border border-white/10"
                                    >
                                        Retry
                                    </button>
                                </>
                            )}
                        </div>
                    ) : (
                        <>
                            {/* Auto-Check Section */}
                            <div className="space-y-4">
                                <div className="flex items-center justify-between p-4 bg-odin-dark/30 rounded-xl border border-white/5">
                                    <div>
                                        <h3 className="text-frost-white font-medium">Automatic Checking</h3>
                                        <p className="text-xs text-frost-white/50">Periodically check for new confirmations</p>
                                    </div>
                                    <label className="relative inline-flex items-center cursor-pointer">
                                        <input
                                            type="checkbox"
                                            className="sr-only peer"
                                            checked={settings.auto_check_enabled}
                                            onChange={(e) => setSettings({ ...settings, auto_check_enabled: e.target.checked })}
                                        />
                                        <div className="w-11 h-6 bg-odin-blue border border-white/10 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-gray-300 after:border after:rounded-full after:h-5 after:w-5 after:transition-all peer-checked:bg-bifrost-cyan peer-checked:border-bifrost-cyan"></div>
                                    </label>
                                </div>

                                <NorseStepper
                                    label="Check Interval (seconds)"
                                    value={settings.check_interval}
                                    onChange={(val) => setSettings({ ...settings, check_interval: val })}
                                    min={view.limits.check_interval.min}
                                    max={view.limits.check_interval.max}
                                    step={view.limits.check_interval.step}
                                />
                                <p className="text-xs text-slate-400 px-1">{view.polling_note}</p>
                            </div>

                            <hr className="border-white/5" />

                            {/* Auto-Confirm Section */}
                            <div className="space-y-3">
                                <h3 className="text-sm font-semibold text-asgard-gold uppercase tracking-wider mb-2">Auto-Confirmation Rules</h3>

                                <label className="flex items-center gap-3 p-3 hover:bg-white/5 rounded-lg cursor-pointer transition-colors group">
                                    <div className={`w-5 h-5 rounded border flex items-center justify-center transition-colors ${settings.auto_confirm_market ? 'bg-bifrost-purple border-bifrost-purple' : 'border-white/20 group-hover:border-white/40'}`}>
                                        {settings.auto_confirm_market && <Check size={14} className="text-white" />}
                                    </div>
                                    <input
                                        type="checkbox"
                                        className="hidden"
                                        checked={settings.auto_confirm_market}
                                        onChange={(e) => setSettings({ ...settings, auto_confirm_market: e.target.checked })}
                                    />
                                    <span className="text-sm text-frost-white/80 group-hover:text-frost-white">Auto-confirm Market Listings</span>
                                </label>

                                <label className="flex items-center gap-3 p-3 hover:bg-white/5 rounded-lg cursor-pointer transition-colors group">
                                    <div className={`w-5 h-5 rounded border flex items-center justify-center transition-colors ${settings.auto_confirm_trades ? 'bg-bifrost-purple border-bifrost-purple' : 'border-white/20 group-hover:border-white/40'}`}>
                                        {settings.auto_confirm_trades && <Check size={14} className="text-white" />}
                                    </div>
                                    <input
                                        type="checkbox"
                                        className="hidden"
                                        checked={settings.auto_confirm_trades}
                                        onChange={(e) => setSettings({ ...settings, auto_confirm_trades: e.target.checked })}
                                    />
                                    <span className="text-sm text-frost-white/80 group-hover:text-frost-white">Auto-confirm Trades</span>
                                </label>

                            </div>

                            {/* Actions */}
                            <div className="pt-4 flex flex-col sm:flex-row items-center justify-between gap-4">
                                <button
                                    onClick={handleCheckNow}
                                    disabled={checking}
                                    className="w-full sm:w-auto flex justify-center items-center gap-2 px-4 py-2 bg-odin-blue hover:bg-odin-blue/80 text-frost-white/70 hover:text-frost-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 border border-white/5"
                                >
                                    {checking ? <Loader2 size={16} className="animate-spin" /> : <Play size={16} />}
                                    {checking && progress?.total
                                        ? `Checking ${(progress.done || 0) + (progress.failed || 0)} of ${progress.total}…`
                                        : 'Check All Now'}
                                </button>

                                <button
                                    onClick={handleSave}
                                    disabled={loading}
                                    className="w-full sm:w-auto flex justify-center items-center gap-2 px-6 py-2 bg-gradient-to-r from-bifrost-purple to-bifrost-cyan hover:from-bifrost-purple/80 hover:to-bifrost-cyan/80 text-odin-dark rounded-lg text-sm font-bold shadow-lg shadow-bifrost-cyan/20 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
                                >
                                    {loading ? <Loader2 size={16} className="animate-spin" /> : <Save size={16} />}
                                    {loading ? 'Saving…' : 'Save Settings'}
                                </button>
                            </div>

                            {message && (
                                <div className={`text-center text-xs p-2 rounded ${message.type === 'success' ? 'text-green-400 bg-green-500/10' : 'text-red-400 bg-red-500/10'}`}>
                                    {message.text}
                                </div>
                            )}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default GlobalConfirmationsModal;
