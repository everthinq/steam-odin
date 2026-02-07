import React, { useState } from 'react';
import { ArrowLeft, Upload, FileJson, CheckCircle, AlertCircle, FileStack } from 'lucide-react';
import { Link, useNavigate } from 'react-router-dom';

const AddAccount = () => {
    const navigate = useNavigate();
    const [dragging, setDragging] = useState(false);
    const [loading, setLoading] = useState(false);

    const [results, setResults] = useState(null); // { total: 0, success: 0, failed: 0, errors: [] }

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

        let successCount = 0;
        let failCount = 0;
        let errors = [];

        for (const file of files) {
            try {
                const text = await readFileAsText(file);
                const json = JSON.parse(text);
                await importAccount(json);
                successCount++;
            } catch (err) {
                failCount++;
                errors.push(`${file.name}: ${err.message}`);
            }
        }

        setLoading(false);
        setResults({
            total: files.length,
            success: successCount,
            failed: failCount,
            errors: errors
        });
    };

    const readFileAsText = (file) => {
        return new Promise((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = (e) => resolve(e.target.result);
            reader.onerror = (e) => reject(new Error('Failed to read file'));
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

    const handleDrop = (e) => {
        e.preventDefault();
        setDragging(false);

        const files = Array.from(e.dataTransfer.files);
        if (files.length > 0) {
            processFiles(files);
        }
    };

    const handleFileSelect = (e) => {
        const files = Array.from(e.target.files);
        if (files.length > 0) {
            processFiles(files);
        }
    };

    return (
        <div className="min-h-screen bg-slate-950 text-white p-8 flex flex-col items-center justify-center">
            <div className="w-full max-w-lg">
                <Link to="/" className="flex items-center gap-2 text-slate-400 hover:text-white mb-8 transition-colors">
                    <ArrowLeft size={20} />
                    Back to Dashboard
                </Link>

                <div className="glass-panel rounded-2xl p-8">
                    <h2 className="text-2xl font-bold mb-2 text-center">Import Steam Guard Files</h2>
                    <p className="text-slate-400 text-center mb-8 text-sm">
                        Upload one or multiple <code>.maFile</code>s to import your accounts.
                        <br />
                        <span className="text-blue-400">They will be encrypted securely on the server.</span>
                    </p>

                    {results && (
                        <div className={`mb-6 p-4 rounded-lg border ${results.failed === 0 ? 'bg-green-500/10 border-green-500/50' : 'bg-yellow-500/10 border-yellow-500/50'}`}>
                            <div className="flex items-center gap-2 font-bold mb-2">
                                {results.failed === 0 ? <CheckCircle className="text-green-500" size={20} /> : <AlertCircle className="text-yellow-500" size={20} />}
                                <span>Processed {results.total} files</span>
                            </div>
                            <div className="text-sm space-y-1 ml-7">
                                <p className="text-green-400">Successfully Imported: {results.success}</p>
                                {results.failed > 0 && (
                                    <p className="text-red-400">Failed: {results.failed}</p>
                                )}
                            </div>

                            {results.errors.length > 0 && (
                                <div className="mt-3 text-xs text-red-400 bg-red-950/30 p-2 rounded max-h-32 overflow-y-auto">
                                    {results.errors.map((err, i) => (
                                        <div key={i} className="mb-1">{err}</div>
                                    ))}
                                </div>
                            )}

                            <button
                                onClick={() => navigate('/')}
                                className="mt-4 w-full bg-slate-700 hover:bg-slate-600 text-white font-bold py-2 rounded transition-colors text-sm"
                            >
                                Return to Dashboard
                            </button>
                        </div>
                    )}

                    {!results && (
                        <div
                            onDragOver={handleDragOver}
                            onDragLeave={handleDragLeave}
                            onDrop={handleDrop}
                            className={`
                                border-2 border-dashed rounded-xl p-10 text-center transition-all cursor-pointer relative overflow-hidden
                                ${dragging
                                    ? 'border-blue-500 bg-blue-500/10'
                                    : 'border-slate-700 hover:border-slate-500 bg-slate-950'}
                            `}
                        >
                            <input
                                type="file"
                                accept=".maFile,.json"
                                multiple
                                onChange={handleFileSelect}
                                className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                            />

                            {loading ? (
                                <div className="flex flex-col items-center animate-pulse">
                                    <Upload size={48} className="text-blue-500 mb-4" />
                                    <span className="text-lg font-medium">Processing Files...</span>
                                </div>
                            ) : (
                                <div className="flex flex-col items-center">
                                    <FileStack size={48} className={`mb-4 ${dragging ? 'text-blue-400' : 'text-slate-500'}`} />
                                    <span className="text-lg font-medium mb-2">
                                        {dragging ? 'Drop files here' : 'Click or drop files here'}
                                    </span>
                                    <span className="text-sm text-slate-500">
                                        Accepts multiple .maFile or .json
                                    </span>
                                </div>
                            )}
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default AddAccount;
