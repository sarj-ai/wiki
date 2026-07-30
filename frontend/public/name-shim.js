window.__name = function (t, n) {
  try {
    Object.defineProperty(t, "name", { value: n, configurable: true });
  } catch (e) {}
  return t;
};
