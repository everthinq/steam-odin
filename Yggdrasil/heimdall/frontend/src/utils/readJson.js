// "/api/… (HTTP 502)": which request answered, for the error a page shows.
const label = (response) => `${new URL(response.url, window.location.origin).pathname} (HTTP ${response.status})`;

// Read a backend answer as JSON. An empty or non-JSON body (the backend restarted or crashed in
// the middle of the request, a proxy error page) becomes an error that names the request and
// says what happened, instead of the browser's bare "Unexpected end of JSON input".
export const readJson = async (response) => {
    const text = await response.text();
    if (!text.trim()) {
        throw new Error(`${label(response)} answered with an empty body: the backend most likely restarted or stopped in the middle of the request. If it started something (a purchase, a transfer), check its log before repeating it; otherwise try again in a few seconds.`);
    }
    try {
        return JSON.parse(text);
    } catch {
        const start = text.replace(/\s+/g, ' ').trim().slice(0, 120);
        throw new Error(`${label(response)} answered with something that is not JSON: "${start}${text.length > 120 ? '…' : ''}"`);
    }
};

// readJson for a status read whose error answer carries nothing to show: a non-OK answer becomes
// its {error} message, so a failed poll keeps the page's last good state instead of replacing it.
export const readJsonOrThrow = async (response) => {
    const body = await readJson(response);
    if (!response.ok) throw new Error(body?.error || `${label(response)} failed.`);
    return body;
};

// The browser's own messages when a request never reached the server (Chrome, Safari, Firefox).
const NETWORK_FAILURE = /Failed to fetch|Load failed|NetworkError/;

// The message for a request that failed. Only the browser's own fetch failure means the backend
// was not reached; readJson's errors, an answer's {error} and anything else say what happened.
export const failureMessage = (error) => (error instanceof TypeError && NETWORK_FAILURE.test(error.message)
    ? `Could not reach the backend (${error.message}).`
    : error.message);
