import React from 'react';
import { Minus, Plus } from 'lucide-react';

const NorseStepper = ({ value, onChange, min = 1, max = 3600, step = 10, label = "Interval" }) => {

    const increment = () => {
        if (value + step <= max) onChange(value + step);
    };

    const decrement = () => {
        if (value - step >= min) onChange(value - step);
    };

    return (
        <div className="flex flex-col gap-2">
            <label className="text-slate-400 text-sm font-medium tracking-wide">{label}</label>
            <div className="flex items-center gap-4 bg-odin-blue/50 p-2 rounded-lg border border-white/10 shadow-inner">
                <button
                    type="button"
                    onClick={decrement}
                    disabled={value <= min}
                    className="p-2 rounded-md bg-odin-blue hover:bg-odin-blue/80 text-frost-white/60 hover:text-frost-white disabled:opacity-30 disabled:cursor-not-allowed transition-all active:scale-95 border border-white/5"
                >
                    <Minus size={16} />
                </button>

                <div className="flex-1 text-center font-mono text-xl text-asgard-gold font-bold tracking-wider relative group">
                    <input
                        type="number"
                        value={value}
                        onChange={(e) => {
                            const val = parseInt(e.target.value);
                            if (!isNaN(val)) onChange(val);
                        }}
                        onBlur={() => {
                            if (value < min) onChange(min);
                            if (value > max) onChange(max);
                        }}
                        className="w-full bg-transparent text-center focus:outline-none appearance-none [&::-webkit-inner-spin-button]:appearance-none drop-shadow-md text-asgard-gold"
                    />
                    <span className="text-xs text-frost-white/40 ml-1 absolute right-2 top-1/2 -translate-y-1/2 pointer-events-none">s</span>
                    <div className="absolute inset-0 bg-asgard-gold/5 blur-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none" />
                </div>

                <button
                    type="button"
                    onClick={increment}
                    disabled={value >= max}
                    className="p-2 rounded-md bg-odin-blue hover:bg-odin-blue/80 text-frost-white/60 hover:text-frost-white disabled:opacity-30 disabled:cursor-not-allowed transition-all active:scale-95 border border-white/5"
                >
                    <Plus size={16} />
                </button>
            </div>
        </div>
    );
};

export default NorseStepper;
