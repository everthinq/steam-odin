import { useState } from 'react';
import { Package } from 'lucide-react';
import { getItemImageUrl } from '../../utils/ratatoskrImages';

// Item image thumbnail with a package-icon fallback on missing/broken art. From Transfer.jsx.
const ItemThumb = ({ item }) => {
    const [failed, setFailed] = useState(false);
    const src = getItemImageUrl(item);

    if (!src || failed) {
        return (
            <div className="w-9 h-9 shrink-0 rounded bg-black/30 flex items-center justify-center">
                <Package size={14} className="text-slate-600" />
            </div>
        );
    }

    return (
        <img
            src={src}
            alt=""
            className="w-9 h-9 object-contain shrink-0 rounded bg-black/20"
            loading="lazy"
            referrerPolicy="no-referrer"
            onError={() => setFailed(true)}
        />
    );
};

export default ItemThumb;
