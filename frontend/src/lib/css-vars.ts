type CssVarName = `--${string}`;

/**
 * Ref callback that writes CSS custom properties onto the element, for
 * dynamic values referenced by arbitrary-value utility classes
 * (for example `pl-(--row-pad)`), keeping `style` props out of JSX.
 */
export function cssVars(vars: Record<CssVarName, string>): (el: HTMLElement | null) => void {
  return (el) => {
    if (!el) return;
    for (const [name, value] of Object.entries(vars)) {
      el.style.setProperty(name, value);
    }
  };
}
