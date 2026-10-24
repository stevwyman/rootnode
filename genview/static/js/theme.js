(() => {
  "use strict";

  const STORAGE_KEY = "rootnode-color-theme";

  const getStoredTheme = () => localStorage.getItem(STORAGE_KEY);

  const setStoredTheme = (theme) => localStorage.setItem(STORAGE_KEY, theme);

  const getPreferredTheme = () => getStoredTheme() || "auto";

  const resolveTheme = (theme) => {
    if (theme === "auto") {
      return window.matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
    }
    return theme;
  };

  const setTheme = (theme) => {
    document.documentElement.setAttribute("data-bs-theme", resolveTheme(theme));
  };

  setTheme(getPreferredTheme());

  const showActiveTheme = (theme, focus = false) => {
    const themeSwitcher = document.querySelector("#bd-theme");
    if (!themeSwitcher) {
      return;
    }

    const themeSwitcherText = document.querySelector("#bd-theme-text");
    const activeThemeIcon = document.querySelector(".theme-icon-active use");
    const btnToActive = document.querySelector(`[data-bs-theme-value="${theme}"]`);
    if (!btnToActive) {
      return;
    }

    const svgOfActiveBtn = btnToActive.querySelector("svg use")?.getAttribute("href");

    document.querySelectorAll("[data-bs-theme-value]").forEach((element) => {
      element.classList.remove("active");
      element.setAttribute("aria-pressed", "false");
      element.querySelector(".theme-check")?.classList.add("d-none");
    });

    btnToActive.classList.add("active");
    btnToActive.setAttribute("aria-pressed", "true");
    btnToActive.querySelector(".theme-check")?.classList.remove("d-none");

    if (activeThemeIcon && svgOfActiveBtn) {
      activeThemeIcon.setAttribute("href", svgOfActiveBtn);
    }

    if (themeSwitcherText) {
      themeSwitcher.setAttribute(
        "aria-label",
        `${themeSwitcherText.textContent} (${btnToActive.dataset.bsThemeValue})`
      );
    }

    if (focus) {
      themeSwitcher.focus();
    }
  };

  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      if (getPreferredTheme() === "auto") {
        setTheme("auto");
      }
    });

  document.addEventListener("click", (event) => {
    const toggle = event.target.closest("[data-bs-theme-value]");
    if (!toggle) {
      return;
    }
    const theme = toggle.getAttribute("data-bs-theme-value");
    setStoredTheme(theme);
    setTheme(theme);
    showActiveTheme(theme, true);
  });

  const init = () => showActiveTheme(getPreferredTheme());
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
