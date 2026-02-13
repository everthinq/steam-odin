import React, { useState } from 'react';
import { ArrowLeft, Upload, CheckCircle, AlertCircle, FileStack, Loader2 } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';

const AddAccount = () => {
    const navigate = useNavigate();
    const [dragging, setDragging] = useState(false);
    const [loading, setLoading] = useState(false);

    const [results, setResults] = useState(null);
    const [pendingAccounts, setPendingAccounts] = useState([]);
    const [passwords, setPasswords] = useState({});
    const [passwordErrors, setPasswordErrors] = useState({});
    const [authNotice, setAuthNotice] = useState(null);
    const [showPasswordModal, setShowPasswordModal] = useState(false);
    const [importLoading, setImportLoading] = useState(false);

    const handleDragOver = (e) => {
        e.preventDefault();
        setDragging(true);
    };

    const handleDragLeave = () => {
        setDragging(false);
    };

    const processFiles = async (files) => {
        setLoading(true);
        setResults(null);

        const accounts = [];
        const errors = [];

        for (const file of files) {
            try {
                const text = await readFileAsText(file);
                const json = JSON.parse(text);

                // Preview-only SteamID extraction
                const steamid = text.match(/"SteamID":\s*(\d+)/)?.[1] || "Unknown";
                const accountName =
                    json.account_name ||
                    json?.Session?.AccountName ||
                    (steamid ? `SteamID: ${steamid}` : file.name);

                accounts.push({
                    fileName: file.name,
                    data: json,
                    accountName,
                    steamid
                });
            } catch (err) {
                errors.push(`${file.name}: ${err.message}`);
            }
        }

        setLoading(false);

        if (accounts.length === 0) {
            setResults({
                total: files.length,
                success: 0,
                failed: files.length,
                errors
            });
            return;
        }

        setPendingAccounts(accounts);
        setPasswords({});
        setShowPasswordModal(true);
    };

    const readFileAsText = (file) => {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => resolve(e.target.result);
            reader.onerror = () => reject(new Error('Failed to read file'));
            reader.readAsText(file);
        });
    };

    const importAccount = async (data) => {
        const res = await fetch('/api/accounts/import', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });

        const result = await res.json();
        if (!res.ok) throw new Error(result.error || 'Import failed');
        return result;
    };

    const handlePasswordSubmit = async (e) => {
        e.preventDefault();
        if (!pendingAccounts.length || importLoading) return;

        setImportLoading(true);
        setPasswordErrors({});
        setAuthNotice(null);

        try {
            let successCount = 0;
            let failCount = 0;
            const errors = (results && results.errors ? [...results.errors] : []);
            const fieldErrors = {};

            for (let i = 0; i < pendingAccounts.length; i++) {
                const account = pendingAccounts[i];
                const password = (passwords[i] || '').trim();

                if (!password) {
                    failCount++;
                    errors.push(`${account.fileName}: Password is required`);
                    continue;
                }

                let savedSteamid = null;
                try {
                    const payload = {
                        ...account.data,
                        fileName: account.fileName,
                        account_password: password
                    };

                    // 1. Import account (Saves and encrypts on backend)
                    const importRes = await importAccount(payload);

                    // FIXED: Always use the SteamID returned by the server
                    savedSteamid = importRes.steamid;

                    const username =
                        account.data?.account_name ||
                        account.data?.Session?.AccountName ||
                        null;

                    if (username) {
                        // 2. Authenticate using the specific SteamID confirmed by backend
                        const authRes = await fetch('/api/accounts/authenticate', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({
                                account_name: username,
                                password
                            })
                        });

                        const authJson = await authRes.json();
                        if (!authRes.ok || authJson.error) {
                            // If auth fails, remove the account to keep storage clean
                            if (savedSteamid) {
                                await fetch(`/api/accounts/${savedSteamid}`, { method: 'DELETE' });
                            }
                            throw new Error(authJson.error || 'Authentication failed');
                        }
                    }

                    successCount++;
                } catch (err) {
                    failCount++;
                    errors.push(`${account.fileName}: ${err.message}`);
                    fieldErrors[i] = err.message;
                }
            }

            setPasswordErrors(fieldErrors);

            if (failCount === 0) {
                setShowPasswordModal(false);
                setPendingAccounts([]);
                setPasswords({});
                setAuthNotice('All accounts imported and authenticated successfully.');
            } else {
                setAuthNotice('Some accounts failed. Please fix errors and try again.');
            }

            setResults({
                total: pendingAccounts.length,
                success: successCount,
                failed: failCount,
                errors
            });
        } catch (err) {
            console.error('Import process error:', err);
            setAuthNotice('An unexpected error occurred.');
        } finally {
            setImportLoading(false);
        }
    };

    const handlePasswordCancel = () => {
        setPendingAccounts([]);
        setPasswords({});
        setShowPasswordModal(false);
    };

    const handleFileSelect = (e) => {
        const files = Array.from(e.target.files);
        if (files.length > 0) processFiles(files);
    };

    const handleDrop = (e) => {
        e.preventDefault();
        setDragging(false);
        const files = Array.from(e.dataTransfer.files);
        if (files.length > 0) processFiles(files);
    };

    const handlePasswordChange = (index, value) => {
        setPasswords((prev) => ({ ...prev, [index]: value }));
    };

    const canSubmitPasswords =
        pendingAccounts.length > 0 &&
        pendingAccounts.every((_, index) => (passwords[index] || '').trim().length > 0);

    return (
        <div className="min-h-screen text-white p-8 flex flex-col items-center justify-center backdrop-blur-md bg-odin-dark/60 transition-all duration-500">
            <div className="w-full max-w-lg">
                <Link to="/" className="flex items-center gap-2 text-slate-400 hover:text-white mb-8 transition-colors">
                    <ArrowLeft size={20} />
                    Back to Dashboard
                </Link>

                <div className="glass-panel rounded-2xl p-8 bg-odin-blue/50 border border-white/10 backdrop-blur-md">
                    <h2 className="text-2xl font-bold mb-2 text-center text-asgard-gold">Import Steam Guard Files</h2>
                    <p className="text-frost-white/60 text-center mb-8 text-sm">
                        Upload your <code>.maFile</code>s to begin.
                        <br />
                        <span className="text-bifrost-cyan">Encryption is handled securely by Heimdall.</span>
                    </p>

                    {authNotice && (
                        <div className="mb-4 text-sm text-center">
                            <span className={authNotice.includes('failed') ? 'text-red-400' : 'text-bifrost-cyan'}>
                                {authNotice}
                            </span>
                        </div>
                    )}

                    {results && (
                        <div className={`mb-6 p-4 rounded-lg border ${results.failed === 0 ? 'bg-bifrost-cyan/10 border-bifrost-cyan/50' : 'bg-asgard-gold/10 border-asgard-gold/50'}`}>
                            <div className="flex items-center gap-2 font-bold mb-2">
                                {results.failed === 0 ? <CheckCircle className="text-bifrost-cyan" size={20} /> : <AlertCircle className="text-asgard-gold" size={20} />}
                                <span className="text-frost-white">Processed {results.total} files</span>
                            </div>
                            <div className="text-sm space-y-1 ml-7">
                                <p className="text-bifrost-cyan">Success: {results.success}</p>
                                {results.failed > 0 && <p className="text-red-400">Failed: {results.failed}</p>}
                            </div>
                            <button onClick={() => navigate('/')} className="mt-4 w-full bg-odin-blue hover:bg-odin-blue/80 text-frost-white py-2 rounded text-sm transition-colors border border-white/10">
                                Return to Dashboard
                            </button>
                        </div>
                    )}

                    {!results && (
                        <div
                            onDragOver={handleDragOver}
                            onDragLeave={handleDragLeave}
                            onDrop={handleDrop}
                            className={`border-2 border-dashed rounded-xl p-10 text-center transition-all cursor-pointer relative ${dragging ? 'border-bifrost-cyan bg-bifrost-cyan/10' : 'border-white/10 hover:border-white/30 bg-odin-dark/40'}`}
                        >
                            <input type="file" accept=".maFile,.json" multiple onChange={handleFileSelect} className="absolute inset-0 w-full h-full opacity-0 cursor-pointer" />
                            <div className="flex flex-col items-center">
                                <FileStack size={48} className={`mb-4 ${dragging ? 'text-bifrost-cyan' : 'text-frost-white/40'}`} />
                                <span className="text-lg font-medium mb-2 text-frost-white">{dragging ? 'Drop files here' : 'Click or drop files here'}</span>
                                <span className="text-sm text-frost-white/40">Supports multiple .maFile uploads</span>
                            </div>
                        </div>
                    )}
                </div>
            </div>

            {showPasswordModal && (
                <div className="fixed inset-0 bg-odin-dark/90 flex items-center justify-center z-50 backdrop-blur-sm">
                    <div className="bg-odin-blue/90 border border-white/10 rounded-2xl p-6 w-full max-w-lg shadow-2xl backdrop-blur-xl">
                        <h3 className="text-xl font-bold mb-4 text-center text-asgard-gold">Account Passwords Required</h3>
                        <form onSubmit={handlePasswordSubmit} className="space-y-4">
                            {pendingAccounts.map((account, index) => (
                                <div key={index} className="bg-odin-dark/50 p-4 rounded-xl border border-white/5">
                                    <p className="font-semibold text-sm mb-2 text-frost-white">{account.accountName}</p>
                                    <input
                                        type="password"
                                        className="w-full bg-odin-blue border border-white/10 rounded-lg px-3 py-2 text-sm focus:border-bifrost-cyan focus:outline-none text-white placeholder-white/30"
                                        placeholder="Steam Password"
                                        value={passwords[index] || ''}
                                        onChange={(e) => handlePasswordChange(index, e.target.value)}
                                        required
                                    />
                                    {passwordErrors[index] && <p className="mt-1 text-xs text-red-400">{passwordErrors[index]}</p>}
                                </div>
                            ))}
                            <div className="flex gap-3 justify-end">
                                <button type="button" onClick={handlePasswordCancel} className="px-4 py-2 bg-odin-blue hover:bg-odin-blue/80 rounded-lg text-sm text-frost-white border border-white/5">Cancel</button>
                                <button type="submit" disabled={!canSubmitPasswords || importLoading} className="px-4 py-2 bg-bifrost-cyan/20 hover:bg-bifrost-cyan/40 disabled:bg-odin-dark disabled:text-white/20 border border-bifrost-cyan/30 text-bifrost-cyan rounded-lg text-sm font-bold flex items-center gap-2 transition-all">
                                    {importLoading && <Loader2 className="animate-spin" size={16} />}
                                    {importLoading ? 'Importing...' : 'Complete Import'}
                                </button>
                            </div>
                        </form>
                    </div>
                </div>
            )}
        </div>
    );
};

export default AddAccount;