document.addEventListener('DOMContentLoaded', () => {
    const mobileMenu = document.getElementById('mobile-menu');
    const menuButton = document.getElementById('mobile-menu-button');

    if (menuButton && mobileMenu) {
        menuButton.addEventListener('click', (event) => {
            event.stopPropagation();
            mobileMenu.classList.toggle('hidden');
        });

        document.addEventListener('click', (event) => {
            if (!mobileMenu.contains(event.target) && !menuButton.contains(event.target)) {
                mobileMenu.classList.add('hidden');
            }
        });
    }

    const anchorLinks = document.querySelectorAll('a[href^="#"]');
    anchorLinks.forEach((link) => {
        link.addEventListener('click', (event) => {
            const targetId = link.getAttribute('href') || '';
            const target = document.querySelector(targetId);
            if (target) {
                event.preventDefault();
                target.scrollIntoView({ behavior: 'smooth', block: 'start' });
                mobileMenu?.classList.add('hidden');
            }
        });
    });
});
