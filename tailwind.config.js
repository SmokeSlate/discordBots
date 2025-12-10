/** @type {import('tailwindcss').Config} */
module.exports = {
    content: [
        "./docs/**/*.{html,js}",
        "./SmokeBot/**/*.{html,js}",
    ],
    theme: {
        extend: {
            fontFamily: {
                retro: ["Press Start 2P", "Courier New", "monospace"],
            },
        },
    },
    plugins: [],
};
