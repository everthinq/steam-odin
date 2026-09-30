import { Component } from 'react';
import { Link } from 'react-router-dom';
import { AlertTriangle, Home, RotateCw } from 'lucide-react';

// A failed code-split chunk (a stale tab after a rebuild, the dev server
// restarting) rejects the lazy import with one of these messages. Reloading the
// page fetches the fresh chunk names, so the fallback says so.
function isChunkLoadError(error) {
    const message = String(error?.message || error || '');
    return /Failed to fetch dynamically imported module|Importing a module script failed|error loading dynamically imported module|Loading chunk .* failed/i.test(message);
}

// Catches a render error (or a failed lazy page import) anywhere below it, so
// one broken tool page shows a recoverable message instead of a blank screen.
// App.jsx keys it by the current path, so navigating away resets it.
class ErrorBoundary extends Component {
    constructor(props) {
        super(props);
        this.state = { error: null };
    }

    static getDerivedStateFromError(error) {
        return { error };
    }

    componentDidCatch(error, errorInformation) {
        console.error('Page crashed:', error, errorInformation?.componentStack);
    }

    render() {
        const { error } = this.state;
        if (!error) return this.props.children;

        const chunkFailed = isChunkLoadError(error);
        return (
            <div className="min-h-screen flex items-center justify-center p-6">
                <div className="max-w-lg w-full bg-slate-900/90 border border-red-500/40 rounded-xl p-6 shadow-xl">
                    <div className="flex items-center gap-3 mb-3 text-red-300">
                        <AlertTriangle className="w-6 h-6 shrink-0" />
                        <h2 className="text-lg font-semibold">
                            {chunkFailed ? 'This page could not be loaded' : 'Something went wrong on this page'}
                        </h2>
                    </div>
                    <p className="text-sm text-slate-300 mb-2">
                        {chunkFailed
                            ? 'The page code failed to download — usually the app was rebuilt while this tab was open. Reloading fetches the new version.'
                            : 'The page hit an unexpected error while rendering. Your data is untouched.'}
                    </p>
                    <pre className="text-xs text-red-200 bg-black/40 rounded-md p-3 mb-5 whitespace-pre-wrap break-words max-h-48 overflow-auto">
                        {String(error?.message || error)}
                    </pre>
                    <div className="flex flex-wrap gap-3">
                        <Link
                            to="/"
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-slate-700 hover:bg-slate-600 text-sm text-white transition-colors"
                        >
                            <Home className="w-4 h-4" /> Back to Dashboard
                        </Link>
                        <button
                            type="button"
                            onClick={() => window.location.reload()}
                            className="inline-flex items-center gap-2 px-4 py-2 rounded-lg bg-blue-600 hover:bg-blue-500 text-sm text-white transition-colors"
                        >
                            <RotateCw className="w-4 h-4" /> Reload
                        </button>
                    </div>
                </div>
            </div>
        );
    }
}

export default ErrorBoundary;
