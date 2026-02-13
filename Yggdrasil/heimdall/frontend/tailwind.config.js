/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        extend: {
            colors: {
                'asgard-gold': '#FFD700',
                'bifrost-cyan': '#00fa9a',
                'bifrost-purple': '#9370db',
                'odin-dark': '#050505',
                'odin-blue': '#0f172a',
                'frost-white': '#e2e8f0',
                'valhalla-burgundy': '#4a040b',
                'mythic-amber': '#b45309',
            }
        },
    },
    plugins: [],
}
