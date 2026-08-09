(() => {
  try {
    const savedTheme = localStorage.getItem("nori-motorlab-theme");
    if (savedTheme === "light" || savedTheme === "dark") {
      document.documentElement.dataset.theme = savedTheme;
      document.querySelector('meta[name="theme-color"]').content =
        savedTheme === "dark" ? "#0b0e11" : "#fbfaf5";
    }
  } catch (_) {}
})();
