// "remove" next to a saved secret (a token, a webhook): the backend never sends a secret back,
// only whether it is set. A blank field keeps it; this switch makes the next Save send null,
// which deletes it (settings.py secret_update).
const SecretRemove = ({ removing, onToggle }) => (
    <button type="button" onClick={onToggle}
        className={`ml-2 text-[10px] ${removing ? 'text-red-400' : 'text-slate-500 hover:text-red-400'}`}>
        {removing ? 'will be removed — undo' : 'remove'}
    </button>
);

export default SecretRemove;
